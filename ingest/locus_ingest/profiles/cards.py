"""Datasheet cards (landscape pages): stat line, weapon tables, abilities, keywords."""
from __future__ import annotations

import re

from ..flow import line_md, render
from ..model import Line, Unit

_STAT_LABELS = ("M", "T", "SV", "W", "LD", "OC")
_STAT_VALUE = re.compile(r'^[\dD+"\-*/.N]+$')
_WEAPON_HEAD = re.compile(r"^(RANGED|MELEE) WEAPONS?$")
_COLS = ("RANGE", "A", "BS", "WS", "S", "AP", "D")


def _white(ln: Line) -> bool:
    return all(s.color == 0xFFFFFF for s in ln.spans)


def _is_header(ln: Line) -> bool:
    t = ln.text
    return _white(ln) and ln.all_bold and 6.5 <= ln.size <= 10 and t.upper() == t and any(c.isalpha() for c in t)


def parse_card(lines: list[Line], width: float, height: float) -> dict:
    """Parse one card page into {name, lines (flow), tables (markdown), keywords, ...}."""
    lines = [l for l in lines if not l.rotated]
    used: set[int] = set()
    split = 0.6 * width
    out: dict = {"name": None, "stats": "", "weapons": "", "keywords": [], "faction_keywords": [], "flow": [], "warn": False}

    # Name: the largest text in the top band.
    top = [l for l in lines if l.y0 < 0.14 * height and len(l.text) > 2 and l.text not in _STAT_LABELS and not _STAT_VALUE.match(l.text)]
    if top:
        name = max(top, key=lambda l: (l.size, -l.y0))
        if name.size >= 11:
            out["name"] = name.text
            used.add(id(name))

    # Stat line: label row, then one row of values per model profile.
    labels = sorted((l for l in lines if l.text in _STAT_LABELS and l.y0 < 0.3 * height), key=lambda l: l.x0)
    if labels:
        seen: list[Line] = []
        for l in labels:
            used.add(id(l))
            if not any(abs(l.x0 - s.x0) < 6 for s in seen):
                seen.append(l)
        label_names = [l.text for l in seen]
        right_edge = max(l.x1 for l in seen) + 22
        first_header_y = min((l.y0 for l in lines if _is_header(l) and l.text not in _STAT_LABELS and l.text != "INVULNERABLE SAVE" and not l.text.startswith("INVULNERABLE")), default=height)
        vals = [l for l in lines if _STAT_VALUE.match(l.text) and l.size >= 9.5 and l.x0 < right_edge and l.y0 < first_header_y and id(l) not in used]
        rows: list[list[Line]] = []
        for v in sorted(vals, key=lambda l: l.y0):
            if rows and abs(v.y0 - rows[-1][0].y0) < 6:
                rows[-1].append(v)
            else:
                rows.append([v])
        rows = [r for r in rows if len(r) >= len(label_names) - 1]
        if rows:
            md = ["| Model | " + " | ".join(label_names) + " |", "|" + "---|" * (len(label_names) + 1)]
            for r in rows:
                r.sort(key=lambda l: l.x0)
                for v in r:
                    used.add(id(v))
                y = r[0].y0
                label = [l for l in lines if id(l) not in used and abs(l.y0 - y) < 9 and l.x0 > right_edge - 10 and l.size < 12 and l.y0 < first_header_y and l.text != "INVULNERABLE SAVE"]
                model = label[0].text if label and len(rows) > 1 else (out["name"] or "").title()
                if label and len(rows) > 1:
                    used.add(id(label[0]))
                md.append("| " + model + " | " + " | ".join(v.text for v in r[: len(label_names)]) + " |")
            out["stats"] = "\n".join(md)
        else:
            out["warn"] = True

    # Invulnerable save value sits beside its label.
    for l in lines:
        if l.text.startswith("INVULNERABLE SAVE"):
            used.add(id(l))
            near = [v for v in lines if id(v) not in used and re.match(r"^\d\+\*?$", v.text) and abs(v.y0 - l.y0) < 12 and abs(v.x0 - l.x0) < 80]
            if near:
                used.add(id(near[0]))
                out["invuln"] = near[0].text + ("*" if l.text.endswith("*") else "")

    # Weapon tables: a white header row, then rows of six cells with the name on the left.
    heads = sorted((l for l in lines if _WEAPON_HEAD.match(l.text) and _white(l)), key=lambda l: l.y0)
    col_heads = [l for l in lines if l.text in _COLS and _white(l)]
    for l in col_heads:
        used.add(id(l))
    section_ys = sorted(l.y0 for l in lines if _is_header(l) and l.x0 < split and l.text not in _COLS)
    tables: list[str] = []
    for h in heads:
        used.add(id(h))
        cols = sorted((c for c in col_heads if abs(c.y0 - h.y0) < 4), key=lambda l: l.x0)
        if len(cols) < 5:
            out["warn"] = True
            continue
        cell_x = cols[0].x0 - 12
        y_end = min((y for y in section_ys if y > h.y0 + 3), default=0.86 * height)
        body = [l for l in lines if h.y0 + 3 < l.y0 < y_end - 1 and l.x0 < split - 4 and id(l) not in used and l.size <= 7.2]
        cells = sorted((l for l in body if l.x0 >= cell_x), key=lambda l: l.y0)
        names = sorted((l for l in body if l.x0 < cell_x), key=lambda l: l.y0)
        rows = []
        for c in cells:
            if rows and abs(c.y0 - rows[-1][0].y0) < 3.5:
                rows[-1].append(c)
            else:
                rows.append([c])
        rows = [r for r in rows if len(r) >= 4]
        if not rows:
            continue
        row_names: list[list[Line]] = [[] for _ in rows]
        leftovers: list[Line] = []
        for n in names:
            k = min(range(len(rows)), key=lambda i: abs(rows[i][0].y0 - n.y0))
            if abs(rows[k][0].y0 - n.y0) <= 9.5:
                row_names[k].append(n)
            else:
                leftovers.append(n)
        md = ["| " + h.text.title() + " | " + " | ".join(c.text for c in cols) + " |", "|" + "---|" * (len(cols) + 1)]
        for r, ns in zip(rows, row_names):
            r.sort(key=lambda l: l.x0)
            for x in r + ns:
                used.add(id(x))
            name = " ".join(line_md(n).replace("**", "") for n in sorted(ns, key=lambda l: (l.y0, l.x0)))
            md.append("| " + (name or "?") + " | " + " | ".join(c.text for c in r) + " |")
            if not name or len(r) != len(cols):
                out["warn"] = True
        tables.append("\n".join(md))
    out["weapons"] = "\n\n".join(tables)

    # Keywords footer.
    for l in sorted(lines, key=lambda l: l.y0):
        t = l.text
        if t.upper().startswith("FACTION KEYWORDS"):
            used.add(id(l))
            rest = t.split(":", 1)[1].strip() if ":" in t else ""
            below = [b for b in lines if id(b) not in used and 0 < b.y0 - l.y0 < 16 and abs(b.x0 - l.x0) < 30]
            for b in below:
                used.add(id(b))
                rest += " " + b.text
            out["faction_keywords"] = [k.strip().title() for k in re.split(r"[,;]", rest) if k.strip()]
        elif t.upper().startswith("KEYWORDS"):
            used.add(id(l))
            rest = t.split(":", 1)[1] if ":" in t else ""
            below = [b for b in lines if id(b) not in used and 0 < b.y0 - l.y0 < 12 and abs(b.x0 - l.x0) < 40 and b.x0 < split]
            for b in below:
                used.add(id(b))
                rest += " " + b.text
            out["keywords"] = [k.strip().title() for k in re.split(r"[,;]", rest.replace("–", ",")) if k.strip()]

    # Everything else flows: left region, then right region; white banners become headings.
    rest = [l for l in lines if id(l) not in used and len(l.text) > 0 and not (l.text.isdigit() and l.size < 7)]
    for l in rest:
        l.col = 0 if l.x0 < split - 4 else 1
    rest.sort(key=lambda l: (l.col, round(l.y0 / 3), l.x0))
    flow: list[Line] = []
    skip: set[int] = set()
    for i, l in enumerate(rest):
        if id(l) in skip:
            continue
        if _is_header(l):
            same = [o for o in rest[i + 1:] if o.col == l.col and abs(o.y0 - l.y0) < 4 and o.x0 > l.x1 and not _is_header(o)]
            if same:  # inline label + value, e.g. "CORE ABILITIES  Stealth"
                for o in same:
                    skip.add(id(o))
                text = f"**{l.text.title()}:** " + " ".join(o.text for o in same)
            else:
                text = "### " + l.text.title()
            flow.append(Line(l.page, l.x0, l.y0, l.x1, l.y1, l.spans, col=l.col, text_override=text))
        else:
            flow.append(l)
    out["flow"] = flow
    return out


