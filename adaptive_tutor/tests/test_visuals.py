"""Тесты для модуля agent.visuals — SVG-санитизация и валидация visuals."""

import pytest

from src.agent.visuals import (
    is_svg_safe,
    is_valid_mermaid,
    sanitize_mermaid_latex,
    sanitize_svg,
    validate_visuals,
)

# === SVG sanitization ===

class TestSanitizeSvg:
    def test_removes_script_tags(self):
        svg = '<svg><script>alert(1)</script><circle r="5"/></svg>'
        result = sanitize_svg(svg)
        assert "<script" not in result
        assert '<circle r="5"/>' in result

    def test_removes_self_closing_script(self):
        svg = '<svg><script src="evil.js" /><rect width="10" height="10"/></svg>'
        result = sanitize_svg(svg)
        assert "<script" not in result
        assert "<rect" in result

    def test_removes_event_handlers(self):
        svg = '<svg><circle r="5" onclick="alert(1)" /><rect onload="x()" /></svg>'
        result = sanitize_svg(svg)
        assert "onclick" not in result
        assert "onload" not in result

    def test_removes_javascript_urls(self):
        svg = '<svg><a href="javascript:alert(1)"><circle r="5"/></a></svg>'
        result = sanitize_svg(svg)
        assert "javascript:" not in result

    def test_removes_data_urls(self):
        svg = '<svg><image src="data:text/html,<script>x</script>" /></svg>'
        result = sanitize_svg(svg)
        assert "data:" not in result

    def test_preserves_safe_svg(self):
        svg = '<svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="40" fill="blue"/></svg>'
        result = sanitize_svg(svg)
        assert result == svg

    def test_removes_iframe(self):
        svg = '<svg><foreignObject><iframe src="evil.html"></iframe></foreignObject></svg>'
        result = sanitize_svg(svg)
        assert "<iframe" not in result

    def test_removes_embed(self):
        svg = '<svg><embed src="evil.swf" /></svg>'
        result = sanitize_svg(svg)
        assert "<embed" not in result

    def test_removes_xml_pi(self):
        svg = '<?xml version="1.0"?><svg><circle r="5"/></svg>'
        result = sanitize_svg(svg)
        assert "<?xml" not in result
        assert "<circle" in result


class TestIsSvgSafe:
    def test_safe_svg_returns_true(self):
        assert is_svg_safe('<svg><circle r="5"/></svg>')

    def test_script_returns_false(self):
        assert not is_svg_safe('<svg><script>x</script></svg>')

    def test_event_handler_returns_false(self):
        assert not is_svg_safe('<svg onclick="x"><circle/></svg>')


# === Mermaid validation ===

class TestIsValidMermaid:
    @pytest.mark.parametrize("code", [
        "graph TD\n  A-->B",
        "flowchart LR\n  Start-->End",
        "sequenceDiagram\n  A->>B: Hello",
        "pie\n  title Test\n  \"A\": 50\n  \"B\": 50",
        "timeline\n  2020: event",
        "mindmap\n  root((Central))",
        "classDiagram\n  Animal <|-- Duck",
        "stateDiagram\n  [*] --> Active",
        "erDiagram\n  CUSTOMER ||--o{ ORDER : places",
        "gantt\n  section A\n  task1: 2024-01-01, 30d",
    ])
    def test_valid_mermaid(self, code):
        assert is_valid_mermaid(code)

    @pytest.mark.parametrize("code", [
        "",
        "hello world",
        "SELECT * FROM table",
        "function foo() {}",
        "  ",
    ])
    def test_invalid_mermaid(self, code):
        assert not is_valid_mermaid(code)


# === visuals validation ===

