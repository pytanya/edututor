"""OKF-бандл: граф источника subject|grade как каталог markdown (спека §4.2).

Файлы OKF v0.2: index.md, topics/<slug(title)>.md (не-book узлы), log.md.
Мастерство (по title) и relations добавляются в frontmatter при наличии.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

import yaml

from ..wiki.store import slug

OKF_VERSION = "0.2"
GENERATOR = "edututor/0.1"


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _frontmatter(data: dict[str, Any]) -> str:
    return "---\n" + yaml.safe_dump(data, allow_unicode=True, sort_keys=False) + "---\n"


def _node_relations(node_id: str, edges: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{"target": e["target"], "relation": e["relation"]}
            for e in edges if e.get("source") == node_id]


def emit_okf_bundle(
    out_dir: Path,
    subject: str,
    grade: str = "",
    curriculum: str = "",
    graph: dict[str, Any] | None = None,
    mastery: dict[str, float] | None = None,
) -> Path:
    """Эмитит OKF-бандл в out_dir (создаётся). Возвращает out_dir."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    nodes = list((graph or {}).get("nodes", []))
    edges = list((graph or {}).get("edges", []))
    mastery = mastery or {}
    generated = _now()
    source_name = subject

    index_meta = {
        "okf_version": OKF_VERSION,
        "type": "Index",
        "title": f"Учебник «{source_name}»",
        "subject": subject or "",
        "grade": grade or "",
        "curriculum": curriculum or "",
        "status": "stable",
        "generated": {"by": GENERATOR, "at": generated},
    }
    body = (f"# Учебник «{source_name}»\n\n"
            f"Граф знаний: {len(nodes)} узлов, {len(edges)} рёбер.\n\nТемы:\n")
    for n in nodes:
        if n.get("type") == "book":
            continue
        title = n.get("title", "")
        body += f"- [«{title}»](topics/{slug(title)}.md)\n"
    body += "\nПроисхождение: граф источника (E2).\n"
    (out_dir / "index.md").write_text(_frontmatter(index_meta) + body, encoding="utf-8")

    log_meta = {"type": "ChangeLog", "title": "История изменений"}
    log = f"## {generated}\n- Сформирован OKF-бандл «{source_name}» ({GENERATOR}).\n"
    (out_dir / "log.md").write_text(_frontmatter(log_meta) + log, encoding="utf-8")

    topics_dir = out_dir / "topics"
    topics_dir.mkdir(exist_ok=True)
    for n in nodes:
        if n.get("type") == "book":
            continue
        title = n.get("title", "")
        meta: dict[str, Any] = {
            "type": "Topic" if n.get("type") == "topic" else "Section",
            "title": title,
            "subject": subject or "",
            "grade": grade or "",
            "status": "stable",
            "generated": {"by": GENERATOR, "at": generated},
        }
        if n.get("section_number"):
            meta["section_number"] = n["section_number"]
        if curriculum:
            meta["curriculum"] = curriculum
        rels = _node_relations(n.get("id", ""), edges)
        if rels:
            meta["relations"] = rels
        if title in mastery:
            meta["mastery"] = round(float(mastery[title]), 4)
        desc = f"{title}\n\nРаздел учебника «{source_name}»"
        if n.get("section_number"):
            desc += f", №{n['section_number']}"
        desc += ". Открыть и готовиться по теме: выбор узла в графе знаний.\n"
        (topics_dir / f"{slug(title)}.md").write_text(_frontmatter(meta) + desc, encoding="utf-8")
    return out_dir


def validate_bundle(bundle_dir: Path) -> dict[str, Any]:
    """Проверка конформизма: каждый *.md начинается с '---', валидный YAML, type."""
    bundle_dir = Path(bundle_dir)
    errors: list[str] = []
    files: list[str] = []
    for p in sorted(bundle_dir.rglob("*.md")):
        rel = str(p.relative_to(bundle_dir)).replace("\\", "/")
        files.append(rel)
        text = p.read_text(encoding="utf-8")
        if not text.startswith("---"):
            errors.append(f"{rel}: нет YAML-frontmatter")
            continue
        parts = text.split("---", 2)
        if len(parts) < 3:
            errors.append(f"{rel}: frontmatter не закрыт")
            continue
        try:
            data = yaml.safe_load(parts[1])
        except Exception as e:
            errors.append(f"{rel}: YAML невалиден: {e}")
            continue
        if not isinstance(data, dict) or not str(data.get("type", "")).strip():
            errors.append(f"{rel}: поле type пустое")
    return {"conformant": not errors, "errors": errors, "files": files}