def build_unit(parts: list[dict], unit: Unit) -> None:
    """Merge the front and back pages of one datasheet into the unit body."""
    blocks: list[str] = []
    stats = next((p["stats"] for p in parts if p["stats"]), "")
    if stats:
        blocks.append("### Characteristics\n\n" + stats)
    invuln = next((p["invuln"] for p in parts if p.get("invuln")), None)
    if invuln:
        blocks.append(f"**Invulnerable save:** {invuln}")
    for p in parts:
        if p["weapons"]:
            blocks.append("### Weapons\n\n" + p["weapons"])
    for p in parts:
        text = render(p["flow"], unit)
        if text:
            blocks.append(text)
    kws = next((p["keywords"] for p in parts if p["keywords"]), [])
    fkws = next((p["faction_keywords"] for p in parts if p["faction_keywords"]), [])
    if kws:
        blocks.append("**Keywords:** " + ", ".join(kws))
        unit.keywords.update(k.upper() for k in kws)
    if fkws:
        blocks.append("**Faction keywords:** " + ", ".join(fkws))
    unit.meta["unit_keywords"] = kws
    unit.meta["faction_keywords"] = fkws
    unit.low_confidence = any(p["warn"] for p in parts) or not stats
    unit.body = "\n\n".join(blocks)
