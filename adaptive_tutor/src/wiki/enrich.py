"""Wiki-LLM: ленивое обогащение тела статьи «конспектом» из RAG-фрагментов.

Роль fast, temperature 0.3, max_tokens 400. Ошибки глотаются: при недоступном
LLM или пустом результате статья остаётся заглушкой (чат не падает).
"""

from __future__ import annotations

from typing import Any

from .models import now_iso

_SYSTEM = (
    "Ты — Wiki-LLM EduTutor. По фрагментам учебных материалов напиши краткий "
    "конспект темы (3-6 предложений): ключевые факты, термины, определения. "
    "Верни ТОЛЬКО текст конспекта, без заголовков и списков."
)

_MAX_CONTEXT_CHARS = 4000


def build_messages(topic: str, context: list[str]) -> list[dict[str, str]]:
    """Сообщения для LLM; контекст обрезается до _MAX_CONTEXT_CHARS."""
    chunks = [c for c in (context or []) if c and c.strip()]
    ctx = "\n---\n".join(chunks)[:_MAX_CONTEXT_CHARS]
    user = f"Тема: {topic}\nФрагменты материалов:\n{ctx}"
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]


async def enrich_body(
    wiki: Any,
    subject: str,
    topic: str,
    context: list[str],
    llm: Any,
    model: str = "",
) -> dict[str, Any] | None:
    """Генерирует/обновляет тело статьи. None — статья-заглушка или сбой."""
    art = wiki.get(subject, topic)
    if art is None or len((art.body or "").strip()) > 20:
        return None
    chunks = [c for c in (context or []) if c and c.strip()]
    if not chunks or llm is None:
        return None
    try:
        resp = await llm.chat(
            messages=build_messages(topic, chunks),
            model=model or "fast",
            temperature=0.3,
            max_tokens=400,
        )
        content = resp.content if resp is not None else None
        body = (content or "").strip().strip(" \n\"'`")
        if len(body) <= 20:
            return None
        art.body = body
        art.last_studied = now_iso()
        wiki.upsert(art)
        return art.to_dict()
    except Exception:
        return None  # best-effort: ошибки обогащения не роняют поток
