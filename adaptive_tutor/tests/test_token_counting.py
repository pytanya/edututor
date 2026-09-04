import pytest

from src.llm.base import TokenUsage
from src.llm.openrouter import OpenRouterTokenCounter
from src.llm.router_ai import RouterAITokenCounter


def _routerai_real_raw():
    """Фактическая структура usage из реального ответа RouterAI (2026-09-03,
    модель qwen/qwen-2.5-7b-instruct).

    Токены лежат ПЛОСКИМИ полями usage.prompt_tokens / usage.completion_tokens /
    usage.total_tokens; вложенные prompt_tokens_details / completion_tokens_details
    содержат только разбивку (audio/cache/reasoning). Стоимость — в usage.cost (RUB).
    """
    return {
        "usage": {
            "completion_tokens": 6,
            "prompt_tokens": 47,
            "total_tokens": 53,
            "completion_tokens_details": {
                "accepted_prediction_tokens": None,
                "audio_tokens": 0,
                "reasoning_tokens": 0,
                "rejected_prediction_tokens": None,
                "image_tokens": 0,
            },
            "prompt_tokens_details": {
                "audio_tokens": 0,
                "cache_write_tokens": 0,
                "cached_tokens": 0,
                "video_tokens": 0,
            },
            "cost": 0.0006672616210000001,
        }
    }


def _openai_usage_model():
    """CompletionUsage из openai SDK; в окружении без openai SDK-тесты
    пропускаются (юнит-тесты не ходят в сеть)."""
    try:
        from openai.types.completion_usage import CompletionUsage
        return CompletionUsage
    except Exception:  # noqa: BLE001 - openai не установлен
        pytest.skip("openai SDK не установлен — SDK-ветка не тестируется")


def _sdk_response(usage_raw: dict):
    """Имитация объекта ответа OpenAI SDK; поле cost остаётся в extras модели,
    как в реальном RouterAI-ответе."""
    class _Resp:  # pragma: no cover - простой контейнер
        pass

    resp = _Resp()
    resp.usage = _openai_usage_model()(**usage_raw)
    return resp


def _routerai_raw():
    """Raw RouterAI usage: cost в рублях в поле usage.cost."""
    return {
        "usage": {
            "prompt_tokens": 1000,
            "completion_tokens": 100,
            "total_tokens": 1100,
            "cost": 2.5,  # RUB
        }
    }


def _openrouter_raw():
    """Raw OpenRouter usage: cost в credits в поле usage.cost, вложенные details."""
    return {
        "usage": {
            "prompt_tokens": 500,
            "completion_tokens": 50,
            "total_tokens": 550,
            "cost": 0.01,  # credits ≈ USD
            "completion_tokens_details": {"reasoning_tokens": 10},
        }
    }


def test_routerai_parses_usage_from_rub():
    counter = RouterAITokenCounter()
    usage = counter.parse_usage(_routerai_raw())
    assert isinstance(usage, TokenUsage)
    assert usage.prompt_tokens == 1000
    assert usage.completion_tokens == 100
    assert usage.total_tokens == 1100
    assert usage.cost_local == 2.5
    # курс 90 RUB -> USD
    assert usage.cost_usd == pytest.approx(2.5 / 90.0, rel=1e-4)


def test_openrouter_parses_usage_from_credits():
    counter = OpenRouterTokenCounter()
    usage = counter.parse_usage(_openrouter_raw())
    assert usage.prompt_tokens == 500
    assert usage.completion_tokens == 50
    assert usage.total_tokens == 550
    assert usage.cost_local == 0.01  # credits
    assert usage.cost_usd == 0.01  # credits ≈ USD


def test_token_counter_handles_partial_usage():
    # Отсутствуют total_tokens — вычисляется из суммы
    counter = RouterAITokenCounter()
    raw = {"usage": {"prompt_tokens": 7, "completion_tokens": 3}}
    usage = counter.parse_usage(raw)
    assert usage.total_tokens == 10


def test_token_counter_estimates_for_limits():
    assert RouterAITokenCounter().estimate("а" * 100) == 25
    assert OpenRouterTokenCounter().estimate("a" * 100) == 25


def test_routerai_parses_usage_from_real_response_dict():
    # Реальный формат RouterAI: плоские токены + вложенные details (разбивка).
    counter = RouterAITokenCounter()
    usage = counter.parse_usage(_routerai_real_raw())
    assert usage.prompt_tokens == 47
    assert usage.completion_tokens == 6
    assert usage.total_tokens == 53
    assert usage.cost_local == pytest.approx(0.0006672616210000001)
    # parse_usage округляет cost_usd = round(cost_local / 90.0, 6) -> 7e-06
    assert usage.cost_usd == round(0.0006672616210000001 / 90.0, 6)


def test_routerai_parses_usage_from_real_sdk_response():
    # Реальный путь приложения: SDK-объект, где cost лежит в extras модели.
    counter = RouterAITokenCounter()
    usage = counter.parse_usage(_sdk_response(_routerai_real_raw()["usage"]))
    assert usage.prompt_tokens == 47
    assert usage.completion_tokens == 6
    assert usage.total_tokens == 53
    assert usage.cost_local == pytest.approx(0.0006672616210000001)
    assert usage.cost_usd == round(0.0006672616210000001 / 90.0, 6)


def test_openrouter_parses_usage_from_real_sdk_response():
    # OpenRouter тоже ходит через AsyncOpenAI; тот же usage-формат должен
    # корректно нормализоваться (cost в extras интерпретируется как credits≈USD).
    counter = OpenRouterTokenCounter()
    usage = counter.parse_usage(_sdk_response(_routerai_real_raw()["usage"]))
    assert usage.prompt_tokens == 47
    assert usage.completion_tokens == 6
    assert usage.total_tokens == 53
    assert usage.cost_local == pytest.approx(0.0006672616210000001)  # credits
    assert usage.cost_usd == pytest.approx(0.0006672616210000001)  # credits ≈ USD


def test_routerai_reasoning_tokens_not_double_counted():
    # Если RouterAI вернёт reasoning-токены (например для deepseek-r1), они уже
    # включены в completion_tokens; суммировать их НЕ нужно.
    counter = RouterAITokenCounter()
    raw = {
        "usage": {
            "prompt_tokens": 20,
            "completion_tokens": 30,  # уже включает reasoning
            "total_tokens": 50,
            "cost": 1.0,
            "completion_tokens_details": {"reasoning_tokens": 12},
        }
    }
    usage = counter.parse_usage(raw)
    assert usage.prompt_tokens == 20
    assert usage.completion_tokens == 30
    assert usage.total_tokens == 50