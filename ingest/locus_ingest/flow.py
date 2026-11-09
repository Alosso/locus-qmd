"""Turn ordered PDF lines into Markdown paragraphs, lists and small tables."""
from __future__ import annotations

import re

from .model import Line, Span, Unit

BULLETS_L0 = ("▪", "■", "•", "➤", "→")
BULLETS_L1 = ("◦", "▫", "○")
_LABEL = re.compile(r"^(\*\*)?['‘]?[A-Z][A-Z \-/]{2,28}:")
_NUM_ITEM = re.compile(r"^(\d+)\.\s+")
RULE_ID = re.compile(r"\b(\d\d\.\d\d)\b")
NOT_KEYWORDS = {"WHEN", "TARGET", "EFFECT", "RESTRICTIONS", "BS", "WS", "AP", "OC", "LD", "SV", "CP", "CHANGES", "UNITS",
                "WEAPON", "WEAPONS", "N/A", "OR", "AND", "FAQ", "FAQS", "CORE", "FACTION"}


def spans_md(spans: list[Span], unit: Unit | None = None, *, plain: bool = False) -> str:
    """Inline Markdown for a run of spans: bold terms and KEYWORDS become **bold**."""
    parts: list[tuple[str, bool]] = []
    prev: Span | None = None
    for s in spans:
        text = s.text
        if prev is not None and not text[:1].isspace() and parts and not parts[-1][0][-1:].isspace():
            if s.x0 - prev.x1 > 0.12 * s.size:
                text = " " + text
        bold = s.bold and not plain
        if parts and parts[-1][1] == bold:
            parts[-1] = (parts[-1][0] + text, bold)
        else:
            parts.append((text, bold))
        prev = s
    out = ""
    for text, bold in parts:
        if bold and text.strip():
            lead = text[: len(text) - len(text.lstrip())]
            trail = text[len(text.rstrip()):]
            out += f"{lead}**{text.strip()}**{trail}"
        else:
            out += text
    out = re.sub(r"\*\*(\s*)\*\*", r"\1", out)
    return re.sub(r"\s+", " ", out).strip()


def line_md(line: Line, unit: Unit | None = None) -> str:
    if line.text_override is not None:
        return line.text_override
    spans = line.spans
    if spans and spans[0].text.strip() in BULLETS_L0 + BULLETS_L1:
        spans = spans[1:]
    md = spans_md(spans, unit, plain=line.all_bold and len(line.text) > 60)
    if line.all_italic and md:
        md = md.replace("*", "")
    if md.startswith("**- "):
        md = "**" + md[4:]
    return md


def _starts_label(line: Line) -> bool:
    """A line opening with a bold run that ends in a colon starts a new block."""
    if line.text_override is not None:
        return False
    spans = [s for s in line.spans if s.text.strip() and s.text.strip() not in BULLETS_L0 + BULLETS_L1]
    if not spans or not spans[0].bold:
        return False
    lead = ""
    for s in spans:
        if not s.bold:
            break
        lead += s.text
    return lead.strip().endswith(":") and len(lead) < 60


def _bullet_level(line: Line) -> int | None:
    t = line.spans[0].text.strip() if line.text_override is None else ""
    if t in BULLETS_L0 or line.text.startswith("- "):
        return 0
    if t in BULLETS_L1:
        return 1
    return None


def tableize(lines: list[Line], *, max_size: float = 7.2, row_gap: float = 9.0) -> list[Line]:
    """Collapse runs of small-print cell lines into synthetic Markdown table rows."""
    out: list[Line] = []
    i = 0
    while i < len(lines):
        if lines[i].size > max_size or lines[i].text_override is not None:
            out.append(lines[i])
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].size <= max_size and lines[j].page == lines[i].page and lines[j].col == lines[i].col:
            j += 1
        cells = sorted(lines[i:j], key=lambda l: l.y0)
        rows: list[list[Line]] = [[cells[0]]]
        for c in cells[1:]:
            if c.y0 - max(x.y0 for x in rows[-1]) > row_gap:
                rows.append([c])
            else:
                rows[-1].append(c)
        width = 0
        rendered: list[tuple[Line, list[str], bool]] = []
        for row in rows:
            row.sort(key=lambda l: l.x0)
            groups: list[list[Line]] = [[row[0]]]
            for c in row[1:]:
                if c.x0 - max(g.x0 for g in groups[-1]) > 15:
                    groups.append([c])
                else:
                    groups[-1].append(c)
            texts = [" ".join(line_md(c) for c in sorted(g, key=lambda l: l.y0)) for g in groups]
            header = all(s.color == 0xFFFFFF for c in row for s in c.spans)
            width = max(width, len(texts))
            rendered.append((row[0], texts, header))
        for k, (anchor, texts, header) in enumerate(rendered):
            texts = texts + [""] * (width - len(texts))
            md = "| " + " | ".join(texts) + " |"
            if header or k == 0:
                md += "\n|" + "---|" * width
            out.append(Line(anchor.page, anchor.x0, anchor.y0, anchor.x1, anchor.y1, anchor.spans, col=anchor.col, text_override=md))
        i = j
    return out


