"""Ingestion CLI.

    python -m locus_ingest run --game wh40k            # incremental
    python -m locus_ingest run --game wh40k --force    # re-parse everything
    python -m locus_ingest run --game wh40k --llm      # add the Claude clean-up pass

Reads games/<game>/game.yml, parses every listed PDF into rule units, resolves which
rules each update amends, then writes corpus/<game>/**.md, graph.jsonl and qa.json.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import pickle
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

import pymupdf
import yaml

from . import graph, writer
from .manifest import discover
from .profiles import core_rules, pack

PROFILES = {"core_rules": core_rules.parse, "pack": pack.parse}
PKG = Path(__file__).parent


def _code_hash() -> str:
    h = hashlib.sha256()
    for f in sorted(PKG.rglob("*.py")):
        h.update(f.read_bytes())
    return h.hexdigest()[:12]


def run(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    cfg = yaml.safe_load((root / "games" / args.game / "game.yml").read_text(encoding="utf-8"))
    game = cfg["game"]
    corpus = root / cfg["corpus_dir"]
    cache = root / "ingest" / ".cache" / "units"
    cache.mkdir(parents=True, exist_ok=True)
    code = _code_hash()
    sources = discover(root, cfg)
    if args.only:
        sources = [s for s in sources if fnmatch.fnmatch(s["file"].name.lower(), args.only.lower())]
    if not sources:
        print("No source PDFs matched.", file=sys.stderr)
        return 1

    # 1. Parse (cached per file hash + parser version).
    docs = []
    for s in sources:
        info = s["info"]
        cpath = cache / f"{info['sha256'][:16]}-{code}.pkl"
        t0 = time.time()
        if cpath.exists() and not args.force:
            units, info = pickle.loads(cpath.read_bytes())
            s["info"] = info
            state = "cached"
        else:
            with pymupdf.open(s["file"]) as doc:
                info["pages"] = len(doc)
                units = PROFILES[s["profile"]](doc, s, cfg)
            for u in units:
                u.lines = []
            cpath.write_bytes(pickle.dumps((units, info)))
            state = f"parsed in {time.time() - t0:.1f}s"
        docs.append((s, units))
        print(f"  {info['doc_slug']:<55} {len(units):>4} units  ({state})")

    # 2. Globally unique ids.
    seen: Counter = Counter()
    for s, units in docs:
        for u in units:
            seen[u.id] += 1
            if seen[u.id] > 1:
                u.id = f"{u.id}-{s['info']['doc_slug'][:24]}"
    all_units = [u for _, units in docs for u in units]

    # 3. Which rule does each update amend?
    stubs = graph.resolve_updates(all_units, game)

    # 4. Optional Claude pass over low-confidence units.
    if args.llm:
        from . import llm_refine
        llm_refine.refine(docs, root)

    # 5. Write Markdown. A document family always maps to the same folder, so a newer
    #    PDF of the same family replaces the old output; the old output is archived.
    manifest_path = corpus / "_manifest.json"
    old = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest: dict = {}
    infos: dict[str, dict] = {}
    for s, units in docs:
        info = s["info"]
        rel_dir = f"{s['collection']}/{info['doc_slug']}"
        out_dir = corpus / rel_dir
        prev = old.get(info["family"])
        if out_dir.exists():
            if prev and prev["sha256"] != info["sha256"]:
                arch = corpus / "_archive" / f"{info['doc_slug']}@{prev.get('document_version') or prev['sha256'][:8]}"
                arch.parent.mkdir(parents=True, exist_ok=True)
                if not arch.exists():
                    shutil.move(str(out_dir), str(arch))
                    print(f"  archived superseded {info['doc_slug']} -> {arch.relative_to(corpus)}")
            shutil.rmtree(out_dir, ignore_errors=True)
        writer.write_units(units, info, game, out_dir)
        for u in units:
            infos[u.id] = {**info, "md_path": f"{rel_dir}/{u.rel_path}"}
        manifest[info["family"]] = {k: info.get(k) for k in (
            "source_file", "sha256", "document_version", "printed_version", "effective_date", "document_title", "doc_slug")}
        manifest[info["family"]].update(collection=s["collection"], units=len(units))
    if not args.only:
        for family, prev in old.items():
            if family not in manifest:  # source PDF was removed
                gone = corpus / prev["collection"] / prev["doc_slug"]
                if gone.exists():
                    arch = corpus / "_archive" / f"{prev['doc_slug']}@{prev.get('document_version') or 'removed'}"
                    arch.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(gone), str(arch))
    else:
        manifest = {**old, **manifest}
    corpus.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # 6. Graph + QA (only meaningful over the full corpus).
    if not args.only:
        nodes, edges = graph.build(all_units, infos, stubs, game)
        with (corpus / "graph.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
            for n in nodes:
                fh.write(json.dumps({"kind": "node", **n}, ensure_ascii=False) + "\n")
            for e in edges:
                fh.write(json.dumps({"kind": "edge", **e}, ensure_ascii=False) + "\n")
        qa = {
            "units": len(all_units),
            "by_type": dict(Counter(u.doc_type for u in all_units).most_common()),
            "low_confidence": sorted(u.id for u in all_units if u.low_confidence),
            "nodes": len(nodes),
            "nodes_by_type": dict(Counter(n["type"] for n in nodes).most_common()),
            "edges_by_type": dict(Counter(e["type"] for e in edges).most_common()),
            "external_targets": sum(1 for n in nodes if n.get("external")),
            "updates_without_target": sorted(u.id for u in all_units if u.doc_type.endswith("_update") and not u.meta.get("replaces_rule_id")),
            "core_rule_numbers": sorted(u.meta["rule_number"] for u in all_units if u.meta.get("rule_number")),
        }
        (corpus / "qa.json").write_text(json.dumps(qa, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n{qa['units']} units -> {corpus}")
        print("  by type:", qa["by_type"])
        print("  graph:", qa["nodes"], "nodes", qa["nodes_by_type"])
        print("  edges:", qa["edges_by_type"])
        print(f"  low-confidence units: {len(qa['low_confidence'])}; update targets outside corpus: {qa['external_targets']};"
              f" updates without target: {len(qa['updates_without_target'])}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="locus_ingest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="parse PDFs and write the Markdown corpus and graph")
    r.add_argument("--game", default="wh40k")
    r.add_argument("--root", default=".")
    r.add_argument("--only", help="only files matching this glob (skips graph rebuild)")
    r.add_argument("--force", action="store_true", help="ignore the parse cache")
    r.add_argument("--llm", action="store_true", help="clean low-confidence units with Claude (needs ANTHROPIC_API_KEY)")
    args = ap.parse_args()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
