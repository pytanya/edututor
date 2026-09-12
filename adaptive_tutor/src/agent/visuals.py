"""Утилиты санитизации визуальных элементов (SVG, Mermaid) в конвертах.

SVG от LLM может содержать XSS-векторы: <script>, onload, javascript: URL.
Mermaid-код проверяется на базовую синтаксическую корректность (не пустой,
начинается с ключевого слова диаграммы).
"""

import re
from typing import Any

# --- SVG sanitization ------------------------------------------------

# Теги, запрещённые внутри SVG (XSS-векторы)
_DANGEROUS_TAGS_RE = re.compile(
    r"<\s*(script|iframe|object|embed|applet|form|input|button|textarea|select|meta|link|base|style)\b",
    re.IGNORECASE,
)

# Атрибуты-обработчики событий (onclick, onload, onerror, ...)
_EVENT_HANDLER_RE = re.compile(r"\bon\w+\s*=", re.IGNORECASE)

# javascript:/data: URL в атрибутах href/src/xlink:href
_DANGEROUS_URL_RE = re.compile(
    r"""(href|src|xlink:href)\s*=\s*["']?\s*(javascript|data|vbscript)\s*:""",
    re.IGNORECASE,
)

# XML-PI <?...?> — не нужны, могут быть использованы для внедрения
_XML_PI_RE = re.compile(r"<\?.*?\?>", re.DOTALL)


def sanitize_svg(svg: str) -> str:
    """Удаляет опасные конструкции из SVG-строки.

    Не pretends быть полноценным парсером — это быстрая regex-зачистка
    наиболее частых XSS-векторов. Для полной защиты на фронтенде
    дополнительно используется DOMPurify.

    >>> sanitize_svg('<svg><script>alert(1)</script></svg>')
    '<svg></svg>'
    """
    result = svg
    # 1. Удаляем запрещённые теги целиком (и самозакрывающиеся, и парные)
    for tag_match in _DANGEROUS_TAGS_RE.finditer(svg):
        tag_name = tag_match.group(1)
        # Парный тег
        pair_re = re.compile(
            rf"<\s*{tag_name}\b[^>]*>.*?</\s*{tag_name}\s*>",
            re.IGNORECASE | re.DOTALL,
        )
        result = pair_re.sub("", result)
        # Самозакрывающийся
        self_re = re.compile(
            rf"<\s*{tag_name}\b[^>]*/\s*>", re.IGNORECASE
        )
        result = self_re.sub("", result)
    # 2. Удаляем event-handler атрибуты (onclick="...", onload="..." и т.д.)
    result = _EVENT_HANDLER_RE.sub("", result)
    # 3. Удаляем опасные URL-схемы
    result = _DANGEROUS_URL_RE.sub(r'\1=""', result)
    # 4. Удаляем XML PI
    result = _XML_PI_RE.sub("", result)
    return result.strip()


def is_svg_safe(svg: str) -> bool:
    """Проверяет, что SVG не содержит известных XSS-конструкций."""
    return (
        not _DANGEROUS_TAGS_RE.search(svg)
        and not _EVENT_HANDLER_RE.search(svg)
        and not _DANGEROUS_URL_RE.search(svg)
    )


# --- Mermaid validation -----------------------------------------------

_MERMAID_KEYWORDS = frozenset({
    "graph", "flowchart", "sequenceDiagram", "classDiagram",
    "stateDiagram", "erDiagram", "gantt", "pie", "journey",
    "gitGraph", "mindmap", "timeline", "sankey", "xychart",
    "quadrantChart", "block", "C4Context",
})


def is_valid_mermaid(code: str) -> bool:
    """Проверяет, что Mermaid-код начинается с допустимого ключевого слова."""
    first_line = (code or "").strip().split("\n", 1)[0].strip()
    first_word = first_line.split(None, 1)[0] if first_line else ""
    # Убираем возможные суффиксы (graph TD, flowchart LR, ...)
    return first_word.rstrip(":") in _MERMAID_KEYWORDS


