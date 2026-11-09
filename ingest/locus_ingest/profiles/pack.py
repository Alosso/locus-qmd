"""Faction packs and update documents.

These PDFs mix several page designs (A4 detachment pages, older codex-style pages,
army-rule pages, datasheet cards, "Rules Updates" pages). Rather than one parser per
design, headings are recognised from typography (display face, all caps, larger than
body text) and the surrounding context decides what a heading starts.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

import pymupdf

from ..flow import render
from ..model import Line, Unit, norm_name, slugify, smart_title
from ..pdftext import assign_columns, page_lines
from . import cards

_CP = re.compile(r"^\d+\s?CP$")
_BANNERS = {
    "DETACHMENT RULES": "detachment_rule",
    "DETACHMENT RULE": "detachment_rule",
    "ENHANCEMENTS": "enhancement",
    "STRATAGEMS": "stratagem",
    "ARMY RULES": "army_rule",
    "ARMY RULE": "army_rule",
}
_SKIP_MAJORS = ("WHAT’S NEW?", "WHAT'S NEW?", "CONTENTS")
_DISPLAY = ("Akhand", "MonoCompressed-Bold", "ConduitITCStd-Bold", "ConduitITCStd-ExtraBold", "ConduitITCStd-Black")
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"])}


@dataclass
class Ctx:
    faction: str
    faction_slug: str
    universal: bool
    title: str | None = None       # detachment (page title)
    major: str | None = None       # UPDATES / FAQS / ARMY RULES / CODEX: ...
    banner: str | None = None      # doc_type implied by the last banner
    group: str | None = None       # heading inside an updates section
    mode: str = "rules"            # rules | updates | faq | skip
    entries_ok: bool = False


def _display_caps(ln: Line) -> bool:
    t = ln.text
    if not any(c.isalpha() for c in t) or t.upper() != t:
        return False
    spans = [s for s in ln.spans if any(c.isalnum() for c in s.text)]
    return all(any(d in s.font for d in _DISPLAY) and (s.bold or s.font.startswith("Akhand")) for s in spans)


def _iso_date(text: str) -> str | None:
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})", text)
    if not m or m.group(2).lower() not in _MONTHS:
        return None
    return f"{int(m.group(3)):04d}-{_MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"


def _title(text: str) -> str:
    return smart_title(text).replace("’S", "’s").replace("'S", "'s")


def _update_mode(body: str) -> str:
    head = re.sub(r"<!--.*?-->", "", body).strip().lower()[:160]
    if head.startswith(("change", "replace")) or "change to" in head:
        return "override"
    if head.startswith(("add", "insert")) or "add the following" in head or "add new" in head:
        return "append"
    if head.startswith(("delete", "remove")):
        return "remove"
    return "append"


def parse(doc: pymupdf.Document, src: dict, cfg: dict) -> list[Unit]:
    game = cfg["game"]
    universal = src.get("scope") == "universal"
    info = src["info"]

    # Cover metadata.
    cover = sorted((l for l in page_lines(doc[0], 1) if not l.rotated), key=lambda l: l.y0)
    doc_title = next((l.text for l in cover if l.size >= 30), None)
    for l in cover:
        if l.y0 > 0.3 * doc[0].rect.height:
            break
        m = re.search(r"VERSION\s+([\d.]+)", l.text)
        if m and l.size >= 14:
            info["printed_version"] = m.group(1)
            if ":" in l.text:
                info["document_kind"] = _title(l.text.split(":")[0])
        if "Legal for" in l.text:
            info["effective_date"] = _iso_date(l.text)
    faction = _title(doc_title) if doc_title else info["family"].replace("_", " ").title()
    if universal:
        faction = "Universal"
    info["document_title"] = (_title(doc_title) if doc_title else faction) + (
        f" {info.get('document_kind', '')}".rstrip() if not universal else "")
    info["faction"] = None if universal else faction
    ctx = Ctx(faction=faction, faction_slug=slugify(faction), universal=universal)

    units: list[Unit] = []
    ids: Counter = Counter()
    cur: Unit | None = None
    card_parts: list[dict] = []
    card_unit: Unit | None = None

    def start(doc_type: str, title: str, page: int, **meta) -> Unit:
        nonlocal cur
        title = _title(title)
        det = meta.get("detachment")
        base = {
            "stratagem": f"stratagem:{slugify(det or 'core')}-{slugify(title)}",
            "enhancement": f"enhancement:{slugify(det or 'x')}-{slugify(title)}",
            "detachment_rule": f"detachment-rule:{slugify(det or 'x')}-{slugify(title)}",
            "army_rule": f"army-rule:{slugify(title)}",
            "datasheet": f"datasheet:{slugify(title)}",
            "faq": f"faq:{slugify(title, 48)}",
        }.get(doc_type, f"{doc_type.replace('_', '-')}:{slugify(title, 48)}")
        uid = f"{game}:{ctx.faction_slug}:{base}"
        ids[uid] += 1
        if ids[uid] > 1:
            uid += f"-{ids[uid]}"
        kind, slug = uid.split(":")[2], uid.split(":")[3]
        folder = {"stratagem": "stratagems", "enhancement": "enhancements", "detachment-rule": "detachments",
                  "army-rule": "army-rules", "datasheet": "datasheets", "faq": "faqs"}.get(kind, "updates")
        h1 = ctx.faction if not det else f"{ctx.faction}: {det}"
        if doc_type in ("faction_update", "universal_update", "faq") and (ctx.group or ctx.major):
            h1 = f"{ctx.faction} Rules Updates: {_title(ctx.group or ctx.major)}"
        cur = Unit(id=uid, doc_type=doc_type, title=title, h1=h1,
                   rel_path=f"{folder}/{slug}.md", page_start=page, page_end=page,
                   meta={"faction": None if universal else ctx.faction, **meta},
                   parent=f"{game}:{ctx.faction_slug}:detachment:{slugify(det)}" if det and doc_type != "datasheet" else f"{game}:faction:{ctx.faction_slug}")
        units.append(cur)
        return cur

    def flush_card() -> None:
        nonlocal card_parts, card_unit
        if card_unit is not None and card_parts:
            cards.build_unit(card_parts, card_unit)
        card_parts, card_unit = [], None

    for pno in range(len(doc)):
        page = doc[pno]
        page_no = pno + 1
        w, h = page.rect.width, page.rect.height
        raw = [l for l in page_lines(page, page_no) if not l.rotated]
        if not raw:
            continue

        # ---- datasheet cards -------------------------------------------------
        if w > h and w < 560:
            part = cards.parse_card(raw, w, h)
            name = part["name"]
            if name and (card_unit is None or norm_name(name) != norm_name(card_unit.title)):
                flush_card()
                ctx.mode, ctx.title, ctx.banner, ctx.group = "rules", None, None, None
                card_unit = start("datasheet", name, page_no)
                cur = None
            if card_unit is not None:
                card_parts.append(part)
                card_unit.page_end = page_no
            continue
        flush_card()

        # ---- flowing pages ---------------------------------------------------
        lines = [l for l in raw
                 if not (l.text.isdigit() and len(l.text) <= 3 and l.size <= 9)
                 and "....." not in l.text and not l.text.startswith("►")]
        is_a4 = 590 < w < 600
        lines = assign_columns(lines, split_at=[w / 2]) if is_a4 else assign_columns(lines)
        if not lines:
            continue
        sizes = Counter()
        for l in lines:
            sizes[l.size] += len(l.text)
        body_size = sizes.most_common(1)[0][0]
        head_min = max(body_size + 1, 9.0)
        strat_heads = {id(lines[i]) for i in range(len(lines) - 1)
                       if lines[i + 1].text.rstrip().endswith("STRATAGEM") and lines[i + 1].size <= 7}
        cps = [l for l in lines if _CP.match(l.text)]
        heads = [l for l in lines if _display_caps(l) and head_min <= l.size < 14 and id(l) not in strat_heads
                 and re.sub(r"[^A-Z ]", "", l.text).strip() not in _BANNERS and not _CP.match(l.text)]
        top_head = max((l.size for l in heads), default=0)
        prev: Line | None = None
        skip_next = False
        for i, ln in enumerate(lines):
            if skip_next:
                skip_next = False
                continue
            t = ln.text
            if _CP.match(t):
                continue
            # Tags such as UPGRADE sit beside enhancement names in a small display face.
            head_spans = [s for s in ln.spans if not (s.size <= 8.5 and ln.first.size >= 11)]
            if ln.size >= 30 and _display_caps(ln):
                ctx.title = _title(t)
                ctx.banner, ctx.group, cur = None, None, None
                if "UPDATES" in t.upper():
                    ctx.mode, ctx.entries_ok, ctx.title = "updates", False, None
                elif norm_name(t) == norm_name(doc_title or ""):
                    ctx.title = None
                else:
                    ctx.mode = "rules"
                prev = ln
                continue
            if 14 <= ln.size < 30 and _display_caps(ln):
                cur = None
                up = t.upper()
                if up in _SKIP_MAJORS:
                    ctx.mode = "skip"
                elif "VERSION" in up or up == "RULES UPDATES":
                    ctx.mode, ctx.entries_ok = ("updates" if "UPDATES" in up else ctx.mode), False
                    if ctx.mode == "skip":
                        ctx.mode = "rules"
                elif up in ("FAQS", "FAQ"):
                    ctx.mode, ctx.major, ctx.group, ctx.entries_ok = "faq", t, None, True
                elif up in ("UPDATES", "ERRATA") or up.startswith("CODEX"):
                    ctx.mode, ctx.major, ctx.group, ctx.entries_ok = "updates", t, None, True
                elif up in _BANNERS:
                    ctx.mode, ctx.major, ctx.banner = "rules", t, _BANNERS[up]
                elif norm_name(t) != norm_name(doc_title or ""):
                    ctx.major = t
                    if ctx.mode == "skip":
                        ctx.mode = "rules"
                prev = ln
                continue
            if ctx.mode == "skip":
                if is_a4 and prev is not None and ln.col != prev.col:
                    pass  # contents and what's-new fill both columns of the cover
                prev = ln
                continue
            if id(ln) in strat_heads:
                type_line = lines[i + 1].text
                skip_next = True
                m = re.match(r"^(.*?)\s*[–-]\s*(.*?)\s*STRATAGEM$", type_line)
                det = m.group(1) if m else re.sub(r"\s*STRATAGEM$", "", type_line)
                near = min(cps, key=lambda c: abs(c.y0 - ln.y0) + 0.05 * abs(c.x0 - ln.x0), default=None)
                cp = near.text.replace(" ", "") if near is not None and abs(near.y0 - ln.y0) < 30 else None
                start("stratagem", t, page_no, detachment=_title(det) if det else ctx.title,
                      category=_title(m.group(2)) if m and m.group(2) else None, cp_cost=cp)
                prev = ln
                continue
            banner_text = re.sub(r"[^A-Z ]", "", t).strip()
            if _display_caps(ln) and banner_text in _BANNERS and ln.size < 14:
                ctx.banner, cur = _BANNERS[banner_text], None
                prev = ln
                continue
            is_head = bool(head_spans) and _display_caps(Line(ln.page, ln.x0, ln.y0, ln.x1, ln.y1, head_spans)) and head_min <= ln.first.size < 14
            if is_head:
                name = re.sub(r"\s+", " ", " ".join(s.text.strip() for s in head_spans)).strip()
                if ctx.mode in ("updates", "faq"):
                    if prev is not None and ctx.group and cur is None and abs(ln.y0 - prev.y0) < 1.4 * ln.size and _display_caps(prev):
                        ctx.group = f"{ctx.group} {name}"  # heading wrapped onto a second line
                    else:
                        ctx.group = name
                    ctx.entries_ok, cur = True, None
                elif prev is not None and cur is not None and not cur.lines and abs(ln.y0 - prev.y0) < 1.4 * ln.size and _display_caps(prev):
                    cur.title = f"{cur.title} {_title(name)}"
                elif name in ("KEYWORDS", "RESTRICTIONS") and cur is not None:
                    cur.lines.append(Line(ln.page, ln.x0, ln.y0, ln.x1, ln.y1, ln.spans, col=ln.col, text_override="### " + _title(name)))
                elif ln.first.size >= top_head - 0.6 or cur is None:
                    doc_type = ctx.banner or ("army_rule" if (ctx.major or "").upper().startswith("ARMY") else "rule")
                    start(doc_type, name, page_no, detachment=ctx.title if doc_type in ("detachment_rule", "enhancement") else None)
                else:
                    cur.lines.append(Line(ln.page, ln.x0, ln.y0, ln.x1, ln.y1, ln.spans, col=ln.col, text_override="### " + _title(name)))
                prev = ln
                continue
            # ---- body text ---------------------------------------------------
            if ctx.mode == "faq" and re.match(r"^Q\s*[:.]", t):
                start("faq", re.sub(r"^Q\s*[:.]\s*", "", t), page_no, update_group=_title(ctx.group) if ctx.group else None)
                cur.lines.append(ln)
                cur.meta["_question_open"] = True
            elif ctx.mode in ("updates", "faq") and ctx.entries_ok:
                bold_chars = sum(len(s.text) for s in ln.spans if s.bold)
                gap = (ln.y0 - prev.y0) if prev is not None and prev.page == ln.page and prev.col == ln.col else 99
                fresh = cur is None or gap > 1.5 * ln.size or gap < 0 or (prev is not None and prev.size >= 11)
                targetish = (ln.first.bold and bold_chars >= 0.8 * max(1, sum(len(s.text) for s in ln.spans))
                             and not t.startswith(("'", "‘", "“", '"', "▪", "■", "- ")) and len(t) < 150
                             and not re.match(r"^[A-Z ]+:", t) and ln.size > 7.2)
                if ctx.mode == "updates" and targetish and fresh:
                    doc_type = "universal_update" if universal else "faction_update"
                    start(doc_type, t, page_no, update_group=_title(ctx.group) if ctx.group else None, update_target=t)
                elif cur is None and ctx.group:
                    doc_type = "universal_update" if universal else ("faq" if ctx.mode == "faq" else "faction_update")
                    start(doc_type, ctx.group, page_no, update_group=_title(ctx.major) if ctx.major else None, update_target=_title(ctx.group))
                    cur.lines.append(ln)
                elif cur is not None:
                    if cur.meta.get("_question_open"):
                        if re.match(r"^A\s*[:.]", t):
                            cur.meta["_question_open"] = False
                        elif ln.first.bold:
                            cur.title = f"{cur.title} {t}"
                    cur.lines.append(ln)
            elif cur is not None:
                cur.lines.append(ln)
            if cur is not None:
                cur.page_end = max(cur.page_end, page_no)
            prev = ln
    flush_card()

    out: list[Unit] = []
    for u in units:
        u.meta.pop("_question_open", None)
        if u.doc_type != "datasheet":
            is_upd = u.doc_type in ("faction_update", "universal_update")
            u.body = render(u.lines, u, tables=is_upd)
            if is_upd:
                u.meta["update_mode"] = _update_mode(u.body)
                u.meta["changed_in_version"] = any(l.red for l in u.lines)
            if u.doc_type == "stratagem" and not re.search(r"\*\*(WHEN|TARGET|EFFECT)", u.body):
                u.low_confidence = True
        if len(re.sub(r"<!--.*?-->|\s", "", u.body)) < 12:
            continue
        out.append(u)

    # One index unit per detachment so "what does detachment X have?" is answerable.
    dets: dict[str, list[Unit]] = {}
    for u in out:
        d = u.meta.get("detachment")
        if d and u.doc_type in ("stratagem", "enhancement", "detachment_rule"):
            dets.setdefault(d, []).append(u)
    for d, members in dets.items():
        body = [f"<!-- page:{min(m.page_start for m in members)} -->", f"{d} is a {faction} detachment.", ""]
        for label, kind in (("Detachment rule", "detachment_rule"), ("Enhancements", "enhancement"), ("Stratagems", "stratagem")):
            names = [m.title + (f" ({m.meta['cp_cost']})" if m.meta.get("cp_cost") else "") for m in members if m.doc_type == kind]
            if names:
                body.append(f"- **{label}:** " + "; ".join(names))
        out.append(Unit(id=f"{game}:{ctx.faction_slug}:detachment:{slugify(d)}", doc_type="detachment", title=d, h1=faction,
                        rel_path=f"detachments/{slugify(d)}.md",
                        page_start=min(m.page_start for m in members), page_end=max(m.page_end for m in members),
                        body="\n".join(body), meta={"faction": faction, "detachment": d},
                        parent=f"{game}:faction:{ctx.faction_slug}"))
    return out
