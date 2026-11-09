"""Write units as Markdown files with YAML frontmatter."""
from __future__ import annotations

from pathlib import Path

import yaml

from .model import Unit

UPDATE_TYPES = ("faction_update", "universal_update")


def frontmatter(u: Unit, info: dict, game: str) -> dict:
    m = u.meta
    fm = {
        "id": u.id,
        "game": game,
        "doc_type": u.doc_type,
        "title": u.title,
        "section": m.get("section"),
        "phase": m.get("phase"),
        "faction": m.get("faction"),
        "detachment": m.get("detachment"),
        "source_file": info["source_file"],
        "document_title": info.get("document_title"),
        "document_version": info.get("document_version"),
        "printed_version": info.get("printed_version"),
        "effective_date": info.get("effective_date"),
        "page_start": u.page_start,
        "page_end": u.page_end,
        "is_update": u.doc_type in UPDATE_TYPES,
    }
    if u.doc_type in UPDATE_TYPES:
        fm.update({
            "replaces_rule_id": m.get("replaces_rule_id"),
            "replaces_rule_ids": m.get("replaces_rule_ids") or [],
            "update_mode": m.get("update_mode"),
            "update_target": m.get("update_target"),
            "changed_in_version": bool(m.get("changed_in_version")),
        })
    for key in ("rule_number", "cp_cost", "category", "update_group"):
        if m.get(key):
            fm[key] = m[key]
    if u.low_confidence:
        fm["extraction_confidence"] = "low"
    fm["keywords"] = sorted(u.keywords)[:40]
    return {k: v for k, v in fm.items() if v is not None or k in ("replaces_rule_id",)}


def markdown(u: Unit, info: dict, game: str) -> str:
    fm = yaml.safe_dump(frontmatter(u, info, game), sort_keys=False, allow_unicode=True, width=1000).strip()
    head = f"# {u.h1}\n\n## {u.title}"
    if u.meta.get("rule_number"):
        head += f" ({u.meta['rule_number']})"
    extra = []
    if u.doc_type == "stratagem":
        bits = [b for b in (u.meta.get("detachment"), u.meta.get("category"), u.meta.get("cp_cost")) if b]
        if bits:
            extra.append("*" + " · ".join(bits) + " stratagem*")
    if u.doc_type in UPDATE_TYPES:
        ver = info.get("printed_version")
        extra.append(f"*Rules update from {info.get('document_title')}" + (f" v{ver}" if ver else "")
                     + (f", legal from {info['effective_date']}" if info.get("effective_date") else "") + ".*")
    tail = []
    kws = sorted(u.keywords)
    terms = sorted(t for t in u.terms if len(t) < 40)
    if kws or terms:
        tail.append("### Keywords\n\n" + ", ".join(kws + terms[:30]))
    parts = [f"---\n{fm}\n---", head, *extra, u.body.strip(), *tail]
    return "\n\n".join(p for p in parts if p) + "\n"


def write_units(units: list[Unit], info: dict, game: str, out_dir: Path) -> list[str]:
    written = []
    for u in units:
        path = out_dir / u.rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown(u, info, game), encoding="utf-8", newline="\n")
        written.append(str(path))
    return written
