from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


def slugify(text: str, max_len: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:max_len].rstrip("-") or "x"


def norm_name(text: str) -> str:
    """Normalised form used to match rule names across documents."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = text.replace("'", "")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


_SMALL = {"of", "the", "and", "to", "in", "for", "a", "an", "on", "at", "by", "with", "or", "from"}


def smart_title(text: str) -> str:
    """Title-case ALL-CAPS headings, keeping small words lower-case."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text.isupper():
        return text
    words = text.lower().split(" ")
    out = [w if (i and w in _SMALL) else w[:1].upper() + w[1:] for i, w in enumerate(words)]
    return re.sub(r"([-/(\[])([a-z])", lambda m: m.group(1) + m.group(2).upper(), " ".join(out))


@dataclass
class Span:
    text: str
    font: str
    size: float
    color: int
    x0: float
    x1: float

    @property
    def bold(self) -> bool:
        f = self.font
        return any(k in f for k in ("Bold", "Black", "Extrabold", "ExtraBold"))

    @property
    def italic(self) -> bool:
        return "Ital" in self.font or self.font.endswith("-It")

    @property
    def keyword(self) -> bool:
        """Game keywords are set in a compressed mono face (e.g. TRANSPORT)."""
        return "MonoCompressed-Bold" in self.font

    @property
    def red(self) -> bool:
        r, g, b = (self.color >> 16) & 255, (self.color >> 8) & 255, self.color & 255
        return r > 140 and g < 45 and b < 45


@dataclass
class Line:
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    spans: list[Span]
    rotated: bool = False
    col: int = 0
    text_override: str | None = None  # synthetic lines (table rows)

    @property
    def first(self) -> Span:
        return self.spans[0]

    @property
    def size(self) -> float:
        best: dict[float, int] = {}
        for s in self.spans:
            best[s.size] = best.get(s.size, 0) + len(s.text.strip())
        return max(best, key=best.get)

    @property
    def text(self) -> str:
        if self.text_override is not None:
            return self.text_override
        out = ""
        prev: Span | None = None
        for s in self.spans:
            if prev is not None and out and not out[-1].isspace() and not s.text[:1].isspace():
                if s.x0 - prev.x1 > 0.12 * s.size:
                    out += " "
            out += s.text
            prev = s
        return re.sub(r"\s+", " ", out).strip()

    @property
    def all_bold(self) -> bool:
        return all(s.bold for s in self.spans)

    @property
    def all_italic(self) -> bool:
        return all(s.italic for s in self.spans)

    @property
    def red(self) -> bool:
        return any(s.red for s in self.spans)


@dataclass
class Unit:
    """One retrievable rule unit; becomes one Markdown file and one graph node."""

    id: str
    doc_type: str
    title: str
    h1: str
    rel_path: str
    page_start: int
    page_end: int
    body: str = ""
    lines: list[Line] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    keywords: set[str] = field(default_factory=set)
    terms: set[str] = field(default_factory=set)
    refs: set[str] = field(default_factory=set)
    parent: str | None = None
    low_confidence: bool = False
