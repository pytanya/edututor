"""Pydantic-модели для агентного цикла."""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

try:
    from langgraph.graph.message import AnyMessage
except ImportError:  # pragma: no cover
    AnyMessage = dict  # type: ignore


class RAGQuery(BaseModel):
    """Семантический запрос для векторного поиска."""
    query: str = Field(description="Семантический запрос к базе знаний")
    top_k: int = Field(5, ge=1, le=20, description="Кол-во результатов")
    filters: dict[str, Any] = Field(default_factory=dict, description="Фильтры (topic, grade, ...)")


class WebSearchQuery(BaseModel):
    """Запрос для веб-поиска."""
    query: str = Field(description="Поисковый запрос")
    max_results: int = Field(5, ge=1, le=10, description="Кол-во результатов")
    language: Literal["ru", "en"] = Field("ru", description="Язык")


class ToolCall(BaseModel):
    """Представление вызова инструмента от модели."""
    name: str
    arguments: dict[str, Any]


class AgentStep(BaseModel):
    """Шаг агентного цикла для наблюдаемости."""
    action: str
    reason_summary: str
    model: str
    tool: str | None = None
    arguments: dict[str, Any] | None = None
    status: Literal["ok", "error", "retry", "terminate"] = "ok"
    duration_ms: int = 0


class LearningStyle(StrEnum):
    """Стиль обучения ученика."""
    VISUAL = "visual"
    AUDITORY = "auditory"
    KINESTHETIC = "kinesthetic"
    READING = "reading"


class ContentType(StrEnum):
    """Тип образовательного контента в конверте ответа агента."""
    THEORY = "theory"
    PRACTICE = "practice"
    HINT = "hint"
    QUIZ = "quiz"
    EVALUATION = "evaluation"


class ContentEnvelope(BaseModel):
    """Типизированный ответ агента (Подход A, конверт в цикле)."""
    v: int = 1
    type: ContentType
    text: str = Field(description="Markdown с формулами $...$ / $$...$$")
    payload: dict[str, Any] = Field(default_factory=dict)
    difficulty: Literal["easy", "medium", "hard"] = "medium"


class AgentGraphState(BaseModel):
    """Состояние графа агента (LangGraph-состояние)."""
    session_id: str = ""
    messages: list[dict[str, str]] = Field(default_factory=list)
    current_model: str = "planner"
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tools_result: dict[str, Any] = Field(default_factory=dict)
    rag_context: list[Any] = Field(default_factory=list)
    web_results: list[Any] = Field(default_factory=list)
    student_profile: dict[str, Any] = Field(default_factory=dict)
    final_answer: str | None = None
    content_envelope: ContentEnvelope | None = None
    content_envelopes: list[ContentEnvelope] = Field(default_factory=list)
    needs_scaffold: bool = False
    current_knowledge_level: float = Field(0.5, ge=0.0, le=1.0)
    learning_style: LearningStyle = Field(LearningStyle.READING)
    fatigue_level: float = Field(0.0, ge=0.0, le=1.0)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    steps: list[AgentStep] = Field(default_factory=list)
    terminated: bool = False
    error: str | None = None