def render(lines: list[Line], unit: Unit | None = None, *, tables: bool = False) -> str:
    """Assemble Markdown from the ordered lines of one unit."""
    if tables:
        lines = tableize(lines)
    blocks: list[str] = []
    cur: list[str] = []
    kind = "p"  # p | li0 | li1 | row
    item_x = 0.0
    prev: Line | None = None
    page = None

    def flush() -> None:
        nonlocal cur
        if cur:
            text = " ".join(cur).strip().replace("** **", " ")
            prefix = {"p": "", "li0": "- ", "li1": "  - ", "row": ""}[kind]
            blocks.append(prefix + text)
        cur = []

    for ln in lines:
        md = line_md(ln, unit)
        if not md:
            continue
        if ln.page != page:
            if page is not None:
                flush()
                kind = "p"
            page = ln.page
            blocks.append(f"<!-- page:{ln.page} -->")
            prev = None
        if ln.text_override is not None:
            flush()
            kind = "row"
            cur = [md]
            flush()
            kind = "p"
            prev = ln
            continue
        level = _bullet_level(ln)
        gap = (ln.y0 - prev.y0) if prev is not None else 0.0
        new_col = prev is not None and (ln.col != prev.col or gap < -3)
        big_gap = prev is not None and gap > 1.6 * max(prev.size, 6.0)
        if level is not None:
            flush()
            kind = f"li{level}"
            item_x = ln.x0
            cur = [md[2:] if md.startswith("- ") else md]
        elif _NUM_ITEM.match(ln.text) and ln.spans[0].bold:
            flush()
            kind = "p"
            cur = [md.replace("**", "", 2) if md.startswith("**") else md]
        elif _starts_label(ln) and any(sp.color == 0xFFFFFF and sp.bold for sp in ln.spans[:2]) and not all(sp.color == 0xFFFFFF for sp in ln.spans):
            flush()  # highlighted option label inside a rules box
            kind = "li0"
            item_x = ln.x0 - 4
            cur = [md]
        elif kind.startswith("li") and not new_col and not big_gap and ln.x0 > item_x + 3:
            cur.append(md)
        elif new_col or big_gap or kind != "p" or _LABEL.match(md) or _starts_label(ln) or (prev is not None and abs(ln.size - prev.size) > 1.2):
            flush()
            kind = "p"
            cur = [md]
        else:
            cur.append(md)
        prev = ln
    flush()

    # Markdown lists and tables need blank lines around them, not between their rows.
    out: list[str] = []
    for b in blocks:
        is_item = b.startswith(("- ", "  - ", "|"))
        if out and not (is_item and out[-1].startswith(("- ", "  - ", "|"))):
            out.append("")
        out.append(b)
    text = "\n".join(out)
    if unit is not None:
        unit.refs.update(RULE_ID.findall(text))
        for m in re.finditer(r"\*\*([^*\n]+)\*\*", text):
            raw = m.group(1).strip()
            if raw.endswith(":") or text[m.end():m.end() + 1] == ":":
                continue  # labels such as WHEN:, ELIGIBLE IF:
            t = raw.strip(" .,;()'‘’\"")
            if len(t) < 2 or not any(c.isalpha() for c in t):
                continue
            if t.upper() == t:
                t = re.sub(r"\s+(\d+\+?|X\+?|D\d)$", "", t.strip("[]")).strip()
                if t not in NOT_KEYWORDS and len(t) > 1:
                    unit.keywords.add(t)
            elif t[0].islower() and len(t) < 48:
                unit.terms.add(t)
    return text.strip()
