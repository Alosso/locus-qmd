"""Low-level PDF text access: spans with font, size and colour, grouped into lines."""
from __future__ import annotations

import pymupdf

from .model import Line, Span

_FLAGS = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES


def _clean(text: str) -> str:
    """Normalise PDF text: dotted leaders arrive as \\x16, other control bytes are noise."""
    text = text.replace(" ", " ").replace("‑", "-").replace("\x16", ".").replace("\t", " ")
    return "".join(c for c in text if c >= " ")


def page_lines(page: pymupdf.Page, page_no: int) -> list[Line]:
    out: list[Line] = []
    for block in page.get_text("dict", flags=_FLAGS)["blocks"]:
        for ln in block.get("lines", []):
            spans = [
                Span(
                    text=_clean(s["text"]),
                    font=s["font"].split("+")[-1],
                    size=round(s["size"], 1),
                    color=s["color"],
                    x0=s["bbox"][0],
                    x1=s["bbox"][2],
                )
                for s in ln["spans"]
                if s["text"].strip()
            ]
            if not spans:
                continue
            x0, y0, x1, y1 = ln["bbox"]
            rotated = abs(ln["dir"][0] - 1.0) > 0.01
            out.append(Line(page_no, x0, y0, x1, y1, spans, rotated))
    return out


def assign_columns(lines: list[Line], *, split_at: list[float] | None = None, gap: float = 30.0) -> list[Line]:
    """Order lines column by column, top to bottom.

    With `split_at`, columns are fixed x boundaries; otherwise columns are found by
    clustering the left edges of lines (a new column starts after a gap of `gap` pt).
    """
    if not lines:
        return []
    if split_at is not None:
        for ln in lines:
            ln.col = sum(1 for b in split_at if ln.x0 >= b)
    else:
        xs = sorted({round(ln.x0) for ln in lines})
        bounds, prev = [], xs[0]
        for x in xs[1:]:
            if x - prev > gap:
                bounds.append((x + prev) / 2)
            prev = x
        for ln in lines:
            ln.col = sum(1 for b in bounds if ln.x0 >= b)
    return sorted(lines, key=lambda l: (l.col, round(l.y0, 1), l.x0))
