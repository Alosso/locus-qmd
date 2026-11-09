"""Core rulebook profile: numbered sections (01-24) whose rules carry printed ids like 18.04."""
from __future__ import annotations

import re

import pymupdf

from ..flow import render
from ..model import Line, Unit, slugify, smart_title
from ..pdftext import assign_columns, page_lines

_ID = re.compile(r"^\d\d\.\d\d$")
_TOC = re.compile(r"(\d\d)\.\s+(.+?)\s*\.{3,}\s*pg\s*(\d+)")
_TOC_PLAIN = re.compile(r"^([A-Z][A-Z ]+?)\s*\.{3,}\s*pg\s*(\d+)")
_GROUP = re.compile(r"^pg\s*(\d+)\s*-\s*(\d+)$")


def _toc(doc: pymupdf.Document) -> tuple[list[dict], list[dict]]:
    """Sections and section groups from the contents page."""
    sections: list[dict] = []
    groups: list[dict] = []
    for pno in range(min(4, len(doc))):
        all_lines = page_lines(doc[pno], pno + 1)
        if not any("CONTENTS" in l.text for l in all_lines):
            continue
        lines = sorted((l for l in all_lines if not l.rotated), key=lambda l: (round(l.y0), l.x0))
        last_big: str | None = None
        for ln in lines:
            t = ln.text
            for m in _TOC.finditer(t):
                sections.append({"num": m.group(1), "title": smart_title(m.group(2).strip()), "page": int(m.group(3))})
            m = _TOC_PLAIN.match(t)
            if m and not _TOC.search(t):
                sections.append({"num": slugify(m.group(1)), "title": smart_title(m.group(1)), "page": int(m.group(2))})
            g = _GROUP.match(t)
            if g and last_big:
                groups.append({"title": smart_title(last_big), "start": int(g.group(1)), "end": int(g.group(2))})
            if ln.size >= 16 and not g:
                last_big = t
    sections.sort(key=lambda s: s["page"])
    return sections, groups


def _is_footer(ln: Line) -> bool:
    t = ln.text
    return (
        ln.rotated
        or t.startswith("++")
        or t.startswith("►")
        or (ln.first.font.startswith("Akhand-Semilight") and t.isdigit())
        or (ln.first.font == "Akhand-Bold" and ln.size >= 36 and t.isdigit())
        or "Scan the code" in t
        or t.startswith(("with the Warhammer 40,000 app", "40,000 app."))
    )


def _rule_head(ln: Line) -> tuple[str, str] | None:
    f = ln.first
    if not ((f.font == "Akhand-Bold" and f.size >= 12) or (f.keyword and f.size >= 9)):
        return None
    for i, sp in enumerate(ln.spans):
        if i > 0 and sp.font.startswith("Akhand-Regular") and _ID.match(sp.text.strip()):
            title = " ".join(x.text.strip() for x in ln.spans[:i]).strip()
            return sp.text.strip(), re.sub(r"\s+", " ", title)
    return None


def _heading(ln: Line, level: int) -> Line:
    text = smart_title(ln.text)
    return Line(ln.page, ln.x0, ln.y0, ln.x1, ln.y1, ln.spans, col=ln.col, text_override="#" * level + " " + text)