# LaTeX → Unicode: LLM иногда вставляет $\alpha$ внутри Mermaid-нод.
_LATEX_UNICODE: dict[str, str] = {
    r"\alpha": "α", r"\beta": "β", r"\gamma": "γ", r"\delta": "δ",
    r"\epsilon": "ε", r"\zeta": "ζ", r"\eta": "η", r"\theta": "θ",
    r"\lambda": "λ", r"\mu": "μ", r"\nu": "ν", r"\pi": "π",
    r"\rho": "ρ", r"\sigma": "σ", r"\tau": "τ", r"\phi": "φ",
    r"\psi": "ψ", r"\omega": "ω",
    r"\Alpha": "Α", r"\Beta": "Β", r"\Gamma": "Γ", r"\Delta": "Δ",
    r"\Sigma": "Σ", r"\Omega": "Ω", r"\Pi": "Π", r"\Phi": "Φ",
    r"\Psi": "Ψ", r"\Lambda": "Λ", r"\Theta": "Θ",
    r"\infty": "∞", r"\approx": "≈", r"\neq": "≠", r"\ne": "≠",
    r"\leq": "≤", r"\le": "≤", r"\geq": "≥", r"\ge": "≥",
    r"\pm": "±", r"\times": "×", r"\cdot": "·",
    r"\rightarrow": "→", r"\leftarrow": "←", r"\leftrightarrow": "↔",
    r"\Rightarrow": "⇒", r"\Leftarrow": "⇐",
    r"\sqrt": "\u221A", r"\partial": "\u2202", r"\nabla": "\u2207",
    "^2": "\u00B2", "^3": "\u00B3",
}

_LATEX_INLINE_RE = re.compile(r"\$([^$]+)\$")


def _replace_latex_match(m: re.Match) -> str:
    """Заменяет содержимое $...$ на Unicode-эквиваленты."""
    inner = m.group(1)
    # Нормализуем двойные бэкслеши (из JSON-экранирования: \\beta → \beta)
    inner = inner.replace("\\\\", "\\")
    for latex, uni in _LATEX_UNICODE.items():
        inner = inner.replace(latex, uni)
    # Убираем оставшиеся \команды и фигурные скобки
    inner = re.sub(r"\\[a-zA-Z]+", "", inner)
    inner = inner.replace("{", "").replace("}", "")
    # Убираем одинокие бэкслеши, оставшиеся после замен
    inner = inner.replace("\\", "")
    return inner


def sanitize_mermaid_latex(code: str) -> str:
    """Заменяет LaTeX-плейсхолдеры ($\\alpha$ и т.п.) на Unicode в Mermaid-коде."""
    return _LATEX_INLINE_RE.sub(_replace_latex_match, code)


# --- visuals validation -----------------------------------------------

_VALID_KINDS = frozenset({"mermaid", "function_plot", "svg", "chart"})


def validate_visuals(visuals: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Валидирует и санитизирует список visuals из payload конверта.

    - Отбрасывает элементы с неизвестным kind.
    - SVG-элементы санитизируются.
    - Mermaid-код проверяется на базовую корректность.
    - Пустые/невалидные элементы пропускаются.
    """
    if not visuals or not isinstance(visuals, list):
        return []
    result: list[dict[str, Any]] = []
    for item in visuals:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind", "")
        if kind not in _VALID_KINDS:
            continue

        if kind == "svg":
            code = item.get("code", "")
            if not code or not isinstance(code, str):
                continue
            item = {**item, "code": sanitize_svg(code)}
        elif kind == "mermaid":
            code = item.get("code", "")
            if not code or not isinstance(code, str) or not is_valid_mermaid(code):
                continue
            item = {**item, "code": sanitize_mermaid_latex(code)}
        elif kind == "function_plot":
            exprs = item.get("expressions", [])
            if not exprs or not isinstance(exprs, list):
                continue
        elif kind == "chart":
            data = item.get("data", [])
            if not data or not isinstance(data, list):
                continue

        result.append(item)
    return result
