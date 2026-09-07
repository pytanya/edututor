"""FastAPI-слой: /chat, /chat/stream (SSE), история сессий, /knowledge, /health.

Многоходовость: per-сессионное in-memory хранилище (SessionStore) держит историю
разговора по session_id. RAG-движок на app.state наполняется сниппетами веб-поиска
по теме (subject/grade/topic) через provisioning — как source-finder в референсе
project_work. SSE стримит безопасные события агента
(agent.step / agent.tool / agent.finalize / message / done) плюс heartbeat.

На каждый запрос собирается свежий AgentRuntime из фабрики — так mutable-состояние
(счётчики шагов, бюджет, трейс) не пересекается между параллельными сессиями.
LLM-клиенты кешируются внутри LLMClientFactory и безопасно переиспользуются.
"""

import asyncio
import contextlib
import json
import re
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from ..agent.critic import Critic
from ..agent.envelope import parse_content_envelopes, sanitize_envelope
from ..agent.loop import AgentRuntime, run_agent
from ..agent.quiz_guard import (
    QUIZ_BLOCKED_NOTE,
    QUIZ_REJECT_CAP,
    build_regen_instruction,
    next_reject_state,
    quiz_problems,
    quiz_reject_text,
)
from ..agent.tools import ToolContext, generate_quiz_card
from ..config import settings
from ..export import csv_exporter
from ..export import okf as okf_export
from ..kg.build import (
    _root_key,
    build_or_load_graph,
    build_topic_scaffold,
    collect_snippets,
    sync_relations_from_graph,
)
from ..kg.cache import graph_cache_key, load_cached_graph
from ..kg.graph import KnowledgeGraph
from ..llm.base import LLMClientFactory
from ..models.schemas import AgentGraphState, ContentEnvelope
from ..observability.logger import JsonlLogger
from ..rag import DocChunk, RAGEngine
from ..rag.provisioning import provision_topic
from ..review.grader import grade_answer
from ..safety import BudgetGuard, CircuitBreaker
from ..student.adaptive import overall_level, pick_recommendation
from ..student.answer_record import build_answer_record
from ..student.store import StudentStore
from ..student.student_kg import knowledge_graph_payload, row_payload
from ..wiki import enrich as wiki_enrich
from ..wiki.models import WikiArticle
from ..wiki.store import KnowledgeWiki
from ..wiki.store import slug as _slug
from .session_store import ChatSession, SessionStore

FALLBACK_REPLY = "..."
RuntimeFactory = Callable[..., AgentRuntime]
Provisioner = Callable[..., Awaitable[int]]
_HEARTBEAT_SEC = 15.0
_PROVISION_TIMEOUT_SEC = 60.0


class StudentProfile(BaseModel):
    """Адаптивные параметры ученика (опционально)."""

    current_knowledge_level: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Уровень знаний (0..1)"
    )
    learning_style: str | None = Field(
        default=None, description="Стиль обучения: visual/auditory/kinesthetic/reading"
    )
    fatigue_level: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Уровень усталости (0..1)"
    )


class IntakeProfile(BaseModel):
    """Тело POST /student/{student_id}/profile (карточка знакомства)."""

    name: str = Field(default="", max_length=200, description="ФИО (имя и фамилия)")
    learner_type: str = Field(default="", description="student|schoolchild")
    grade: str = Field(default="", max_length=50, description="Класс (если школьник)")

    @field_validator("name")
    @classmethod
    def _name_two_words_if_present(cls, value: str) -> str:
        """Непустое имя должно содержать минимум два слова (иначе 422)."""
        value = value.strip()
        if value and len(value.split()) < 2:
            raise ValueError("Укажи имя и фамилию (минимум два слова)")
        return value

    @field_validator("learner_type")
    @classmethod
    def _learner_type_in_list(cls, value: str) -> str:
        """learner_type строго из пусто|student|schoolchild (иначе 422)."""
        value = value.strip().lower()
        if value not in {"", "student", "schoolchild"}:
            raise ValueError("learner_type должен быть student или schoolchild")
        return value


class ChatRequest(BaseModel):
    """Тело запроса POST /chat и POST /chat/stream."""

    message: str = Field(description="Сообщение ученика")
    session_id: str = Field(default="", description="Идентификатор диалога")
    student_id: str = Field(default="", description="Идентификатор ученика (опц.)")
    student_profile: StudentProfile = Field(
        default_factory=StudentProfile, description="Адаптивный профиль ученика"
    )
    topic: str = Field(default="", description="Тема (для провижининга RAG)")
    subject: str = Field(default="", description="Предмет (для провижининга RAG)")
    grade: str = Field(default="", description="Класс (для провижининга RAG)")
    kind: Literal["message", "hint_request", "review_request"] = Field(
        default="message", description="hint_request — кнопка «Подсказка»; "
        "review_request — старт блица повторений"
    )

    @model_validator(mode="after")
    def _message_not_blank(self) -> "ChatRequest":
        """Отклоняет пустые/whitespace-сообщения (422), кроме служебных kinds.

        review_request приходит с пустым message (карточки шлёт сервер);
        hint_request — кнопка «Подсказка», сообщение подставляется сервером
        (HINT_SERVICE_TEXT). Для них проверку пропускаем и не триммим пустоту.
        """
        if self.kind not in {"review_request", "hint_request"} and not (self.message or "").strip():
            raise ValueError("message не должен быть пустым")
        self.message = self.message.strip()
        return self


class KnowledgeProvisionRequest(BaseModel):
    """Тело запроса POST /knowledge/provision."""

    topic: str = Field(min_length=1, description="Тема для поиска материалов")
    subject: str = Field(default="", description="Предмет")
    grade: str = Field(default="", description="Класс")

    @field_validator("topic")
    @classmethod
    def _topic_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("topic не должен быть пустым")
        return value.strip()


class WikiEnrichRequest(BaseModel):
    """Тело POST /student/{student_id}/wiki/enrich."""

    subject: str = Field(default="", max_length=200, description="Предмет")
    topic: str = Field(min_length=1, max_length=300, description="Тема")

    @field_validator("topic")
    @classmethod
    def _topic_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("topic не должен быть пустым")
        return value.strip()


class ChatResponse(BaseModel):
    """Ответ фронтенду: стабильный контракт без внутренностей состояния графа."""

    reply: str = Field(description="Текст ответа репетитора")
    envelope: dict | None = Field(default=None, description="Конверт типизированного контента")
    adaptive: dict = Field(default_factory=dict, description="Адаптивный блок для панели")
    error: str | None = Field(default=None, description="Текст ошибки, если была")
    trace_id: str = Field(default="", description="Идентификатор трассировки")
    session_id: str = Field(default="", description="Идентификатор диалога")
    difficulty: str = Field(default="medium", description="Сложность ответа")
    steps: int = Field(default=0, description="Кол-во шагов агента")
    terminated: bool = Field(default=False, description="Завершён ли цикл агента")


def build_runtime(rag: object | None = None) -> AgentRuntime:
    """Собирает AgentRuntime с боевыми зависимостями.

    rag — точка расширения для подключения RAG-движка (по умолчанию None;
    приложение подставит общий движок после сборки рантайма).
    """
    llm = LLMClientFactory.get_client(settings.region)
    models = LLMClientFactory.get_models_for_region(settings.region)
    tool_context = ToolContext(
        rag=rag,
        region=settings.region.value,
        llm=llm,
        model=models.get("fast") or models.get("planner", ""),
    )
    return AgentRuntime(
        llm=llm,
        models=models,
        tool_context=tool_context,
        circuit_breaker=CircuitBreaker(),
        budget=BudgetGuard(),
        logger=JsonlLogger(settings.log_file),
        critic=Critic(llm=llm, model=models["judge"]),
    )


def _to_response(
    state: AgentGraphState,
    trace_id: str,
    session_id: str,
    envelope: ContentEnvelope | None,
    adaptive: dict,
) -> ChatResponse:
    """Маппит состояние в стабильный ответ + конверт + adaptive."""
    reply = state.final_answer or (envelope.text if envelope else "")
    error = state.error
    if not reply:
        reply = FALLBACK_REPLY
        error = error or "агент не сформировал ответ"
    return ChatResponse(
        reply=reply,
        envelope=(
            sanitize_envelope(envelope).model_dump() if envelope is not None else None
        ),
        adaptive=adaptive,
        error=error,
        trace_id=trace_id,
        session_id=session_id,
        difficulty=state.difficulty,
        steps=len(state.steps),
        terminated=bool(state.terminated),
    )


def _quiz_secret(envelope: ContentEnvelope) -> dict | None:
    """Секрет квиза для answer record (None для не-quiz / review-карточек)."""
    if envelope is None or envelope.type.value != "quiz":
        return None
    if (envelope.payload or {}).get("review"):
        return None
    return {
        "question": envelope.text,
        "options": (envelope.payload or {}).get("options"),
        "answer_type": (envelope.payload or {}).get("answer_type", "open"),
        "difficulty": envelope.difficulty,
        "correct_answer": (envelope.payload or {}).get("_correct_answer") or "",
    }


def _guard_quiz_envelopes(
    app: FastAPI,
    trace_id: str,
    envelopes: list[ContentEnvelope],
    fallback_text: str,
    streak: int | None = None,
) -> tuple[list[ContentEnvelope], list[dict]]:
    """Серверный контроль quiz-конвертов перед выдачей ученику.

    Квиз, в котором раскрыт ответ (или битая структура — дубли вариантов,
    эталон не из options и т.п.), заменяется нейтральным theory-сообщением с
    текстом ``fallback_text`` (выбирает вызывающий по состоянию сессии). Ученик
    не получает заведомо некачественный/негрейдуемый вопрос, а secret
    ``last_quiz`` для него не фиксируется.

    Возвращает кортеж (чистый список, список отклонений). Элемент отклонения:
    {"reasons": [...], "text": env.text[:160], "answer_type": ...}. Причины
    пишутся в JSONL ``quiz.reject`` (с числом неудач подряд ``streak``).
    """
    out: list[ContentEnvelope] = []
    rejected: list[dict] = []
    for env in envelopes:
        if env.type.value != "quiz":
            out.append(env)
            continue
        problems = quiz_problems(env)
        if not problems:
            out.append(env)
            continue
        JsonlLogger(settings.log_file).log(
            trace_id, "INFO", "quiz.reject",
            reasons=problems,
            text=(env.text or "")[:160],
            answer_type=(env.payload or {}).get("answer_type"),
            streak=streak,
        )
        rejected.append(
            {
                "reasons": list(problems),
                "text": (env.text or "")[:160],
                "answer_type": (env.payload or {}).get("answer_type"),
            }
        )
        out.append(ContentEnvelope(type="theory", text=fallback_text))
    return out, rejected


def _tool_quiz_card(state: AgentGraphState) -> dict | None:
    """Карточка квиза из результата инструмента generate_quiz (если был вызван).

    ``state.tools_result`` хранит сериализованный вывод последнего вызова
    инструмента вида {"status": "ok", "data": {question, options, ...}}.
    """
    raw = (state.tools_result or {}).get("generate_quiz")
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if parsed.get("status") != "ok":
        return None
    data = parsed.get("data")
    return data if isinstance(data, dict) else None