class TestValidateVisuals:
    def test_none_returns_empty(self):
        assert validate_visuals(None) == []

    def test_empty_list_returns_empty(self):
        assert validate_visuals([]) == []

    def test_non_list_returns_empty(self):
        assert validate_visuals("not a list") == []

    def test_valid_mermaid(self):
        visuals = [{"kind": "mermaid", "code": "graph TD\n  A-->B", "caption": "Test"}]
        result = validate_visuals(visuals)
        assert len(result) == 1
        assert result[0]["kind"] == "mermaid"
        assert result[0]["caption"] == "Test"

    def test_invalid_mermaid_rejected(self):
        visuals = [{"kind": "mermaid", "code": "not valid mermaid"}]
        assert validate_visuals(visuals) == []

    def test_empty_mermaid_rejected(self):
        visuals = [{"kind": "mermaid", "code": ""}]
        assert validate_visuals(visuals) == []

    def test_valid_function_plot(self):
        visuals = [{"kind": "function_plot", "expressions": ["sin(x)"], "x_range": [-6, 6]}]
        result = validate_visuals(visuals)
        assert len(result) == 1
        assert result[0]["expressions"] == ["sin(x)"]

    def test_empty_expressions_rejected(self):
        visuals = [{"kind": "function_plot", "expressions": []}]
        assert validate_visuals(visuals) == []

    def test_valid_svg(self):
        visuals = [{"kind": "svg", "code": '<svg><circle r="5"/></svg>'}]
        result = validate_visuals(visuals)
        assert len(result) == 1

    def test_svg_sanitized(self):
        visuals = [{"kind": "svg", "code": '<svg><script>evil</script><circle r="5"/></svg>'}]
        result = validate_visuals(visuals)
        assert len(result) == 1
        assert "<script" not in result[0]["code"]

    def test_empty_svg_rejected(self):
        visuals = [{"kind": "svg", "code": ""}]
        assert validate_visuals(visuals) == []

    def test_valid_chart(self):
        visuals = [{"kind": "chart", "data": [10, 20, 30], "labels": ["A", "B", "C"]}]
        result = validate_visuals(visuals)
        assert len(result) == 1

    def test_empty_chart_data_rejected(self):
        visuals = [{"kind": "chart", "data": []}]
        assert validate_visuals(visuals) == []

    def test_unknown_kind_rejected(self):
        visuals = [{"kind": "unknown", "code": "test"}]
        assert validate_visuals(visuals) == []

    def test_non_dict_items_rejected(self):
        visuals = ["not a dict", 42, None]
        assert validate_visuals(visuals) == []

    def test_mixed_valid_invalid(self):
        visuals = [
            {"kind": "mermaid", "code": "graph TD\n  A-->B"},
            {"kind": "unknown", "code": "bad"},
            {"kind": "function_plot", "expressions": ["x^2"]},
            {"kind": "mermaid", "code": ""},  # empty = rejected
        ]
        result = validate_visuals(visuals)
        assert len(result) == 2
        assert result[0]["kind"] == "mermaid"
        assert result[1]["kind"] == "function_plot"


# === Mermaid LaTeX sanitization ===

class TestSanitizeMermaidLatex:
    def test_replaces_alpha(self):
        assert sanitize_mermaid_latex(r"$\alpha$-излучение") == "α-излучение"

    def test_replaces_beta_gamma(self):
        code = r"A[$\beta$-распад] --> B[$\gamma$-лучи]"
        result = sanitize_mermaid_latex(code)
        assert "β-распад" in result
        assert "γ-лучи" in result
        assert "$" not in result

    def test_replaces_multiple_symbols(self):
        code = r"$\alpha$ + $\beta$ = $\gamma$"
        result = sanitize_mermaid_latex(code)
        assert result == "α + β = γ"

    def test_no_latex_unchanged(self):
        code = "graph TD\n  A-->B"
        assert sanitize_mermaid_latex(code) == code

    def test_operators(self):
        assert "≤" in sanitize_mermaid_latex(r"$\leq$")
        assert "≥" in sanitize_mermaid_latex(r"$\geq$")
        assert "≠" in sanitize_mermaid_latex(r"$\neq$")
        assert "±" in sanitize_mermaid_latex(r"$\pm$")
        assert "→" in sanitize_mermaid_latex(r"$\rightarrow$")

    def test_removes_unknown_commands(self):
        result = sanitize_mermaid_latex(r"$\text{hello}$")
        assert "\\" not in result
        assert "hello" in result

    def test_removes_braces(self):
        result = sanitize_mermaid_latex(r"$\mathrm{Fe}$")
        assert "{" not in result
        assert "}" not in result

    def test_superscripts(self):
        assert "²" in sanitize_mermaid_latex("$x^2$")
        assert "³" in sanitize_mermaid_latex("$x^3$")

    def test_validate_visuals_sanitizes_mermaid(self):
        """validate_visuals должен конвертировать LaTeX в Unicode для Mermaid."""
        visuals = [{"kind": "mermaid", "code": "graph TD\n  A[$\\alpha$]-->B"}]
        result = validate_visuals(visuals)
        assert len(result) == 1
        assert "α" in result[0]["code"]
        assert "$" not in result[0]["code"]

    def test_double_backslash_normalized(self):
        """Двойной бэкслеш (из JSON-экранирования) не должен оставлять лишний \\."""
        # Simulates $\\beta$ coming from JSON where \\ was double-escaped
        result = sanitize_mermaid_latex("$\\\\beta$-излучение")
        assert result == "β-излучение"
        assert "\\" not in result

    def test_double_backslash_multiple(self):
        code = "A[$\\\\alpha$] --> B[$\\\\gamma$]"
        result = sanitize_mermaid_latex(code)
        assert "α" in result
        assert "γ" in result
        assert "\\" not in result
        assert "$" not in result

    def test_no_stray_backslash(self):
        """После замены не должно оставаться одиноких бэкслешей."""
        result = sanitize_mermaid_latex(r"$\beta$-излучение")
        assert "\\" not in result
        assert result == "β-излучение"
