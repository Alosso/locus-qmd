"""Source discovery: which PDFs exist, what version they are, and whether they changed."""
from __future__ import annotations

import fnmatch
import hashlib
import re
from pathlib import Path

from .model import slugify

_NOISE = {"eng", "wh40k", "warhammer", "warhammer40k", "new40k", "40", "000", "40000", "key"}


def file_info(path: Path) -> dict:
    """Derive document family and version token from a Warhammer Community filename.

    eng_30-09_wh40k_faction_pack_world_eaters-rxxj1fzeoh-kfx1cectxb.pdf
      -> family faction_pack_world_eaters, document_version RXXJ1FZEOH
    """
    stem = path.stem
    parts = stem.rsplit("-", 2)
    base, version = (parts[0], parts[1]) if len(parts) == 3 else (stem, "")
    tokens = [t for t in re.split(r"[^a-z0-9]+", base.lower()) if t and t not in _NOISE]
    while len(tokens) >= 2 and tokens[0].isdigit() and tokens[1].isdigit():  # leading dd-mm date
        tokens = tokens[2:]
    return {
        "source_file": path.name,
        "family": "_".join(tokens),
        "doc_slug": slugify("-".join(tokens)),
        "document_version": version.upper() or None,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def discover(root: Path, cfg: dict) -> list[dict]:
    """Resolve the `sources` of a game config to concrete files (first matching rule wins)."""
    seen: set[Path] = set()
    out: list[dict] = []
    for rule in cfg["sources"]:
        folder = root / rule["path"]
        if not folder.is_dir():
            continue
        for f in sorted(folder.iterdir()):
            if f.suffix.lower() != ".pdf" or f in seen or not fnmatch.fnmatch(f.name.lower(), rule["match"].lower()):
                continue
            seen.add(f)
            out.append({**{k: v for k, v in rule.items() if k not in ("path", "match")}, "file": f, "info": file_info(f)})
    return out
