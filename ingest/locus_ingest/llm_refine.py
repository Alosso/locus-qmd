"""Optional Claude pass: re-transcribe units the deterministic parser flagged as low confidence.

Only units with `low_confidence` (unusual datasheet layouts, stratagems without the
WHEN/TARGET/EFFECT structure) are sent. Claude sees the rendered page image(s) plus the
parser's attempt and returns corrected Markdown. Results are cached on disk by content
hash, so a re-run costs nothing unless the source page or the prompt changed.

Needs ANTHROPIC_API_KEY (or another credential the Anthropic SDK resolves).
"""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

import pymupdf

MODEL = os.environ.get("LOCUS_INGEST_MODEL", "claude-opus-5-5")
PROMPT_VERSION = "1"

SYSTEM = """You transcribe tabletop wargame rules pages into clean Markdown for a rules search index.

You are given page images from a rules PDF and an automatic transcription attempt of ONE entry on those pages. The attempt may have scrambled tables or reading order.

Return only the corrected Markdown body for that entry:
- Transcribe exactly what the page says. Never add, infer, summarise or omit rules text, numbers or keywords.
- Stat lines and weapon profiles become Markdown tables with the printed column headers.
- Use `###` for sub-sections (Abilities, Wargear Options, Unit Composition, ...). Do not emit `#` or `##` headings.
- Keep bold for defined terms and KEYWORDS as on the page. Omit flavour text, page furniture and logos.
- Keep the `<!-- page:N -->` markers from the attempt, each placed before the content of that page.
- If the entry is not legible in the images, return the attempt unchanged."""


def _page_png(doc: pymupdf.Document, page_no: int) -> str:
    pix = doc[page_no - 1].get_pixmap(dpi=130)
    return base64.standard_b64encode(pix.tobytes("png")).decode()


def refine(docs: list[tuple[dict, list]], root: Path) -> None:
    import anthropic

    client = anthropic.Anthropic()
    cache = root / "ingest" / ".cache" / "llm"
    cache.mkdir(parents=True, exist_ok=True)
    done = skipped = 0
    for src, units in docs:
        todo = [u for u in units if u.low_confidence]
        if not todo:
            continue
        with pymupdf.open(src["file"]) as doc:
            for u in todo:
                key = hashlib.sha256(f"{PROMPT_VERSION}|{MODEL}|{src['info']['sha256']}|{u.id}|{u.body}".encode()).hexdigest()[:24]
                cpath = cache / f"{key}.md"
                if cpath.exists():
                    u.body, u.low_confidence = cpath.read_text(encoding="utf-8"), False
                    u.meta["refined_by"] = MODEL
                    skipped += 1
                    continue
                pages = list(range(u.page_start, min(u.page_end, u.page_start + 3) + 1))
                content: list[dict] = [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _page_png(doc, p)}}
                    for p in pages
                ]
                content.append({"type": "text", "text": f"Entry: {u.title} ({u.doc_type}), pages {pages}.\n\nAutomatic attempt:\n\n{u.body}"})
                try:
                    resp = client.messages.create(
                        model=MODEL,
                        max_tokens=16000,
                        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
                        messages=[{"role": "user", "content": content}],
                    )
                except anthropic.RateLimitError:
                    print("  rate limited; stopping the refine pass (re-run to continue, finished units are cached)")
                    return
                except anthropic.APIStatusError as e:
                    print(f"  refine failed for {u.id}: {e.status_code} {e.message}")
                    continue
                if resp.stop_reason != "end_turn":
                    print(f"  refine skipped for {u.id}: stop_reason={resp.stop_reason}")
                    continue
                text = "".join(b.text for b in resp.content if b.type == "text").strip()
                if len(text) < 0.4 * len(u.body):  # a much shorter answer means content was dropped
                    print(f"  refine rejected for {u.id}: output too short")
                    continue
                cpath.write_text(text, encoding="utf-8")
                u.body, u.low_confidence = text, False
                u.meta["refined_by"] = MODEL
                done += 1
    print(f"  LLM refine: {done} units rewritten, {skipped} from cache")