def parse(doc: pymupdf.Document, src: dict, cfg: dict) -> list[Unit]:
    game = cfg["game"]
    sections, groups = _toc(doc)
    phases = set(cfg.get("phases", []))
    units: list[Unit] = []
    by_id: dict[str, Unit] = {}
    see_also: list[tuple[int, str, str]] = []  # (page, topic, rule id)
    cur: Unit | None = None
    cur_section: dict | None = None

    def section_for(page: int) -> dict | None:
        best = None
        for s in sections:
            if s["page"] <= page:
                best = s
        return best

    def group_for(page: int) -> str | None:
        for g in groups:
            if g["start"] <= page <= g["end"]:
                return g["title"]
        return None

    def new_unit(uid: str, title: str, page: int, doc_type: str, sec: dict) -> Unit:
        numbered = sec["num"].isdigit()
        folder = f"{sec['num']}-{slugify(sec['title'])}" if numbered else slugify(sec["title"])
        fname = f"{uid.replace('.', '-')}-{slugify(title)}" if _ID.match(uid) else slugify(uid.split(":")[-1])
        first_word = sec["title"].split()[0].lower()
        full_id = f"{game}:core:{uid}"
        if full_id in by_id:  # repeated heading: keep feeding the original unit
            return by_id[full_id]
        u = Unit(
            id=full_id,
            doc_type=doc_type,
            title=smart_title(title),
            h1=sec["title"],
            rel_path=f"{folder}/{fname}.md",
            page_start=page,
            page_end=page,
            meta={
                "section": f"{sec['num']} {sec['title']}" if numbered else sec["title"],
                "group": group_for(page),
                "phase": first_word if sec["title"].lower().endswith("phase") and first_word in phases else None,
                "rule_number": uid if _ID.match(uid) else None,
            },
            parent=None if doc_type == "core_section" else f"{game}:core:{sec['num']}.00" if numbered else f"{game}:core:{sec['num']}",
        )
        by_id[full_id] = u
        units.append(u)
        return u

    for pno in range(len(doc)):
        page_no = pno + 1
        sec = section_for(page_no)
        if sec is None or "contents" in sec["title"].lower():
            continue
        lines = [l for l in page_lines(doc[pno], page_no) if not _is_footer(l)]
        if not lines:
            continue
        if sec is not cur_section:
            cur_section = sec
            uid = f"{sec['num']}.00" if sec["num"].isdigit() else sec["num"]
            cur = new_unit(uid, sec["title"], page_no, "core_section", sec)
        lines = assign_columns(lines)
        page_units: list[tuple[float, Unit]] = []
        mode = "body"
        topic = ""
        note_target: Unit | None = None
        for ln in lines:
            f = ln.first
            head = _rule_head(ln)
            if head:
                rid, title = head
                cur = new_unit(rid, title, page_no, "core_rule", sec)
                page_units.append((ln.y0, cur))
                mode = "body"
                continue
            if f.font == "Akhand-Bold" and ln.text == "SEE ALSO":
                mode = "see"
                continue
            if mode == "see":
                if f.font == "Akhand-Bold" and f.size >= 11:
                    topic = ln.text
                    continue
                if f.text.strip() in ("▪", "►"):
                    for s in ln.spans:
                        if _ID.match(s.text.strip()):
                            see_also.append((page_no, topic, s.text.strip()))
                    continue
                mode = "body"
            if f.font == "Akhand-Bold" and abs(f.size - 8.0) < 0.6 and ln.text.isupper():
                # Margin note: belongs to the rule whose heading sits above it on this page.
                above = [u for y, u in page_units if y <= ln.y0 + 2]
                note_target = above[-1] if above else cur
                mode = "note"
                note_target.lines.append(_heading(ln, 3))
                continue
            if mode == "note":
                if any(s.italic for s in ln.spans):
                    note_target.lines.append(ln)
                    continue
                mode = "body"
            if f.font == "Akhand-Bold" and f.size >= 11 and ln.all_bold and ln.text.upper() == ln.text:
                in_rule = sec["num"].isdigit() and cur is not None and cur.doc_type == "core_rule"
                if f.size < 16 and (in_rule or re.match(r"^\d+\.\s", ln.text)):
                    cur.lines.append(_heading(ln, 3))
                else:
                    uid = f"{sec['num']}:{slugify(ln.text)}"
                    doc_type = "core_rule" if sec["num"].isdigit() else "core_reference"
                    cur = new_unit(uid, ln.text, page_no, doc_type, sec)
                    page_units.append((ln.y0, cur))
                continue
            if cur is not None:
                cur.lines.append(ln)
                cur.page_end = max(cur.page_end, page_no)

    # Headings that wrap over two lines arrive as an empty unit followed by its tail.
    merged: list[Unit] = []
    carry: Unit | None = None
    for u in units:
        if carry is not None:
            u.title = f"{carry.title} {u.title}"
            u.page_start = min(u.page_start, carry.page_start)
            if not u.meta.get("rule_number"):
                u.id = f"{carry.id}-{u.id.rsplit(':', 1)[-1]}"
                u.rel_path = f"{u.rel_path.rsplit('/', 1)[0]}/{slugify(u.title)}.md"
            carry = None
        if not u.lines and u.doc_type != "core_section" and not u.meta.get("rule_number"):
            carry = u
            continue
        merged.append(u)
    units = merged

    for u in units:
        u.body = render(u.lines, u)
        u.refs.discard(u.meta.get("rule_number") or "")
    titles = {u.title.upper(): u for u in units}
    for _page, topic, rid in see_also:
        if topic.upper() in titles:
            titles[topic.upper()].refs.add(rid)
    return [u for u in units if u.body.strip() or u.doc_type == "core_section"]
