"""Грейдер ответов на review-карточку (без создания новой карточки)."""

import json


def _extract_single_choice(answer: str, prefix: str = "Ответ:") -> str:
    a = (answer or "").strip()
    if a.lower().startswith(prefix.lower()):
        a = a[len(prefix):].strip()
    return a


def _eq(a: str, b: str) -> bool:
    return (a or "").strip().lower() == (b or "").strip().lower()


def grade_deterministic(answer: str, correct_answer: str, options: list | None) -> bool:
    """Закрытый вопрос: выбор сравнивается с эталоном."""
    if not options:
        return False
    return any(_eq(_extract_single_choice(answer), opt) and _eq(opt, correct_answer)
               for opt in options)


async def grade_answer(llm, model: str, *, question: str, correct_answer: str,
                       answer: str, options: list | None = None) -> tuple[bool, str]:
    """Детерминированно для закрытых, LLM — для открытых."""
    if options:
        ok = grade_deterministic(answer, correct_answer, options)
        return ok, ("Верно!" if ok else "Ошибка")
    prompt = (
        "Ты — проверяющий ответов. Оцени ответ ученика на вопрос. "
        "Верни строго JSON: {\"correct\": true|false, \"feedback\": \"...\"}.\n"
        f"Вопрос: {question}\nЭталонный ответ: {correct_answer}\n"
        f"Ответ ученика: {answer}"
    )
    try:
        resp = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            model=model, temperature=0.0, max_tokens=300,
        )
        data = json.loads(resp.content)
        return bool(data.get("correct")), str(data.get("feedback") or "Ошибка")
    except Exception:  # noqa: BLE001
        return False, "Не удалось проверить ответ"
