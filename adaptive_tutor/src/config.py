from enum import StrEnum

from pydantic import Field
from pydantic_settings import BaseSettings


def _resolve_data_path(path: str) -> str:
    import os
    if os.path.isabs(path):
        return path
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, path)


class Region(StrEnum):
    RU = "RU"
    GLOBAL = "GLOBAL"


class Settings(BaseSettings):
    region: Region = Field(default=Region.GLOBAL, description="Регион: RU или GLOBAL")
    
    # RouterAI (RU)
    routerai_api_key: str = Field(default="", description="API ключ RouterAI")
    routerai_base_url: str = Field(
        default="https://routerai.ru/api/v1", description="Base URL RouterAI"
    )

    # OpenRouter (Global)
    openrouter_api_key: str = Field(default="", description="API ключ OpenRouter")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", description="Base URL OpenRouter"
    )

    # Yandex Search (RU)
    yandex_api_key: str = Field(default="", description="API ключ Yandex Search")
    yandex_folder_id: str = Field(
        default="", description="Каталог (folderId) Yandex Cloud для Yandex Search API v2"
    )
    yandex_search_url: str = Field(
        default="https://searchapi.api.cloud.yandex.net/v2/web/search",
        description="URL Yandex Cloud Search API v2",
    )
    # Tavily Search (Global)
    tavily_api_key: str = Field(default="", description="API ключ Tavily")
    
    # DuckDuckGo (Fallback)
    ddgs_enabled: bool = Field(default=True, description="Включить DuckDuckGo как fallback")
    
    # Qdrant
    qdrant_url: str = Field(default="http://localhost:6333", description="URL Qdrant")
    qdrant_collection: str = Field(default="adaptive_tutor", description="Коллекция Qdrant")

    # Каскад LLM: провайдеры + фолбек-модели + retry (как в референсе project_work)
    llm_primary_provider: str = Field(
        default="", description="Основной провайдер: routerai|openrouter (пусто — по региону)"
    )
    llm_retries: int = Field(
        default=2, ge=0, description="Доп. попытки при временных ошибках (429/5xx/сеть)"
    )
    llm_timeout_sec: float = Field(
        default=120.0, gt=0, description="Таймаут одного LLM-запроса (сек)"
    )
    llm_fallback_models: str = Field(
        default="",
        description="Фолбек-модели (через запятую) для planner/fast (пусто — дефолт региона)",
    )
    llm_judge_fallback_models: str = Field(
        default="",
        description="Фолбек-модели (через запятую) для judge (пусто — дефолт региона)",
    )
    llm_retry_empty: bool = Field(
        default=True,
        description="Пустой ответ (без tool_calls) считать ошибкой и пробовать следующий кандидат",
    )
    llm_max_tokens: int = Field(
        default=6144,
        ge=256,
        description="Макс. токенов ответа planner (уроки/JSON-конверты могут быть длинными)",
    )
    llm_quiz_max_tokens: int = Field(
        default=768,
        ge=128,
        description="Макс. токенов отдельного вызова generate_quiz (короткая карточка квиза)",
    )
    # Ролевые модели (переопределяют региональные дефолты get_models_for_region)
    llm_planner_model: str = Field(
        default="", description="Модель planner (пусто — дефолт региона)"
    )
    llm_fast_model: str = Field(default="", description="Модель fast (пусто — дефолт региона)")
    llm_judge_model: str = Field(
        default="", description="Модель judge (пусто — дефолт региона)"
    )
    
    # Эмбеддинги
    embedding_provider: str = Field(
        default="local", description="Провайдер эмбеддингов: local или api"
    )
    embedding_model: str = Field(
        default="intfloat/multilingual-e5-small", description="Модель эмбеддингов"
    )
    # LinUCB: советник сложности заданий quiz/practice
    bandit_enabled: bool = Field(default=True, description="Включить LinUCB-советник")
    bandit_alpha: float = Field(default=0.6, description="Параметр исследования LinUCB")

    # Профиль ученика
    student_db_path: str = Field(
        default="data/students.db", description="Путь к SQLite-файлу профилей"
    )

    @property
    def resolved_student_db_path(self) -> str:
        """Абсолютный путь к БД: относительные пути считаются от каталога adaptive_tutor/."""
        return _resolve_data_path(self.student_db_path)

    # Knowledge Wiki и экспорт (E4)
    knowledge_wiki_dir: str = Field(
        default="data/knowledge_wiki",
        description="Корень wiki-статей (относительно adaptive_tutor/)",
    )
    okf_dir: str = Field(
        default="data/okf", description="Корень OKF-бандлов экспорта (относительно adaptive_tutor/)"
    )
    wiki_enabled: bool = Field(default=True, description="Включить Knowledge Wiki")
    wiki_enrich_enabled: bool = Field(
        default=True, description="Включить ленивое LLM-обогащение тела статьи"
    )

    @property
    def resolved_knowledge_wiki_dir(self) -> str:
        """Абсолютный путь к корню wiki: как student_db_path (от adaptive_tutor/)."""
        return _resolve_data_path(self.knowledge_wiki_dir)

    @property
    def resolved_okf_dir(self) -> str:
        """Абсолютный путь к корню OKF-бандлов."""
        return _resolve_data_path(self.okf_dir)

    # Граф знаний (E2): кэш графов источника + пороги мастерства
    knowledge_graph_dir: str = Field(
        default="data/knowledge_graphs", description="Каталог JSON-кэша графов источника"
    )
    graph_schema_version: int = Field(
        default=1, description="Версия схемы графа — входит в ключ кэша (пересборка)"
    )
    ontology_max_vertices: int = Field(
        default=14, ge=1, le=50, description="Макс. вершин LLM-онтологии"
    )
    mastery_mastered_threshold: float = Field(
        default=0.8, ge=0.0, le=1.0, description="Порог mastery для статуса mastered"
    )
    mastery_mastered_attempts: int = Field(
        default=3, ge=1, description="Мин. попыток для статуса mastered"
    )

    @property
    def resolved_knowledge_graph_dir(self) -> str:
        """Абсолютный путь к кэшу графов: относительно adaptive_tutor/ как student_db_path."""
        import os
        if os.path.isabs(self.knowledge_graph_dir):
            return self.knowledge_graph_dir
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, self.knowledge_graph_dir)

    # Адаптивное обучение: интервальные повторения (SM-2)
    review_enabled: bool = Field(
        default=True, description="Включить карточки для повторений (SM-2)"
    )
    review_quiz_size: int = Field(
        default=5, ge=1, le=20, description="Размер блица повторения (кол-во due-карточек)"
    )
    review_bank_max_cards: int = Field(
        default=200, ge=1, description="Максимум карточек повторения на ученика"
    )

    # Безопасность
    max_llm_calls_per_session: int = Field(default=50, description="Макс. LLM-вызовов за сессию")
    max_cost_per_session_usd: float = Field(default=1.0, description="Макс. стоимость сессии (USD)")
    circuit_breaker_threshold: int = Field(default=3, description="Порог circuit breaker")
    circuit_breaker_timeout: int = Field(default=30, description="Таймаут circuit breaker (сек)")
    
    # Агент
    max_agent_steps: int = Field(default=6, description="Макс. шагов агента")
    max_agent_time_sec: int = Field(default=150, description="Макс. время агента (сек)")
    
    # Логирование
    log_level: str = Field(default="INFO", description="Уровень логирования")
    log_file: str = Field(default="logs/agent.jsonl", description="Файл для JSON-логов")
    
    model_config = {"env_file": ".env", "env_prefix": "TUTOR_"}


settings = Settings()