def _quiz_envelope_from_card(card: dict) -> ContentEnvelope | None:
    """ContentEnvelope(quiz) из карточки generate_quiz (авторитетный источник).

    Карточка возвращается коротким отдельным LLM-вызовом со строгой схемой —
    её поля (в т.ч. _correct_answer) переносятся в payload без «пересказа»
    моделью, которая в финальном конверте может исказить формулировку вопроса.
    """
    question = str(card.get("question") or "").strip()
    if not question:
        return None
    answer_type = card.get("answer_type")
    if answer_type not in ("single", "open"):
        answer_type = "open"
    payload: dict[str, Any] = {"answer_type": answer_type}
    if answer_type == "single":
        options = card.get("options")
        payload["options"] = options if isinstance(options, list) else []
    payload["_correct_answer"] = str(card.get("_correct_answer") or "")
    difficulty = str(card.get("difficulty") or "medium").strip()
    if difficulty not in {"easy", "medium", "hard"}:
        difficulty = "medium"
    return ContentEnvelope(
        type="quiz", text=question, payload=payload, difficulty=difficulty
    )


async def _resolve_tool_quiz(
    state: AgentGraphState,
    runtime: AgentRuntime,
    trace_id: str,
    topic: str,
    envelopes: list[ContentEnvelope],
) -> list[ContentEnvelope]:
    """Quiz-конверт хода строится из карточки инструмента, а не из конверта модели.

    Когда агент в ходе вызывал ``generate_quiz``, карточка (короткий отдельный
    LLM-вызов со строгой схемой) авторитетна: она заменяет quiz-конверт, который
    модель могла собрать из неё с искажениями (вопрос-утверждение, раскрытый
    ответ). Если карточка сама не прошла ``quiz_problems`` — делаем одну тихую
    регенерацию тем же генератором карточки с причинами отклонения (только
    reasons, без текста/ответа). Секрет в промпт не попадает.
    """
    card = _tool_quiz_card(state)
    if not isinstance(card, dict):
        return envelopes
    env = _quiz_envelope_from_card(card)
    problems = quiz_problems(env) if env is not None else ["пустая карточка квиза"]
    if problems:
        try:
            card = await generate_quiz_card(
                runtime.tool_context,
                topic=str(topic or "").strip() or "пройденная тема",
                difficulty=str(card.get("difficulty") or "medium"),
                fix_hint="; ".join(problems),
            )
        except Exception as exc:  # noqa: BLE001 — fail-soft: оставляем исходный ход
            JsonlLogger(settings.log_file).log(
                trace_id, "WARNING", "quiz.gen", status="regen_error",
                error=str(exc)[:200],
            )
            return envelopes
        env = _quiz_envelope_from_card(card)
        if env is None:
            return envelopes
    replaced = [env if e.type.value == "quiz" else e for e in envelopes]
    if not any(e.type.value == "quiz" for e in envelopes):
        # Модель вызвала generate_quiz, но не оформила quiz-конверт — отдаём
        # карточку отдельным блоком (намерение «дай квиз» очевидно).
        replaced.append(env)
    return replaced


