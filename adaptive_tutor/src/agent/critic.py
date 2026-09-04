"""Критик (judge model): проверяет ответ на качество перед выдачей ученику."""

import json
import re
from dataclasses import dataclass, field

from ..llm.base import LLMClient

CRITIC_SYSTEM_PROMPT = """Ты — строгий проверяющий качества ответов в системе адаптивного обучения.
Проанализируй ответ и проверь:

1. LaTeX-разметка: все долларовые подписи ($…$ и $$…$$) должны быть
   сбалансированы (чётное кол-во разделителей).
2. Галлюцинации: нет ли в ответе необоснованных фактов, выдуманных формул
   или утверждений, которые невозможно проверить.
3. Структура: ответ должен быть непустым и осмысленным.

Верни строго JSON (без комментариев и markdown-обёрток):
{
  "passed": true/false,
  "issues": ["описание проблемы 1", ...],
  "corrected_answer": "исправленный ответ" или null
}

Если ответ проходит все проверки — "passed": true, "issues": [], "corrected_answer": null.
Если есть проблемы — "passed": false, перечисли проблемы в "issues".
Если можешь исправить ответ — заполни "corrected_answer", иначе null."""


@dataclass
class CriticResult:
    """Результат проверки критиком."""
    passed: bool
    issues: list[str] = field(default_factory=list)
    corrected_answer: str | None = None


class Critic:
    """Валидатор ответа через judge model."""

    def __init__(self, llm: LLMClient, model: str):
        self.llm = llm
        self.model = model

    async def validate(self, answer: str, rag_context: list | None = None) -> CriticResult:
        if not answer or not answer.strip():
            return CriticResult(passed=False, issues=["пустой ответ"])

        latex_issues = _check_latex(answer)
        if latex_issues:
            return CriticResult(passed=False, issues=latex_issues)

        try:
            messages = _build_messages(answer, rag_context)
            resp = await self.llm.chat(
                messages=messages,
                model=self.model,
                temperature=0.0,
                max_tokens=512,
            )
            return _parse_critic_response(resp.content)
        except Exception:  # noqa: BLE001
            return CriticResult(passed=True)


def _check_latex(text: str) -> list[str]:
    issues = []
    dd_count = text.count("$$")
    if dd_count % 2 != 0:
        issues.append(f"нечётное кол-во $$: {dd_count}")
    single_dollars = re.findall(r'(?<!\$)\$(?!\$)', text)
    if len(single_dollars) % 2 != 0:
        issues.append(f"нечётное кол-во $: {len(single_dollars)}")
    return issues


def _build_messages(answer: str, rag_context: list | None = None) -> list[dict]:
    content = f"Ответ для проверки:\n\n{answer}"
    if rag_context:
        ctx_text = "\n".join(
            str(item) for item in rag_context
        )
        content += f"\n\nKонтекст из базы знаний (для проверки фактов):\n\n{ctx_text}"
    return [
        {"role": "system", "content": CRITIC_SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


def _parse_critic_response(content: str) -> CriticResult:
    cleaned = content.strip()
    match = re.search(r"```json\s*(.*?)\s*```", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(1).strip()
    try:
        data = json.loads(cleaned)
        return CriticResult(
            passed=bool(data.get("passed", True)),
            issues=list(data.get("issues", [])),
            corrected_answer=data.get("corrected_answer"),
        )
    except (json.JSONDecodeError, AttributeError):
        return CriticResult(passed=True)