async def _regen_quiz(
    runtime: AgentRuntime,
    context: list[dict],
    rejected: list[dict],
    trace_id: str,
) -> list[ContentEnvelope] | None:
    """Одна тихая регенерация quiz после отклонения серверным guard.

    Один вызов planner (та же модель, без tools) с corrective-инструкцией по
    причинам отклонения. Возвращает распарсенные конверты повторного ответа
    либо None (LLM error / пусто / нераспознанный JSON) — fail-soft.
    Секрет отклонённого квиза (_correct_answer/options/text) в промпт не
    попадает: только строки reasons.
    """
    model = runtime.models.get("planner", "")
    instruction = build_regen_instruction(rejected)
    messages = [*context, {"role": "system", "content": instruction}]
    log = JsonlLogger(settings.log_file)
    try:
        resp = await runtime.llm.chat(
            messages=messages,
            model=model,
            temperature=0.4,
            max_tokens=settings.llm_max_tokens,
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft
        log.log(trace_id, "WARNING", "quiz.regen", status="llm_error",
                error=str(exc)[:300])
        return None
    if runtime.budget and resp.usage:
        runtime.budget.record(resp.cost_usd)
    reply = (resp.content or "").strip()
    if not reply:
        log.log(trace_id, "WARNING", "quiz.regen", status="empty")
        return None
    parsed = parse_content_envelopes(reply)
    if not parsed:
        log.log(trace_id, "WARNING", "quiz.regen", status="parse_error")
        return None
    return parsed


_HINT_SERVICE_TEXT = "[ученик просит подсказку к текущей задаче]"
_HINT_ANSWER_NOTE = (
    "Ученик просит подсказку к текущему заданию. Ответь ТОЛЬКО конвертом типа "
    "hint: дай направление к решению, НЕ раскрывай полное решение/эталон и НЕ "
    "создавай новых вопросов. Инструменты в этом ходе недоступны."
)
_MAX_HINTS_IN_A_ROW = 2


def _gen_id(prefix: str) -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _build_adaptive(
    store: StudentStore,
    student_id: str,
    topic: str,
    difficulty: str,
    next_from_model: str | None = None,
    subject: str = "",
) -> dict:
    """Собирает adaptive-блок для ответа фронтенду.

    subject — фильтр рекомендаций (Слой 2, §3.3): если пуст — без фильтра.
    """
    topics: list[dict[str, Any]] = []
    if store is not None:
        store.upsert_student(student_id)
        topics = store.list_topics(student_id)
    topic_level = (
        store.get_topic_level(student_id, topic) if store is not None and topic else 0.5
    )
    topic_payload = None
    if store is not None and topic:
        try:
            topic_payload = store.get_topic(student_id, topic)
        except Exception:  # noqa: BLE001 — сбой БД не должен ронять ответ
            topic_payload = None
    rec = None
    if store is not None:
        try:
            recs = store.recommend_topics(
                student_id, subject=subject, current_topic=topic, limit=1
            )
            rec = (
                recs[0]["topic"]
                if recs
                else pick_recommendation(topics, next_from_model=next_from_model)
            )
        except Exception:  # noqa: BLE001 — сбой БД не должен ронять ответ
            rec = pick_recommendation(topics, next_from_model=next_from_model)
    review_due = 0
    if store is not None:
        try:
            review_due = store.review_stats(student_id)["due"]
        except Exception:  # noqa: BLE001 — сбой БД не должен ронять ответ
            review_due = 0
    return {
        "student_id": student_id,
        "current_knowledge_level": overall_level(topics),
        "topic": topic,
        "topic_level": topic_level,
        "attempts": next((t["attempts"] for t in topics if t["topic"] == topic), 0),
        "correct": next((t["correct"] for t in topics if t["topic"] == topic), 0),
        "difficulty": difficulty,
        "recommended_next": rec,
        "topic_status": (topic_payload or {}).get("status"),
        "topic_mastery": (topic_payload or {}).get("mastery"),
        "topic_accuracy": (topic_payload or {}).get("accuracy"),
        "review_due": review_due,
    }


def _bandit_advice(features: list[float], bandit: dict) -> tuple[int, str]:
    """Рекомендация LinUCB: (индекс руки, текст совета для модели)."""
    from src.student.linucb import arm_difficulty, bandit_select

    arm = bandit_select(bandit, features, current_arm=1)
    return arm, (
        "[Адаптивный совет: для текущего контекста рекомендуемая сложность "
        f"задания — «{arm_difficulty(arm)}». Учитывай её при выборе сложности, "
        "но решение за тобой.]"
    )


def _make_provisioner(rag_engine: RAGEngine) -> Provisioner:
    """Фабрика провижинера: closure над общим RAG-движком приложения."""

    async def provisioner(topic: str, subject: str = "", grade: str = "") -> int:
        return await provision_topic(
            rag_engine, topic=topic, subject=subject, grade=grade, region=settings.region
        )

    return provisioner


def _sse(event: str, data: dict[str, Any]) -> str:
    """Форматирует один SSE-фрейм: event + JSON data."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# --- Knowledge Wiki (E4): хелперы статей, обогащения и наблюдаемости ---


def _wiki_for(student_id: str) -> KnowledgeWiki | None:
    """Wiki ученика (None если выключено)."""
    if not settings.wiki_enabled:
        return None
    return KnowledgeWiki(settings.resolved_knowledge_wiki_dir, student_id=student_id)


def _log_wiki(app: FastAPI, event: str, **fields: Any) -> None:
    """JSONL-события наблюдаемости (спека §8): wiki.updated/wiki.note."""
    with contextlib.suppress(Exception):
        app.state.jsonl_logger.log("", "INFO", event, **fields)


async def _schedule_enrich(
    app: FastAPI,
    student_id: str,
    body: ChatRequest,
    art: Any,
    wiki: KnowledgeWiki,
) -> None:
    """Ленивое обогащение тела: fire-and-forget, только при провижиненных
    материалах темы и наличии RAG. Ошибки глотаются."""
    if not settings.wiki_enrich_enabled or not body.topic:
        return
    if (art.body or "").strip():
        return
    rag = app.state.rag_engine
    key = "|".join((body.subject.strip(), body.grade.strip(), body.topic.strip()))
    if rag is None or key not in app.state.provisioned:
        return
    if key in app.state.wiki_enriching:
        return
    app.state.wiki_enriching.add(key)

    async def _run() -> None:
        try:
            llm = app.state.runtime_factory().llm
            models = LLMClientFactory.get_models_for_region(settings.region)
            context = _rag_context(app, body.topic, body.subject, body.grade)
            res = await wiki_enrich.enrich_body(
                wiki, art.subject, body.topic, context, llm,
                model=models.get("fast", "fast"),
            )
            if res is not None:
                _log_wiki(
                    app, "wiki.updated", student_id=student_id,
                    subject=art.subject, topic=body.topic,
                    mastery=res.get("mastery"),
                )
        except Exception:  # noqa: BLE001
            pass
        finally:
            app.state.wiki_enriching.discard(key)

    task = asyncio.create_task(_run())
    tasks: set = app.state._enrich_tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)


def _rag_context(
    app: FastAPI, topic: str, subject: str = "", grade: str = "", k: int = 3
) -> list[str]:
    """Фрагменты по теме из RAG (fail-soft)."""
    rag = app.state.rag_engine
    if rag is None:
        return []
    filters: dict[str, str] = {}
    if subject:
        filters["subject"] = subject
    if grade:
        filters["grade"] = grade
    try:
        results = rag.search(query=topic, top_k=k, filters=filters or None)
        return [r.chunk.text for r in results]
    except Exception:
        return []


# --- Экспорт (E4): CSV журнала ответов и OKF-бандл графа источника ---


def _log_export(app: FastAPI, event: str, **fields: Any) -> None:
    """JSONL-события наблюдаемости (спека §8): export.csv / export.okf."""
    with contextlib.suppress(Exception):
        app.state.jsonl_logger.log("", "INFO", event, **fields)


def _safe_float(*values: Any) -> float | None:
    """Первое числовое значение из списка; иначе None (толерантное чтение)."""
    for value in values:
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _mastered_set(store: StudentStore | None, student_id: str) -> set[str]:
    """Имена освоенных тем ученика: явный статус mastered или порог 3×0.8."""
    out: set[str] = set()
    if store is None:
        return out
    try:
        rows = store.list_topics(student_id)
    except Exception:  # noqa: BLE001 — сбой БД не роняет экспорт
        return out
    for row in rows:
        topic = str(row.get("topic") or "").strip()
        if not topic:
            continue
        mastery = _safe_float(row.get("mastery"), row.get("level")) or 0.0
        attempts = int(row.get("attempts") or 0)
        if str(row.get("status") or "") == "mastered" or (
            attempts >= 3 and mastery >= 0.8
        ):
            out.add(topic)
    return out


def _mastery_by_title(store: StudentStore | None, student_id: str) -> dict[str, float]:
    """Мастерство по названиям тем (attempts > 0; mastery или level) — для OKF."""
    out: dict[str, float] = {}
    if store is None:
        return out
    try:
        rows = store.list_topics(student_id)
    except Exception:  # noqa: BLE001 — сбой БД не роняет экспорт
        return out
    for row in rows:
        if int(row.get("attempts") or 0) <= 0:
            continue
        topic = str(row.get("topic") or "").strip()
        mastery = _safe_float(row.get("mastery"), row.get("level"))
        if topic and mastery is not None:
            out[topic] = round(mastery, 4)
    return out


def _last_subject(store: StudentStore | None, student_id: str) -> str:
    """Предмет последней записи ученика; пусто → «общая тема»."""
    if store is not None:
        try:
            rows = store.list_records(student_id, limit=1)
            subject = (rows[0].get("subject") or "").strip() if rows else ""
            if subject:
                return subject
        except Exception:  # noqa: BLE001
            pass
    return "общая тема"


def _summary_rows(
    store: StudentStore,
    student_id: str,
    records: list[dict],
    mastered_topics: set[str],
) -> list[dict[str, Any]]:
    """Группировка записей по сессии -> одна строка SUMMARY_COLUMNS.

    started_at/ended_at берутся из таблицы sessions (точное время занятия),
    при отсутствии сессии — fallback на мин/макс меток ответов журнала.
    """
    by_session: dict[str, list[dict]] = {}
    for r in records:
        by_session.setdefault(r.get("session_id") or "", []).append(r)
    sessions_map: dict[str, dict[str, Any]] = {}
    if store is not None:
        for session_id in by_session:
            s = store.get_session(student_id, session_id)
            if s is not None:
                sessions_map[session_id] = s
    rows: list[dict[str, Any]] = []
    for session_id, rows_ in by_session.items():
        ts = [float(r.get("ts") or 0) for r in rows_]
        sess = sessions_map.get(session_id)
        total = len(rows_)
        correct = sum(1 for r in rows_ if r.get("correct"))
        topics = sorted({str(r.get("topic") or "") for r in rows_ if r.get("topic")})
        mastered = sorted(t for t in topics if t in mastered_topics)
        rows.append({
            "session_id": session_id,
            "subject": (rows_[0].get("subject") or (sess or {}).get("subject")) or "",
            "topic": " | ".join(topics),
            "started_at": (
                (sess or {}).get("started_at") if sess and sess.get("started_at")
                else (min(ts) if ts else None)
            ),
            "ended_at": (
                (sess or {}).get("ended_at") if sess and sess.get("ended_at")
                else (max(ts) if ts else None)
            ),
            "questions": total,
            "correct": correct,
            "accuracy": round(correct / total, 4) if total else 0.0,
            "mastered_topics": mastered,
        })
    return rows


def _question_rows(records: list[dict]) -> list[dict]:
    """Строки журнала для questions_csv: ts -> timestamp (ISO считает csv_exporter)."""
    rows = []
    for record in records:
        row = dict(record)
        row["timestamp"] = row.pop("ts", None)
        rows.append(row)
    return rows


async def _source_graph(
    app: FastAPI,
    subject: str,
    grade: str,
    store: StudentStore | None,
    student_id: str,
) -> dict[str, Any]:
    """Граф источника subject|grade для OKF-бандла (спека §4.2).

    Сначала пробуем E2-источник: in-memory реестр app.state.graph_store либо
    JSON-кэш src/kg для subject|grade. Если графа ещё нет — детерминированный
    «каркас» из изученных тем ученика (build_topic_scaffold по store.list_topics).
    Возврат всегда dict {nodes, edges}.
    """
    graph: dict[str, Any] | None = None
    if subject or grade:
        graph = _graph_store_get(app, subject, grade)
        if graph is None:
            try:
                chunks = _list_subject_chunks(app.state.rag_engine, subject, grade)
                topics, _merged, count, size = collect_snippets(chunks)
                key = graph_cache_key(subject, grade, count, size)
                graph = load_cached_graph(key, settings.resolved_knowledge_graph_dir)
            except Exception:  # noqa: BLE001 — чтение кэша не роняет экспорт
                graph = None
        if (graph or {}).get("nodes"):
            return graph
    topics: list[str] = []
    if store is not None:
        try:
            topics = sorted({
                str(row.get("topic") or "").strip()
                for row in store.list_topics(student_id)
                if (row.get("topic") or "").strip()
                and (not subject or (row.get("subject") or "") == subject)
            })
        except Exception:  # noqa: BLE001 — сбой БД не роняет экспорт
            topics = []
    root = _root_key(subject or "общая тема", grade)
    return build_topic_scaffold(root, topics)


# --- Граф знаний (E2, Слой 1/2): payload, оверлей мастерства, ленивая сборка ---

# Префикс «Урок/Параграф N …» в заголовках: срезается при матчинге topic↔узел.
_PREFIX_RE = re.compile(
    r"^(?:урок|параграф|lesson|section|module|unit|тема|раздел)"
    r"\s*\d+[.:\s—–-]*\s*",
    re.IGNORECASE,
)


def _norm_topic(title: str) -> str:
    """Нормализованный заголовок для матчинга title↔topic (§3.4)."""
    t = _PREFIX_RE.sub("", title or "").strip()
    return re.sub(r"\s+", " ", t).lower()


def _graph_key(subject: str, grade: str) -> str:
    """Единый ключ ``subject|grade``: реестр, сборка, события graph.ready."""
    return _root_key(subject, grade)


def _resolve_subject_grade(
    app: FastAPI, student_id: str, subject: str, grade: str
) -> tuple[str, str]:
    """subject/grade из запроса; недостающие части — из последней сессии ученика."""
    if subject and grade:
        return subject.strip(), grade.strip()
    store: StudentStore | None = app.state.student_store
    last = None
    if store is not None:
        try:
            last = store.get_last_session(student_id)
        except Exception:  # noqa: BLE001 — сбой БД не роняет запрос графа
            last = None
    return (
        (subject or (last or {}).get("subject") or "").strip(),
        (grade or (last or {}).get("grade") or "").strip(),
    )


def _overlay_mastery(
    rows_by_norm_topic: dict[str, dict[str, Any]], nodes: list[dict[str, Any]]
) -> None:
    """Добавляет узлам графа мастерство ученика (только для существующих тем)."""
    for node in nodes:
        row = rows_by_norm_topic.get(_norm_topic(str(node.get("title") or "")))
        if row is None:
            continue
        node["mastery"] = row.get("mastery", 0.0)
        node["attempts"] = row.get("attempts", 0)
        node["correct"] = row.get("correct", 0)
        node["accuracy"] = row.get("accuracy", 0.0)
        node["status"] = row.get("status", "not_studied")


def _maybe_sync_relations(
    store: StudentStore | None,
    student_id: str,
    subject: str,
    graph: dict[str, Any],
) -> None:
    """Разово переносит рёбра графа в relations тем ученика (только при чтении).

    Синк выполняется для тем без отношений; сбой БД не роняет ответ.
    """
    if store is None:
        return
    try:
        rows = [
            r for r in store.list_topics(student_id)
            if (r.get("subject") or "") == subject
        ]
        pending = [
            r for r in rows
            if not (r.get("relations") or {}).get("prerequisite")
            and not (r.get("relations") or {}).get("related")
        ]
        if not pending:
            return
        updates = sync_relations_from_graph(pending, graph["nodes"], graph["edges"])
        for topic, relations in updates.items():
            store.set_relations(student_id, topic, relations)
    except Exception:  # noqa: BLE001 — синк отношений не должен ронять запрос
        return


def _list_subject_chunks(
    rag: RAGEngine | None, subject: str, grade: str
) -> list[DocChunk]:
    """Чанки RAG для ``subject|grade`` (фильтр только по заданным полям)."""
    if rag is None or not (subject or grade):
        return []
    filters: dict[str, str] = {}
    if subject:
        filters["subject"] = subject
    if grade:
        filters["grade"] = grade
    return rag.list_chunks(filters)


def _graph_store_get(app: FastAPI, subject: str, grade: str) -> dict | None:
    """Граф из in-memory реестра (собран fire-and-forget в этом процессе)."""
    with app.state.knowledge_lock:
        return app.state.graph_store.get(_graph_key(subject, grade))


def _graph_store_put(app: FastAPI, subject: str, grade: str, graph: dict) -> None:
    """Кладёт собранный граф в in-memory реестр под ключом subject|grade."""
    with app.state.knowledge_lock:
        app.state.graph_store[_graph_key(subject, grade)] = graph


def _ensure_graph_build(
    app: FastAPI, subject: str, grade: str, chunks: list[DocChunk]
) -> None:
    """Запускает фоновую LLM-сборку графа (fire-and-forget, спека §4.4/§8).

    Ключ сборки держится в ``app.state.graph_building`` — повторный запрос той же
    subject|grade не создаёт дубль-задачу. Запрос никогда не блокируется на LLM;
    исключения целиком гасятся внутри ``_build_background``.
    """
    if not chunks:
        return
    key = _graph_key(subject, grade)
    with app.state.knowledge_lock:
        if key in app.state.graph_building:
            return
        app.state.graph_building.add(key)
    task = asyncio.create_task(_build_background(app, subject, grade, chunks))
    app.state._graph_tasks.add(task)
    task.add_done_callback(app.state._graph_tasks.discard)


async def _build_background(
    app: FastAPI, subject: str, grade: str, chunks: list[DocChunk]
) -> None:
    """Фоновая сборка: LLM (лениво из фабрики) → кэш → событие graph.ready.

    LLM-клиент берётся из ``runtime_factory`` в момент выполнения (не на импорте);
    после сборки граф кладётся в реестр/кэш и накапливается событие ``graph.ready``,
    которое следующий SSE-ход эмитит через ``_drain_graph_ready``.
    """
    key = _graph_key(subject, grade)
    try:
        llm = app.state.runtime_factory().llm
        models = LLMClientFactory.get_models_for_region(settings.region)
        graph = await build_or_load_graph(
            subject=subject,
            grade=grade,
            chunks=chunks,
            graph_dir=settings.resolved_knowledge_graph_dir,
            llm=llm,
            model=models["fast"],
        )
        _graph_store_put(app, subject, grade, graph)
        root = graph["nodes"][0]["id"] if graph.get("nodes") else None
        data = {
            "root": root,
            "stats": {
                "nodes": len(graph.get("nodes", [])),
                "edges": len(graph.get("edges", [])),
            },
        }
        app.state.graph_ready_events.setdefault(key, []).append(data)
        app.state.jsonl.log(
            JsonlLogger.new_trace_id(), "INFO", "kg.ready",
            key=key, root=root, stats=data["stats"],
        )
    except Exception as exc:  # noqa: BLE001 — фоновая сборка не роняет запрос
        app.state.jsonl.log(
            JsonlLogger.new_trace_id(), "ERROR", "kg.ready", error=str(exc)[:500]
        )
    finally:
        with app.state.knowledge_lock:
            app.state.graph_building.discard(key)


def _drain_graph_ready(
    app: FastAPI, on_event: Callable[[str, dict], None], subject: str, grade: str
) -> None:
    """Вынимает и эмитит накопленные события ``graph.ready`` для subject|grade."""
    events = app.state.graph_ready_events.pop(_graph_key(subject, grade), [])
    for data in events:
        on_event("graph.ready", data)


def _graph_for(
    app: FastAPI, subject: str, grade: str, *, schedule_build: bool = True
) -> dict[str, Any]:
    """Граф источника ``subject|grade``: реестр/кэш либо мгновенный каркас.

    При ``schedule_build=True`` и отсутствии кэша запускается fire-and-forget
    LLM-сборка (не блокирует запрос); related/wiki используют каркас без сборки.
    """
    rag: RAGEngine | None = app.state.rag_engine
    chunks = _list_subject_chunks(rag, subject, grade)
    topics, _merged, count, size = collect_snippets(chunks)
    key = graph_cache_key(subject, grade, count, size)
    graph: dict[str, Any] | None = load_cached_graph(
        key, settings.resolved_knowledge_graph_dir
    )
    if graph is None:
        graph = _graph_store_get(app, subject, grade)
    if graph is None:
        graph = build_topic_scaffold(_root_key(subject, grade), topics)
        if schedule_build and chunks:
            _ensure_graph_build(app, subject, grade, chunks)
    return graph


async def _provision_for(app: FastAPI, topic: str, subject: str = "", grade: str = "") -> int:
    """Провижинит материалы по теме один раз на ключ (subject|grade|topic).

    Приложение хранит app.state.provisioned (множество ключей) и счётчик
    app.state.ingested_chunks. Провал поиска не роняет чат (возвращает 0).
    """
    provisioner: Provisioner | None = app.state.provisioner
    if provisioner is None or not topic.strip():
        return 0
    key = "|".join((subject.strip(), grade.strip(), topic.strip()))
    with app.state.knowledge_lock:
        if key in app.state.provisioned:
            return 0
        app.state.provisioned.add(key)
    try:
        count = await asyncio.wait_for(
            provisioner(topic=topic, subject=subject, grade=grade),
            timeout=_PROVISION_TIMEOUT_SEC,
        )
        app.state.ingested_chunks += count
        return count
    except Exception:  # noqa: BLE001 — поиск/индексация не должны ронять чат
        with app.state.knowledge_lock:
            app.state.provisioned.discard(key)
        return 0


def _append_user_if_new(store: SessionStore, session_id: str, content: str) -> None:
    """Добавляет user-сообщение в историю (без дубликата последнего user)."""
    messages = store.list_messages(session_id)
    last = messages[-1] if messages else None
    if last and last.get("role") == "user" and last.get("content") == content:
        return
    store.append_message(session_id, "user", content)


def _review_message(kind_meta: str, env: ContentEnvelope) -> dict:
    """Одно feed-сообщение review-хода: content + санитизированный envelope."""
    return {
        "content": env.text,
        "envelope": sanitize_envelope(env).model_dump(),
        "kind": kind_meta,
        "review": True,
    }


def _review_history_message(
    app: FastAPI, session_id: str, msg: dict, kind_meta: str
) -> None:
    """Кладёт review-сообщение в историю сессии (ассистент + meta)."""
    app.state.sessions.append_message(
        session_id,
        "assistant",
        msg["content"],
        meta={"kind": kind_meta, "envelope": msg.get("envelope")},
    )


async def _grade_review_card(
    app: FastAPI, card: dict, incoming: str
) -> tuple[bool, str]:
    """Грейдит ответ на review-карточку.

    Закрытый вопрос (есть options) — детерминированно, без LLM. Открытый —
    коротким LLM-грейдером (role fast); runtime собирается лениво, чтобы
    в детерминированных сценариях не конструировать LLM-клиентов.
    """
    options = card.get("options")
    if options:
        return await grade_answer(
            None, "",
            question=str(card.get("question") or ""),
            correct_answer=str(card.get("correct_answer") or ""),
            answer=incoming or "",
            options=options,
        )
    runtime: AgentRuntime = app.state.runtime_factory()
    models = LLMClientFactory.get_models_for_region(settings.region)
    return await grade_answer(
        runtime.llm, str(models.get("fast") or ""),
        question=str(card.get("question") or ""),
        correct_answer=str(card.get("correct_answer") or ""),
        answer=incoming or "",
        options=None,
    )


_QUIZ_ANSWER_PREFIX = "Ответ:"
_OPTION_LETTERS = ("А", "Б", "В", "Г", "Д", "Е", "Ж", "З", "И", "К")

# Начала фраз, которые ученик адресует репетитору, а НЕ отвечает на открытый квиз
# («расскажи подробнее», «дай пример», «почему?», «можно подсказку» и т.п.).
# Используется только для открытых вопросов: свободный текст без «Ответ: » грейдим
# серверно, только если он не похож на встречный вопрос/директиву.
_OPEN_ANSWER_TUTOR_DIRECTIVE = (
    "что", "что такое", "чтотакое", "как", "как решить", "почему", "зачем",
    "можно", "объясни", "объясните", "подскажи", "подскажите", "помоги",
    "помогите", "расскажи", "расскажите", "дай", "дайте", "покажи", "покажите",
    "приведи", "приведите", "напиши", "напишите", "назови", "назовите",
    "перефразируй", "перефразируйте", "уточни", "уточните", "придумай",
    "составь", "реши", "решите", "сформулируй", "пример", "другой", "другое",
    "другие", "новая", "новую", "новый", "следующ", "перейдём", "перейдем",
    "перейди", "хватит", "стоп", "прекрати", "ещё", "еще", "повтори",
    "повторите", "продолжим", "вернись", "отмена", "отмени", "смени", "не знаю",
    "не могу", "не понимаю", "давай", "давайте", "подведи", "подытожь",
    "хочу", "хотел", "хотела", "хотелось", "поменяй", "переключи", "закончим",
    "непонятно", "не ясно", "не понял", "не понятно", "не совсем", "поясни",
    "поясните", "поподробнее", "то есть", "т е", "иными словами", "другими словами",
)


def _strip_answer_prefix(message: str) -> str:
    """Убирает префикс «Ответ: » из сообщения ученика (если есть)."""
    text = (message or "").strip()
    if text.lower().startswith(_QUIZ_ANSWER_PREFIX.lower()):
        return text[len(_QUIZ_ANSWER_PREFIX):].strip()
    return text


def _latest_assistant_kind(sessions: SessionStore, session_id: str) -> str | None:
    """kind последнего assistant-сообщения сессии (None, если его нет).

    Нужно для серверного грейда квиза: отвечаем только если последним ходом агента
    был quiz (или подсказка к нему) — это исключает «зависший» last_quiz после
    посторонних вопросов.
    """
    for message in reversed(sessions.list_messages(session_id)):
        if message.get("role") == "assistant":
            return message.get("kind")
    return None



def _norm_text(value: Any) -> str:
    """Нормализация для сравнения ответа с вариантом/эталоном."""
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _resolve_option_answer(message: str, quiz: dict) -> str:
    """Приводит ответ ученика к тексту варианта (буквы А-К → вариант).

    Открытые ответы (без options) и произвольный текст возвращаются без изменений
    (после среза префикса «Ответ: »).
    """
    answer = _strip_answer_prefix(message)
    options = quiz.get("options")
    if not options:
        return answer
    head = answer.strip()[:1].upper()
    if head in _OPTION_LETTERS:
        idx = _OPTION_LETTERS.index(head)
        if idx < len(options):
            return str(options[idx])
    return answer


def _looks_like_quiz_answer(message: str, quiz: dict) -> bool:
    """Похоже на ответ на активный квиз: префикс «Ответ: », вариант или буква.

    Для открытых вопросов (нет options) свободный текст без «Ответ: » тоже
    считается ответом, если он не похож на встречный вопрос/директиву репетитору
    (это позволяет грейдить ответ, набранный в главном поле чата).
    """
    text = (message or "").strip()
    if not text:
        return False
    if text.lower().startswith(_QUIZ_ANSWER_PREFIX.lower()):
        return True
    options = quiz.get("options")
    if not options:
        return not _is_tutor_directed_phrase(text)
    norm = _norm_text(text)
    if any(_norm_text(opt) == norm for opt in options):
        return True
    head = text[:1].upper()
    return head in _OPTION_LETTERS and _OPTION_LETTERS.index(head) < len(options)


def _is_tutor_directed_phrase(text: str) -> bool:
    """True, если фраза адресована репетитору (вопрос/просьба), а не ответ.

    Критерии: заканчивается «?» либо начинается с типовой директивы/вопроса
    («расскажи…», «дай пример…», «почему?»). Вопросительные фразы в любом месте
    не проверяем — только «хвост-?» и первый токен, чтобы не терять ответы вида
    «инерция — это когда тело…».
    """
    t = (text or "").strip()
    if not t:
        return True
    if t.endswith("?"):
        return True
    head = _norm_text(re.sub(r"[^\w\s]", " ", t))
    for start in _OPEN_ANSWER_TUTOR_DIRECTIVE:
        if head == start or head.startswith(start + " "):
            return True
    return False




async def _grade_active_quiz(
    app: FastAPI, quiz: dict, message: str
) -> tuple[bool, str]:
    """Серверный грейд ответа на активный in-chat квиз.

    Закрытый (есть options) — детерминированно: ответ сравнивается с эталоном
    (буква А-К приводится к тексту варианта). Открытый — коротким LLM-грейдером
    (role fast), как review-карточки. Возвращает (correct, feedback).
    """
    question = str(quiz.get("question") or "")
    correct_answer = str(quiz.get("correct_answer") or "")
    options = quiz.get("options")
    if options:
        answer = _resolve_option_answer(message, quiz)
        ok = _norm_text(answer) == _norm_text(correct_answer)
        feedback = "Верно!" if ok else f"Ошибка. Правильный ответ: {correct_answer}"
        return bool(ok), feedback
    runtime: AgentRuntime = app.state.runtime_factory()
    models = LLMClientFactory.get_models_for_region(settings.region)
    answer = _strip_answer_prefix(message)
    return await grade_answer(
        runtime.llm, str(models.get("fast") or ""),
        question=question,
        correct_answer=correct_answer,
        answer=answer or "",
        options=None,
    )


async def _run_review(
    app: FastAPI,
    session: ChatSession,
    student_id: str,
    body: ChatRequest,
    incoming: str | None,
) -> list[dict]:
    """Обслуживает один review-ход и возвращает feed-сообщения (0..2).

    Агент в review-режиме НЕ вызывается. Инвариант очереди: отдана карточка N —
    review_index == N + 1; на следующем ходе грейдится review_cards[N - 1]
    (spec 5.2). Каждое feed-сообщение дублируется в историю сессии (ассистент
    + meta kind/envelope), user-ответ уже добавлен вызывающим _run_chat.
    """
    store: StudentStore | None = app.state.student_store
    session_id = session.session_id
    messages: list[dict] = []

    def _emit(env: ContentEnvelope) -> dict:
        msg = _review_message(env.type.value, env)
        messages.append(msg)
        _review_history_message(app, session_id, msg, env.type.value)
        return msg

    # Активный блиц + повторный старт (kind=review_request): блокируем (spec 9).
    if session.review_requested and session.review_active:
        session.review_requested = False
        return messages

    # Шаг 1: старт блица — загружаем due-карточки, активируем очередь.
    if session.review_requested and not session.review_active:
        session.review_requested = False
        due: list[dict] = []
        if store is not None and settings.review_enabled:
            try:
                due = store.get_due_review(
                    student_id,
                    subject=session.subject or "",
                    limit=int(settings.review_quiz_size),
                )
            except Exception as exc:  # noqa: BLE001 — сбой БД не роняет чат
                print(f"[review] не удалось загрузить due-карточки: {exc}")
        if not due:
            _emit(ContentEnvelope(type="theory", text="Карточек на повторение нет."))
            return messages
        session.review_active = True
        session.review_cards = list(due)
        session.review_index = 0
        session.review_correct = 0
        session.review_reviewed = 0

    # Шаг 2: пришёл ответ на последнюю отданную карточку — грейдим.
    if session.review_active and session.review_index > 0 and incoming is not None:
        card = session.review_cards[session.review_index - 1]
        correct, feedback = await _grade_review_card(app, card, incoming)
        if store is not None:
            try:
                store.review_card(student_id, str(card.get("card_id") or ""), correct)
            except Exception as exc:  # noqa: BLE001
                print(f"[review] не удалось применить SM-2: {exc}")
        # Мастерство темы карточки тоже обновляется (spec E2 §3.4): карточку
        # НЕ создаёт, секрета квиза нет — запись собирается из полей карточки.
        record = None
        if store is not None and str(card.get("topic") or "").strip():
            try:
                record = build_answer_record(
                    session_id=session_id,
                    student_id=student_id,
                    topic=str(card.get("topic") or ""),
                    subject=str(card.get("subject") or "") or session.subject or "",
                    student_answer=incoming or "",
                    correct=bool(correct),
                    feedback=feedback or "",
                    last_quiz={
                        "question": str(card.get("question") or ""),
                        "options": card.get("options"),
                        "answer_type": str(card.get("answer_type") or "open"),
                        "difficulty": str(card.get("difficulty") or "medium"),
                        "correct_answer": str(card.get("correct_answer") or ""),
                    },
                )
                store.apply_result(
                    student_id, record.topic, record.subject or "", record
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[review] не удалось обновить мастерство: {exc}")
        # Knowledge Wiki (E4 §2.4): review-ответ тоже применяется к статье ученика;
        # пустое тело добивается фоновым обогащением (fail-soft, блиц не падает).
        wiki = _wiki_for(student_id)
        if wiki is not None and record is not None:
            try:
                rec_data = asdict(record)
                rec_data["score01"] = 1.0 if record.correct else 0.0
                art = wiki.apply_record(
                    rec_data,
                    subject=record.subject or "",
                    grade="",
                    curriculum="",
                )
                if art is not None:
                    _log_wiki(
                        app, "wiki.updated", student_id=student_id,
                        subject=art.subject, topic=art.topic,
                        mastery=art.mastery,
                    )
                    if not record.correct and art.notes:
                        _log_wiki(
                            app, "wiki.note", student_id=student_id,
                            subject=art.subject, topic=art.topic,
                            notes=len(art.notes),
                        )
                    await _schedule_enrich(app, student_id, body, art, wiki)
            except Exception as exc:  # noqa: BLE001
                print(f"[wiki] не удалось применить ответ: {exc}")
        # Журнал ответов (E4): review-ответ логируется как 'review:<card_id>'.
        if store is not None:
            try:
                journal = build_answer_record(
                    session_id=session_id,
                    student_id=student_id,
                    topic=str(card.get("topic") or ""),
                    subject=str(card.get("subject") or "") or session.subject or "",
                    student_answer=incoming or "",
                    correct=bool(correct),
                    feedback=feedback or "",
                    last_quiz={
                        "question": str(card.get("question") or ""),
                        "options": card.get("options"),
                        "answer_type": str(card.get("answer_type") or "open"),
                        "difficulty": str(card.get("difficulty") or "medium"),
                        "correct_answer": str(card.get("correct_answer") or ""),
                    },
                )
                entry = asdict(journal)
                entry["question_id"] = "review:" + str(card.get("card_id") or "")
                store.append_record(student_id, session_id, entry)
            except Exception as exc:  # noqa: BLE001
                print(f"[student] не удалось сохранить запись журнала: {exc}")
        session.review_reviewed += 1
        if correct:
            session.review_correct += 1
        _emit(
            ContentEnvelope(
                type="evaluation",
                text=feedback,
                payload={"correct": correct, "feedback": feedback},
            )
        )

    # Шаг 3: есть неотданные карточки — отдаём следующую.
    if session.review_active and session.review_index < len(session.review_cards):
        card = session.review_cards[session.review_index]
        payload = {
            "answer_type": str(card.get("answer_type") or "open"),
            "options": card.get("options"),
            "review": True,
            "card_id": "review:" + str(card.get("card_id") or ""),
            "num_questions": len(session.review_cards),
            "question_num": session.review_index + 1,
        }
        _emit(
            ContentEnvelope(
                type="quiz",
                text=str(card.get("question") or ""),
                payload=payload,
            )
        )
        session.review_index += 1
        return messages

    # Шаг 4: все карточки отвечены — итог и сброс review-полей.
    if session.review_active:
        _emit(
            ContentEnvelope(
                type="theory",
                text=(
                    f"Повторение завершено: верно {session.review_correct} "
                    f"из {session.review_reviewed}."
                ),
            )
        )
        session.review_active = False
        session.review_requested = False
        session.review_cards = []
        session.review_index = 0
        session.review_correct = 0
        session.review_reviewed = 0
    return messages


async def _run_chat(
    app: FastAPI,
    body: ChatRequest,
    on_event: Callable[[str, dict], None] | None = None,
) -> tuple[AgentGraphState, str, str, ContentEnvelope | None, dict, list[dict]]:
    """Общий путь POST /chat и SSE.

    Возвращает (state, session_id, trace_id, envelope, adaptive, messages),
    где messages — feed-сообщения этого хода: [{content, envelope, ...}].
    Обычный ход отдаёт 1 элемент (ответ агента), review-ход — 0..2
    (оценка прошлой карточки + следующая карточка / итог). Механика SSE:
    chat_stream эмитит каждое сообщение как отдельное SSE-событие message.
    Привязка сессия<->ученик и тема пишутся в SQLite на КАЖДОМ ходе
    (upsert, не только evaluation); сбой БД не роняет чат.
    """
    store: StudentStore | None = app.state.student_store
    profile = body.student_profile.model_dump(exclude_none=True)
    owner = (body.student_id or "").strip()
    session = app.state.sessions.get_or_create(
        session_id=body.session_id or None,
        student_profile=profile,
        student_id=owner,
        topic=body.topic,
        subject=body.subject,
        grade=body.grade,
    )
    session_id = session.session_id
    # Используем student_id из сессии (если уже был привязан), затем из запроса,
    # и только в крайнем случае генерируем новый. Это гарантирует, что при
    # отсутствии student_id в запросе прогресс не теряется между ходами.
    student_id = session.student_id or owner or _gen_id("stu")
    if not session.student_id and student_id:
        session.student_id = student_id

    # Накопленные события готовности графа (fire-and-forget сборка) уходят
    # первым SSE-фреймом этого хода; в POST /chat (on_event=None) не эмитятся.
    if on_event is not None:
        _drain_graph_ready(app, on_event, session.subject, session.grade)

    # Персистим привязку ученика, сессии и темы каждый ход. Генерируемый
    # fallback-student_id НЕ участвует в правиле владения (см. session_store).
    if store is not None:
        try:
            store.upsert_student(student_id)
            store.register_session(student_id, session_id, body.topic or "")
        except Exception as exc:  # noqa: BLE001
            print(f"[student] не удалось сохранить сессию: {exc}")

    # Review-режим (блиц): ветка ДО агента. kind=review_request или активная
    # сессия с review_requested/review_active обслуживаются без run_agent.
    review_mode = (
        body.kind == "review_request"
        or session.review_requested
        or session.review_active
    )
    if review_mode:
        if body.kind == "review_request":
            session.review_requested = True
        else:
            _append_user_if_new(app.state.sessions, session_id, body.message)
        messages = await _run_review(
            app=app,
            session=session,
            student_id=student_id,
            body=body,
            incoming=None if body.kind == "review_request" else body.message,
        )
        last = messages[-1] if messages else None
        env = (
            ContentEnvelope.model_validate(last["envelope"])
            if last and last.get("envelope") is not None
            else None
        )
        state = AgentGraphState(
            messages=[],
            final_answer=last["content"] if last else "",
            terminated=True,
        )
        state.content_envelope = env
        adaptive = _build_adaptive(
            store, student_id, body.topic, "medium", subject=body.subject
        )
        return state, session_id, "", env, adaptive, messages

    # Mastery-гейт (advisory): при выборе/смене темы советуем повторить незакрытые
    # пререквизиты ДО run_agent (spec §4.5); обучение не блокируется. Событие
    # уходит в тот же SSE-канал on_event("system", ...), что использует run_agent
    # для своих событий (system-фрейм до основного message-фрейма).
    if (
        on_event is not None
        and store is not None
        and body.topic.strip()
        and session.last_gate_topic != body.topic
    ):
        session.last_gate_topic = body.topic
        try:
            gaps = store.get_prerequisite_gaps(student_id, body.topic)
        except Exception:  # noqa: BLE001 — сбой БД не должен ронять чат
            gaps = []
        if gaps:
            message = (
                "Совет: прежде чем «"
                f"{body.topic}», стоит повторить: {', '.join(gaps)}."
            )
            on_event("system", {
                "message": message,
                "kind": "mastery.gate", "gaps": gaps, "topic": body.topic,
            })

    if body.topic:
        await _provision_for(app, topic=body.topic, subject=body.subject, grade=body.grade)

    # LinUCB (Этап 5): фиксация сыгранной руки. Объявляется до ветвления hint —
    # пост-обработка конверта ниже общая для обычного и hint-ходов.
    session_bandit: tuple[int, str, list[float]] | None = None

    hint_request = body.kind == "hint_request"
    if hint_request:
        # Guard: не более _MAX_HINTS_IN_A_ROW подсказок подряд на одну задачу.
        if (
            app.state.sessions.consecutive_assistant_kind(session_id, "hint")
            >= _MAX_HINTS_IN_A_ROW
        ):
            env = ContentEnvelope(
                type="hint",
                text="Вы уже получили несколько подсказок — попробуйте решить задачу сами, "
                "а если совсем сложно, задайте вопрос иначе.",
            )
            answer = env.text
            app.state.sessions.append_message(
                session_id,
                "assistant",
                answer,
                meta={"kind": env.type.value, "envelope": env.model_dump()},
            )
            adaptive = _build_adaptive(
                store, student_id, body.topic, "medium", subject=body.subject
            )
            state = AgentGraphState(messages=[], final_answer=answer, terminated=True)
            state.content_envelope = env
            return state, session_id, "", env, adaptive, [
                {"content": answer, "envelope": sanitize_envelope(env).model_dump()}
            ]
        # Служебный контекст вместо обычного сообщения ученика
        context = app.state.sessions.to_llm_context(session_id)
        context.append({"role": "user", "content": _HINT_SERVICE_TEXT})
        # Hint-ход без инструментов: модели не даются схемы (allow_tools=False),
        # иначе planner может ошибочно вызвать generate_quiz и выдать новый вопрос
        # вместо подсказки к текущей задаче.
        context.append({"role": "system", "content": _HINT_ANSWER_NOTE})
    else:
        _append_user_if_new(app.state.sessions, session_id, body.message)
        context = app.state.sessions.to_llm_context(session_id)
        # Quiz.blocked (Task 2): заблокированной сессии модель в ЭТОМ ходе не
        # выдаёт quiz — только practice/theory (заметка уходит в контекст).
        if session.quiz_blocked:
            context.append({"role": "system", "content": QUIZ_BLOCKED_NOTE})
        # LinUCB (Этап 5): советник сложности — рекомендация модели (не диктат).
        # Совет добавляем только если нет «висящего» задания (иначе ход — это
        # ответ ученика, и менять целевую сложность не нужно).
        if (
            settings.bandit_enabled
            and store is not None
            and student_id
            and body.topic.strip()
            and session.bandit_arm is None
        ):
            try:
                from src.student.linucb import arm_difficulty, build_features

                tp = store.get_topic(student_id, body.topic) or {}
                features = build_features(
                    accuracy=float(tp.get("accuracy") or 0.0),
                    attempts=int(tp.get("attempts") or 0),
                    fatigue=float((profile or {}).get("fatigue_level") or 0.0),
                    overall=overall_level(store.list_topics(student_id)),
                )
                bandit = store.get_topic_bandit(
                    student_id, body.topic, alpha=settings.bandit_alpha
                )
                arm, advice = _bandit_advice(features, bandit)
                session_bandit = (arm, body.topic, features)
                context.append({"role": "system", "content": advice})
                JsonlLogger(settings.log_file).log(
                    "", "INFO", "bandit.select",
                    topic=body.topic,
                    arm=arm,
                    difficulty=arm_difficulty(arm),
                    features=[round(v, 4) for v in features],
                )
            except Exception as exc:  # noqa: BLE001 — совет не должен ронять чат
                print(f"[bandit] совет не сформирован: {exc}")
                session_bandit = None

    runtime: AgentRuntime = app.state.runtime_factory()
    rag = app.state.rag_engine
    if rag is not None and getattr(runtime.tool_context, "rag", None) is None:
        runtime.tool_context.rag = rag
    if on_event is not None:
        runtime.on_event = on_event

    trace_id = JsonlLogger.new_trace_id()
    # Серверный грейд ответа на активный (не-review) in-chat квиз: если модель
    # выдала quiz (session.last_quiz зафиксирован) и пришло сообщение-ответ, вердикт
    # выносится ДО run_agent — закрытый вопрос детерминированно, открытый — коротким
    # LLM-грейдером. Агент в таких ходах не вызывается: он не видит _correct_answer
    # и оценивал бы «вслепую» (а порой и просил прислать ответ для проверки).
    server_grade = None
    if (
        body.kind == "message"
        and session.last_quiz is not None
        and _latest_assistant_kind(app.state.sessions, session_id) in {"quiz", "hint"}
        and _looks_like_quiz_answer(body.message, session.last_quiz)
    ):
        correct, feedback = await _grade_active_quiz(app, session.last_quiz, body.message)
        difficulty = str(session.last_quiz.get("difficulty") or "medium")
        if difficulty not in {"easy", "medium", "hard"}:
            difficulty = "medium"
        server_grade = ContentEnvelope(
            type="evaluation",
            text=feedback,
            payload={"correct": correct},
            difficulty=difficulty,
        )
        JsonlLogger(settings.log_file).log(
            trace_id, "INFO", "quiz.grade",
            session_id=session_id,
            answer_type=session.last_quiz.get("answer_type"),
            correct=correct,
            server=True,
        )
    if server_grade is not None:
        state = AgentGraphState(
            messages=[],
            final_answer=feedback,
            terminated=True,
            difficulty=server_grade.difficulty,
        )
        state.content_envelopes = [server_grade]
    else:
        state = await run_agent(
            runtime,
            messages=context,
            session_id=session_id,
            student_profile=profile,
            trace_id=trace_id,
            allow_tools=not hint_request,
        )

    # Конверты хода. Модель иногда отвечает НЕСКОЛЬКИМИ JSON-конвертами подряд
    # (например, theory + practice) — каждый становится отдельным сообщением и
    # отдельным блоком в чате, вместо сырого JSON-«словаря» одним текстом.
    raw_reply = state.final_answer or ""
    content_envs = state.content_envelopes
    if content_envs:
        envelopes = list(content_envs)
    else:
        parsed = parse_content_envelopes(raw_reply)
        if parsed:
            envelopes = parsed
        elif state.content_envelope is not None:
            envelopes = [state.content_envelope]
        else:
            envelopes = (
                [ContentEnvelope(type="theory", text=raw_reply)] if raw_reply else []
            )
    # Quiz из карточки generate_quiz авторитетен: модель могла исказить вопрос в
    # финальном конверте (утверждение вместо вопроса, раскрытый ответ) — берём
    # формулировку/эталон из короткого отдельного вызова инструмента.
    envelopes = await _resolve_tool_quiz(state, runtime, trace_id, body.topic, envelopes)
    # Серверный контроль качества quiz: квиз с ответом в вопросе или битой
    # структурой заменяется на theory (см. _guard_quiz_envelopes); для таких
    # квизов session.last_quiz не фиксируется и рука бандита не засчитывается.
    fallback_text = quiz_reject_text(blocked=session.quiz_blocked)
    envelopes, rejected = _guard_quiz_envelopes(
        app,
        trace_id,
        envelopes,
        fallback_text=fallback_text,
        streak=session.quiz_reject_streak + 1,
    )
    # Quiz.regen (Task 3): пока streak ниже CAP и сессия не заблокирована, после
    # отклонения делаем ОДНУ тихую регенерацию quiz тем же planner. Успешный
    # повторный quiz/practice ЗАМЕНЯЕТ canned-fallback (rejected очищается —
    # переход состояния ниже считает ход recovered). Секрет отклонённого квиза
    # в промпт не попадает (см. _regen_quiz / build_regen_instruction).
    if (
        rejected
        and not review_mode
        and not hint_request
        and not session.quiz_blocked
        and session.quiz_reject_streak < QUIZ_REJECT_CAP
    ):
        regen_envs = await _regen_quiz(runtime, context, rejected, trace_id)
        if regen_envs:
            regen_out, regen_rejected = _guard_quiz_envelopes(
                app, trace_id, regen_envs,
                fallback_text=fallback_text,
                streak=session.quiz_reject_streak + 1,
            )
            if not regen_rejected and any(
                env.type.value in {"quiz", "practice"} for env in regen_out
            ):
                envelopes = regen_out
                rejected = []
                JsonlLogger(settings.log_file).log(
                    trace_id, "INFO", "quiz.regen", status="ok",
                    type=(
                        "quiz" if any(
                            env.type.value == "quiz" for env in regen_out
                        ) else "practice"
                    ),
                    streak=session.quiz_reject_streak + 1,
                )
            else:
                JsonlLogger(settings.log_file).log(
                    trace_id, "INFO", "quiz.regen", status="failed",
                    streak=session.quiz_reject_streak + 1,
                )

    # Quiz.blocked (Task 3): на заблокированной сессии fallback-конверты теории
    # (текст == fallback_text) не дублируются — оставляем прочий контент хода
    # либо единственную короткую директиву QUIZ_BLOCKED_TEXT.
    if session.quiz_blocked and rejected:
        others = [env for env in envelopes if env.text != fallback_text]
        envelopes = others if others else [
            ContentEnvelope(type="theory", text=fallback_text)
        ]
    envelope = envelopes[-1] if envelopes else None
    for env in envelopes:
        if env.type.value == "quiz":
            session.last_quiz = _quiz_secret(env)
    if (
        settings.bandit_enabled
        and session_bandit is not None
        and envelope is not None
        and envelope.type.value in {"quiz", "practice"}
    ):
        session.bandit_arm, session.bandit_topic, session.bandit_features = session_bandit
    safe_envs = [sanitize_envelope(env) for env in envelopes]
    for env, safe_env in zip(envelopes, safe_envs, strict=True):
        if not env.text:
            continue
        app.state.sessions.append_message(
            session_id,
            "assistant",
            env.text,
            meta={
                "kind": env.type.value,
                "envelope": safe_env.model_dump(),
            },
        )

    # Quiz.reject (Task 2): переход состояния по итогам хода (не на hint-ходе).
    # rejected непуст => streak растёт; после CAP сессия блокируется, а первый
    # корректный ход сбрасывает счётчик и снимает блок.
    if not hint_request:
        session.quiz_reject_streak, session.quiz_blocked = next_reject_state(
            session.quiz_reject_streak,
            session.quiz_blocked,
            recovered=not rejected,
        )

    # Обновление мастерства темы после evaluation-хода (сбой БД не роняет чат);
    # upsert/register_session уже выполнен выше на каждом ходе.
    if store is not None and envelope is not None and envelope.type.value == "evaluation":
        payload = envelope.payload or {}
        # Answer record из секрета последнего квиза и последнего user-сообщения
        # (E1): идёт и в mastery (apply_result), и в review-банк (add_review_card).
        record = None
        try:
            last_user = next(
                (
                    m.get("content")
                    for m in reversed(app.state.sessions.list_messages(session_id))
                    if m.get("role") == "user"
                ),
                "",
            )
            record = build_answer_record(
                session_id=session_id,
                student_id=student_id,
                topic=body.topic,
                subject=body.subject,
                student_answer=last_user or body.message,
                correct=bool(payload.get("correct", False)),
                feedback=envelope.text or "",
                last_quiz=session.last_quiz,
            )
            session.last_quiz = None
        except Exception as exc:  # noqa: BLE001
            print(f"[review] не удалось собрать ответ: {exc}")
        # LinUCB (Этап 5): обновление бандита наградой за ответ на задание.
        if (
            settings.bandit_enabled
            and session.bandit_arm is not None
            and session.bandit_topic == body.topic
            and record is not None
        ):
            try:
                from src.student.linucb import arm_difficulty, bandit_update

                bandit = store.get_topic_bandit(
                    student_id, session.bandit_topic, alpha=settings.bandit_alpha
                )
                reward = 1.0 if record.correct else 0.0
                bandit_update(
                    bandit,
                    list(session.bandit_features),
                    arm=int(session.bandit_arm),
                    reward=reward,
                )
                store.set_topic_bandit(student_id, session.bandit_topic, bandit)
                JsonlLogger(settings.log_file).log(
                    trace_id, "INFO", "bandit.update",
                    topic=session.bandit_topic,
                    arm=int(session.bandit_arm),
                    difficulty=arm_difficulty(int(session.bandit_arm)),
                    reward=reward,
                    arms_n=[a["n"] for a in bandit["arms"]],
                )
            except Exception as exc:  # noqa: BLE001 — бандит не должен ронять чат
                print(f"[bandit] не удалось обновить: {exc}")
            finally:
                session.bandit_arm = None
                session.bandit_topic = ""
                session.bandit_features = []
        # Мастерство темы (EMA/status) по answer record вместо legacy touch_topic.
        if getattr(record, "topic", None):
            try:
                state_row = store.apply_result(
                    student_id, record.topic, record.subject or body.subject, record
                )
                jsonl = JsonlLogger(settings.log_file)
                jsonl.log(
                    trace_id, "INFO", "mastery.update",
                    topic=record.topic,
                    mastery=state_row["mastery"],
                    status=state_row["status"],
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[student] не удалось применить мастерство: {exc}")
        # Авто-добавление карточки повторения (SM-2) при неверном ответе (E1).
        if (
            record is not None
            and settings.review_enabled
            and not record.correct
            and record.question
            and record.correct_answer
        ):
            try:
                store.add_review_card(
                    student_id,
                    {
                        "question": record.question,
                        "topic": record.topic,
                        "subject": record.subject or body.subject,
                        "options": record.options,
                        "answer_type": record.answer_type,
                        "correct_answer": record.correct_answer,
                        "difficulty": record.difficulty,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[review] не удалось добавить карточку: {exc}")
        # Журнал ответов (E4): каждый evaluation пишется в session_records.
        if record is not None:
            try:
                store.append_record(student_id, session_id, asdict(record))
            except Exception as exc:  # noqa: BLE001
                print(f"[student] не удалось сохранить запись журнала: {exc}")
        # Knowledge Wiki (E4): применяем ответ к статье ученика; пустое тело
        # добивается фоновым обогащением (fail-soft, чат не падает).
        wiki = _wiki_for(student_id)
        if wiki is not None and record is not None:
            try:
                rec_data = asdict(record)
                rec_data["score01"] = 1.0 if record.correct else 0.0
                art = wiki.apply_record(
                    rec_data,
                    subject=body.subject,
                    grade=body.grade,
                    curriculum="",
                )
                if art is not None:
                    _log_wiki(
                        app, "wiki.updated", student_id=student_id,
                        subject=art.subject, topic=art.topic,
                        mastery=art.mastery,
                    )
                    if not record.correct and art.notes:
                        _log_wiki(
                            app, "wiki.note", student_id=student_id,
                            subject=art.subject, topic=art.topic,
                            notes=len(art.notes),
                        )
                    await _schedule_enrich(app, student_id, body, art, wiki)
            except Exception as exc:  # noqa: BLE001
                print(f"[wiki] не удалось применить ответ: {exc}")

    next_from_model = None
    if envelope is not None and envelope.payload and envelope.payload.get("next_topic"):
        next_from_model = envelope.payload["next_topic"]
    adaptive = _build_adaptive(
        store, student_id, body.topic, state.difficulty, next_from_model,
        subject=body.subject,
    )
    return state, session_id, trace_id, envelope, adaptive, [
        {
            "content": safe_env.text or "",
            "envelope": safe_env.model_dump(),
        }
        for safe_env in safe_envs
    ]


def create_app(
    runtime_factory: RuntimeFactory | None = None,
    sessions: SessionStore | None = None,
    rag_engine: RAGEngine | None = None,
    provisioner: Provisioner | None = None,
    student_store: StudentStore | None = None,
) -> FastAPI:
    """Создаёт FastAPI-приложение (runtime_factory/rag инъекции — для тестов).

    Если rag_engine не задан, создаётся общий in-memory RAG-движок; эмбеддер
    выбирается по `settings.embedding_provider`: local — локальный
    sentence-transformers, api — OpenAI-совместимый /embeddings RouterAI.
    """
    app = FastAPI(title="Adaptive Tutor API", version="0.2.0")
    app.state.runtime_factory = runtime_factory or build_runtime
    app.state.sessions = sessions or SessionStore()
    app.state.student_store = student_store or StudentStore(settings.resolved_student_db_path)
    app.state.rag_engine = rag_engine
    if rag_engine is None:
        app.state.rag_engine = RAGEngine()
    if provisioner is None and app.state.rag_engine is not None:
        app.state.provisioner = _make_provisioner(app.state.rag_engine)
    else:
        app.state.provisioner = provisioner
    app.state.provisioned: set[str] = set()
    app.state.ingested_chunks = 0
    app.state.knowledge_lock = threading.Lock()
    # Граф знаний (E2): in-memory реестр собранных графов, ключи активной
    # сборки, накопленные события graph.ready и JSONL-логгер приложения.
    app.state.graph_store: dict[str, dict] = {}
    app.state.graph_building: set[str] = set()
    app.state.graph_ready_events: dict[str, list[dict]] = {}
    app.state._graph_tasks: set = set()
    app.state.jsonl = JsonlLogger(settings.log_file)
    # Knowledge Wiki (E4): ключи активного обогащения, ссылки на фоновые задачи
    # и JSONL-логгер wiki-событий.
    app.state.wiki_enriching: set[str] = set()
    app.state._enrich_tasks: set = set()
    app.state.jsonl_logger = app.state.jsonl

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, Any]:
        """Проверка живости сервиса."""
        return {
            "status": "ok",
            "region": settings.region.value,
            "sessions": app.state.sessions.active_count(),
            "knowledge_chunks": app.state.ingested_chunks,
            "rag_enabled": app.state.rag_engine is not None,
        }

    @app.post("/chat", response_model=ChatResponse)
    async def chat(body: ChatRequest, request: Request) -> ChatResponse:
        """Синхронный чат: полный ответ одним JSON (fallback для фронтенда)."""
        try:
            state, session_id, trace_id, _envelope, adaptive, messages = (
                await asyncio.wait_for(
                    _run_chat(request.app, body),
                    timeout=settings.max_agent_time_sec + 30,
                )
            )
        except TimeoutError:
            return ChatResponse(
                reply=FALLBACK_REPLY,
                error="превышен таймаут агента",
                trace_id="",
                session_id=body.session_id,
                adaptive={"student_id": body.student_id, "recommended_next": None},
                terminated=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"внутренняя ошибка: {exc}") from exc
        envelope = (
            ContentEnvelope.model_validate(messages[-1]["envelope"])
            if messages and messages[-1].get("envelope") is not None
            else None
        )
        if messages:
            state = state.model_copy(update={"final_answer": messages[-1].get("content") or ""})
        return _to_response(
            state,
            trace_id=trace_id,
            session_id=session_id,
            envelope=envelope,
            adaptive=adaptive,
        )

    @app.post("/chat/stream")
    async def chat_stream(body: ChatRequest, request: Request):
        """SSE-стрим: события агента + финальный message + done."""
        events: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

        def on_event(event: str, data: dict[str, Any]) -> None:
            if event in ("message", "done"):
                return  # финальные события шлём сами, с adaptive
            events.put_nowait((event, data))

        async def run() -> None:
            try:
                state, session_id, trace_id, _envelope, adaptive, messages = (
                    await _run_chat(request.app, body, on_event=on_event)
                )
            except Exception as exc:  # noqa: BLE001
                events.put_nowait(("error", {"message": str(exc)}))
                events.put_nowait(("done", {"session_id": body.session_id}))
                return
            # Механика SSE: каждый элемент фида — отдельное message-событие
            # (в review-ходе их 1..2), затем одно done.
            for item in messages:
                events.put_nowait(
                    (
                        "message",
                        {
                            "content": item["content"],
                            "envelope": item.get("envelope"),
                            "adaptive": adaptive,
                            "error": state.error,
                            "session_id": session_id,
                        },
                    )
                )
            events.put_nowait(
                (
                    "done",
                    {"session_id": session_id, "trace_id": trace_id, "steps": len(state.steps)},
                )
            )

        task = asyncio.create_task(run())

        async def gen():
            try:
                while True:
                    try:
                        event, data = await asyncio.wait_for(events.get(), timeout=_HEARTBEAT_SEC)
                    except TimeoutError:
                        if task.done():
                            break
                        yield _sse("heartbeat", {"ts": time.time()})
                        continue
                    yield _sse(event, data)
                    if event == "done":
                        break
                with contextlib.suppress(Exception):  # ошибка уже отправлена как событие
                    await task
            finally:
                if not task.done():
                    task.cancel()

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/chat/history/{session_id}")
    def chat_history(session_id: str) -> dict[str, Any]:
        """История сообщений сессии (для resync фронтенда)."""
        messages = app.state.sessions.list_messages(session_id)
        if not app.state.sessions.get(session_id):
            raise HTTPException(status_code=404, detail="сессия не найдена")
        return {"session_id": session_id, "messages": messages}

    @app.delete("/chat/history/{session_id}", status_code=204)
    def chat_history_delete(session_id: str):
        """Удаляет сессию и её историю."""
        if not app.state.sessions.delete(session_id):
            raise HTTPException(status_code=404, detail="сессия не найдена")
        return None

    @app.get("/knowledge")
    def knowledge_status() -> dict[str, Any]:
        """Статус базы знаний."""
        return {
            "rag_enabled": app.state.rag_engine is not None,
            "ingested_chunks": app.state.ingested_chunks,
            "provisioned_topics": sorted(app.state.provisioned),
        }

    @app.post("/knowledge/provision")
    async def knowledge_provision(body: KnowledgeProvisionRequest) -> dict[str, Any]:
        """Ручной провижининг материалов по теме (веб-поиск -> RAG)."""
        if app.state.provisioner is None:
            raise HTTPException(status_code=503, detail="RAG отключён")
        count = await _provision_for(
            app, topic=body.topic, subject=body.subject, grade=body.grade
        )
        return {"indexed": count, "topic": body.topic}

    @app.get("/student/{student_id}")
    def student_profile(student_id: str) -> dict[str, Any]:
        """Профиль ученика: темы, уровни, рекомендация, поля карточки знакомства."""
        store: StudentStore = app.state.student_store
        if store is None or store.get_student(student_id) is None:
            raise HTTPException(status_code=404, detail="студент не найден")
        topics = store.list_topics(student_id)
        row = store.get_student(student_id)
        return {
            "student_id": student_id,
            "name": row.get("name", ""),
            "learner_type": row.get("learner_type", ""),
            "grade": row.get("grade", ""),
            "topics": topics,
            "recommended_next": pick_recommendation(topics),
        }

    @app.post("/student/{student_id}/profile")
    def student_profile_update(student_id: str, body: IntakeProfile) -> dict[str, Any]:
        """Сохраняет профиль ученика из карточки знакомства (upsert, не 404)."""
        store: StudentStore = app.state.student_store
        if store is None:
            return {
                "student_id": student_id,
                "name": body.name,
                "learner_type": body.learner_type,
                "grade": body.grade,
            }
        try:
            return store.set_profile(
                student_id,
                name=body.name,
                learner_type=body.learner_type,
                grade=body.grade,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"внутренняя ошибка: {exc}") from exc

    @app.get("/student/{student_id}/sessions")
    def student_sessions(student_id: str) -> dict[str, Any]:
        """Сессии ученика (последние сверху). Не 404 для неизвестного ученика."""
        store: StudentStore = app.state.student_store
        sessions = store.list_sessions(student_id) if store is not None else []
        return {"student_id": student_id, "sessions": sessions}

    @app.get("/student/{student_id}/review")
    def student_review(student_id: str) -> dict[str, Any]:
        """SM-2: статистика + due-карточки."""
        store: StudentStore = app.state.student_store
        try:
            stats = store.review_stats(student_id) if store is not None else {}
            due = (store.list_review_cards(student_id, limit=50, only_due=True)
                   if store is not None else [])
            return {"stats": stats, "due": due}
        except Exception:  # noqa: BLE001
            return {"stats": {"total": 0, "due": 0, "lapses": 0, "by_topic": {}}, "due": []}

    @app.get("/student/{student_id}/knowledge-graph")
    def student_knowledge_graph(student_id: str, subject: str = "") -> dict[str, Any]:
        """Слой 2: статусы тем ученика (mastery/статистика) — спека §5."""
        store: StudentStore | None = app.state.student_store
        rows: list[dict] = []
        if store is not None:
            try:
                rows = [
                    r for r in store.list_topics(student_id)
                    if not subject or (r.get("subject") or "") == subject
                ]
            except Exception:  # noqa: BLE001 — сбой БД не роняет ответ
                rows = []
        return knowledge_graph_payload(student_id, subject, rows)

    @app.get("/student/{student_id}/recommendations")
    def student_recommendations(
        student_id: str, current_topic: str = "", subject: str = "", limit: int = 5
    ) -> dict[str, Any]:
        """Рекомендации тем (limit 1..20): слабые, пробелы, в работе — спека §5."""
        empty = {"recommendations": [], "weak_topics": [], "prerequisite_gaps": []}
        store: StudentStore | None = app.state.student_store
        limit = max(1, min(int(limit), 20))
        if store is None:
            return empty
        try:
            recommendations = store.recommend_topics(
                student_id, subject=subject, current_topic=current_topic, limit=limit
            )
            weak = store.get_weak_topics(student_id, subject=subject)
            gaps = (
                store.get_prerequisite_gaps(student_id, current_topic)
                if current_topic else []
            )
        except Exception:  # noqa: BLE001 — сбой БД не роняет ответ
            return empty
        return {
            "recommendations": [row_payload(r) for r in recommendations],
            "weak_topics": [row_payload(r) for r in weak],
            "prerequisite_gaps": list(gaps),
        }

    @app.get("/student/{student_id}/graph")
    async def student_graph(
        student_id: str, subject: str = "", grade: str = ""
    ) -> dict[str, Any]:
        """Граф источника subject|grade с мастерством ученика; каркас + фоновая сборка.

        Возвращает мгновенный каркас (или кэшированный граф), не блокируя запрос
        на LLM: полная сборка запускается fire-and-forget, готовность сообщается
        SSE-событием ``graph.ready`` на следующем ходе.
        """
        store: StudentStore | None = app.state.student_store
        subject, grade = _resolve_subject_grade(app, student_id, subject, grade)
        graph = _graph_for(app, subject, grade, schedule_build=True)
        _maybe_sync_relations(store, student_id, subject, graph)
        nodes = [dict(n) for n in graph["nodes"]]
        rows: list[dict] = []
        if store is not None:
            try:
                rows = [
                    r for r in store.list_topics(student_id)
                    if (r.get("subject") or "") == subject
                ]
            except Exception:  # noqa: BLE001
                rows = []
        rows_by_topic = {_norm_topic(str(r["topic"])): r for r in rows}
        _overlay_mastery(rows_by_topic, nodes)
        return {
            "root": nodes[0]["id"] if nodes else None,
            "nodes": nodes,
            "edges": graph["edges"],
            "active_topic": None,
            "stats": {"nodes": len(nodes), "edges": len(graph["edges"])},
        }

    @app.get("/student/{student_id}/graph/{node_id}/related")
    def student_graph_related(
        student_id: str, node_id: str, subject: str = "", grade: str = ""
    ) -> dict[str, Any]:
        """Соседи узла графа до глубины 2 (спека §5/§8)."""
        subject, grade = _resolve_subject_grade(app, student_id, subject, grade)
        graph = _graph_for(app, subject, grade, schedule_build=False)
        node = next(
            (dict(n) for n in graph["nodes"] if n.get("id") == node_id), None
        )
        if node is None:
            return {"node": None, "related": []}
        return {"node": node, "related": KnowledgeGraph.from_dict(graph).neighbors(
            node_id, max_depth=2
        )}

    @app.get("/student/{student_id}/graph/{node_id}/wiki")
    def student_graph_wiki(
        student_id: str, node_id: str, subject: str = "", grade: str = ""
    ) -> dict[str, Any]:
        """Статья узла графа: реальная статья ученика по теме узла (E4 §5).

        Статья ищется среди wiki ученика по предмету ``subject`` и нормализованному
        заголовку узла (та же конвенция, что у mastery-оверлея). Нет статьи —
        ``wiki: null``; битый wiki/файловый I/O — fail-soft (не 500).
        """
        subject, grade = _resolve_subject_grade(app, student_id, subject, grade)
        graph = _graph_for(app, subject, grade, schedule_build=False)
        node = next(
            (dict(n) for n in graph["nodes"] if n.get("id") == node_id), None
        )
        if node is None:
            raise HTTPException(status_code=404, detail="Узел не найден")
        article = None
        wiki = _wiki_for(student_id)
        if wiki is not None:
            try:
                title_norm = _norm_topic(str(node.get("title") or ""))
                for art in wiki.list_articles():
                    if subject and not (
                        art.subject == subject
                        or _slug(art.subject) == _slug(subject)
                    ):
                        continue
                    if title_norm and _norm_topic(
                        str(art.topic or art.title or "")
                    ) == title_norm:
                        article = art
                        break
            except Exception:  # noqa: BLE001 — wiki I/O fail-soft → wiki: null
                article = None
        return {
            "node": node,
            "wiki": article.to_dict() if article is not None else None,
        }

    # --- Knowledge Wiki (E4): статьи ученика, обогащение, удаление ---

    @app.get("/student/{student_id}/wiki")
    def student_wiki(student_id: str, subject: str = Query(default="")) -> dict[str, Any]:
        """Список статей: все предметы или один subject (человеческое имя)."""
        wiki = _wiki_for(student_id)
        articles = wiki.list_articles() if wiki is not None else []
        if subject:
            picked = [
                a for a in articles
                if a.subject == subject or _slug(a.subject) == _slug(subject)
            ]
            return {
                "subject": picked[0].subject if picked else subject,
                "articles": [a.to_dict() for a in picked],
            }
        groups: dict[str, list] = {}
        for a in articles:
            groups.setdefault(a.subject, []).append(a.to_dict())
        return {
            "subjects": [
                {"subject": s, "articles": arts} for s, arts in sorted(groups.items())
            ]
        }

    @app.get("/student/{student_id}/wiki/{subject}/{topic}")
    def student_wiki_article(student_id: str, subject: str, topic: str) -> dict[str, Any]:
        """Одна статья (URL-сегменты — человеческие имена; ищутся по slug)."""
        wiki = _wiki_for(student_id)
        art = (
            wiki.article_by_slug(_slug(subject), _slug(topic))
            if wiki is not None else None
        )
        if art is None:
            raise HTTPException(status_code=404, detail="Тема не найдена в базе знаний")
        return art.to_dict()

    @app.post("/student/{student_id}/wiki/enrich")
    async def student_wiki_enrich(
        student_id: str, body: WikiEnrichRequest
    ) -> dict[str, Any]:
        """Обогащение тела статьи LLM по RAG-материалам темы (не 404 при пустых)."""
        wiki = _wiki_for(student_id)
        if wiki is None:
            return {"article": None, "note": "Wiki выключено."}
        art = wiki.get(body.subject, body.topic)
        if art is None:
            art = WikiArticle(subject=body.subject, topic=body.topic, title=body.topic)
        context = _rag_context(app, body.topic, body.subject, "")
        if not context:
            return {
                "article": None,
                "note": "Нет материалов по теме в базе знаний — пройдите квиз или "
                "добавьте источник, затем повторите.",
            }
        llm = app.state.runtime_factory().llm
        models = LLMClientFactory.get_models_for_region(settings.region)
        res = await wiki_enrich.enrich_body(
            wiki, body.subject, body.topic, context, llm,
            model=models.get("fast", "fast"),
        )
        fresh = wiki.get(body.subject, body.topic)
        return {
            "article": fresh.to_dict() if fresh is not None else None,
            "note": "" if res is not None else "Не удалось сформировать конспект.",
        }

    @app.delete("/student/{student_id}/wiki/{subject}/{topic}")
    def student_wiki_delete(student_id: str, subject: str, topic: str) -> dict[str, Any]:
        """Удаляет статью ученика; 404 для отсутствующей."""
        wiki = _wiki_for(student_id)
        ok = wiki.delete(subject, topic) if wiki is not None else False
        if not ok:
            raise HTTPException(status_code=404, detail="Тема не найдена в базе знаний")
        _log_wiki(
            app, "wiki.updated", student_id=student_id,
            subject=subject, topic=topic, mastery=None, deleted=True,
        )
        return {"deleted": True, "subject": subject, "topic": topic}

    # --- Экспорт (E4): CSV журнала ответов и OKF-бандл графа источника ---

    @app.get("/student/{student_id}/export/csv")
    def export_csv(
        student_id: str,
        subject: str = Query(default=""),
        limit: int = Query(default=500, ge=1),
    ) -> Response:
        """Скачивание CSV журнала вопросов (Excel: utf-8-sig)."""
        store: StudentStore | None = app.state.student_store
        if store is None:
            raise HTTPException(status_code=503, detail="хранилище недоступно")
        records = store.list_records(student_id, subject=subject or None, limit=limit)
        csv_text = csv_exporter.questions_csv(_question_rows(records))
        _log_export(
            app, "export.csv", student_id=student_id, count=len(records),
            bytes=len(csv_text.encode("utf-8")),
        )
        return Response(
            content=csv_text.encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{student_id}_session_log.csv"'},
        )

    @app.get("/student/{student_id}/export/summary.csv")
    def export_summary_csv(
        student_id: str,
        subject: str = Query(default=""),
        limit: int = Query(default=500, ge=1),
    ) -> Response:
        """Скачивание CSV сводки по сессиям."""
        store: StudentStore | None = app.state.student_store
        if store is None:
            raise HTTPException(status_code=503, detail="хранилище недоступно")
        records = store.list_records(student_id, subject=subject or None, limit=limit)
        mastered = _mastered_set(store, student_id)
        csv_text = csv_exporter.summary_csv(
            _summary_rows(store, student_id, records, mastered)
        )
        _log_export(
            app, "export.summary", student_id=student_id, count=len(records),
            bytes=len(csv_text.encode("utf-8")),
        )
        return Response(
            content=csv_text.encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{student_id}_summary.csv"'},
        )

    @app.get("/student/{student_id}/export/okf")
    async def export_okf(
        student_id: str,
        subject: str = Query(default=""),
        grade: str = Query(default=""),
    ) -> dict[str, Any]:
        """OKF-бандл графа источника subject|grade (манифест)."""
        store: StudentStore | None = app.state.student_store
        if store is None:
            raise HTTPException(status_code=503, detail="хранилище недоступно")
        subject = (subject or _last_subject(store, student_id)).strip() or "общая тема"
        graph = await _source_graph(app, subject, grade, store, student_id)
        mastery = _mastery_by_title(store, student_id)
        out_dir = Path(settings.resolved_okf_dir) / student_id / _slug(subject)
        okf_export.emit_okf_bundle(
            out_dir, subject=subject, grade=grade, curriculum="",
            graph=graph, mastery=mastery,
        )
        manifest = okf_export.validate_bundle(out_dir)
        _log_export(
            app, "export.okf", student_id=student_id, subject=subject,
            files=len(manifest["files"]), conformant=manifest["conformant"],
        )
        return {"dir": str(out_dir), **manifest}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
