"""Knowledge-graph extraction.

Entities: Section, Rule, Phase, Unit, Keyword, Term, Stratagem, Enhancement, Detachment,
Faction, Distance, Update, FAQ.
Relationships: MODIFIES, OVERRIDES, REQUIRES, EXCLUDES plus the structural PART_OF,
REFERENCES, HAS_KEYWORD, MENTIONS, OCCURS_IN and HAS_DISTANCE.

Everything here is deterministic (typography and wording patterns). Each semantic edge
carries the sentence it was derived from so it can be audited.
"""
from __future__ import annotations

import re

from .model import Unit, norm_name, slugify

NODE_TYPES = {
    "core_section": "Section", "core_rule": "Rule", "core_reference": "Rule", "rule": "Rule",
    "army_rule": "Rule", "detachment_rule": "Rule", "detachment": "Detachment", "stratagem": "Stratagem",
    "enhancement": "Enhancement", "datasheet": "Unit", "faction_update": "Update",
    "universal_update": "Update", "faq": "FAQ",
}
_PHASE = re.compile(r"\b(Command|Movement|Shooting|Charge|Fight) phase", re.I)
_DIST = re.compile(r'(?<![\w.])(\d{1,2})"')
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_COND = re.compile(r"\b(if|when|while|must|only|unless|provided|eligible)\b", re.I)
_EXCL_PAREN = re.compile(r"\(excluding ([^)]+)\)", re.I)
_EXCL_NEG = re.compile(r"\b(?:cannot|can not|not|never)\b(?:\s+[\w’'-]+){0,6}?\s+\*\*([^*]+)\*\*", re.I)
_SUFFIX = re.compile(
    r"\s+(stratagem|enhancement|detachment rule|detachment|army rule|wargear ability|ability|abilities|datasheet|"
    r"keywords?|section|sections|profile)s?$", re.I)


def _singular(n: str) -> str:
    return n[:-1] if n.endswith("s") and not n.endswith("ss") else n


def _sentences(body: str) -> list[str]:
    text = re.sub(r"<!--.*?-->", " ", body)
    out = []
    for para in re.split(r"\n\s*\n|\n(?=- )", text):
        para = " ".join(para.split())
        out.extend(s for s in re.split(r"(?<=[.:;])\s+(?=[A-Z*‘'])", para) if s)
    return out


def resolve_updates(units: list[Unit], game: str) -> list[dict]:
    """Decide which rule each update entry amends. Returns stub nodes for rules that
    live in documents outside the corpus (e.g. a Codex we do not have)."""
    by_name: dict[tuple[str, str], Unit] = {}
    core_ids = {u.meta.get("rule_number"): u for u in units if u.meta.get("rule_number")}
    core_titles: dict[str, Unit] = {}
    for u in units:
        if u.doc_type in ("core_rule", "core_reference", "core_section"):
            core_titles.setdefault(_singular(norm_name(re.sub(r"^\d+\.\s*", "", u.title))), u)
        elif u.doc_type not in ("faction_update", "universal_update", "faq"):
            fslug = u.id.split(":")[1]
            by_name.setdefault((fslug, norm_name(u.title)), u)
    stubs: dict[str, dict] = {}

    def target_for(fslug: str, faction: str, name: str, group: str | None, hint: str) -> str:
        key = norm_name(_SUFFIX.sub("", _SUFFIX.sub("", name.strip())))
        hit = by_name.get((fslug, key)) or by_name.get((fslug, _singular(key)))
        if hit is not None:
            return hit.id
        low = name.lower()
        kind = ("Stratagem" if "stratagem" in low else "Enhancement" if "enhancement" in low
                else "Rule" if "rule" in low or (group or "").upper().startswith("ARMY") else hint)
        sid = f"{game}:{fslug}:ext:{slugify(kind)}-{slugify(key)}"
        stubs.setdefault(sid, {"id": sid, "type": kind, "name": _SUFFIX.sub("", name.strip()), "faction": faction,
                               "external": True, "note": "Defined in a publication that is not in the corpus."})
        return sid

    for u in units:
        if u.doc_type == "universal_update":
            # Rules whose title is a bolded term (most-mentioned first), then rule numbers cited
            # in the text. Numbers missing from the printed core rules become stubs.
            ids = []
            ranked = sorted(u.terms, key=lambda t: (-u.body.count(f"**{t}**"), -len(t)))
            for term in ranked:
                hit = core_titles.get(_singular(norm_name(term)))
                if hit is not None and hit.id not in ids:
                    ids.append(hit.id)
            for r in sorted(u.refs):
                if r in core_ids:
                    ids.insert(0, f"{game}:core:{r}")
                else:
                    sid = f"{game}:core:ext:{r}"
                    stubs.setdefault(sid, {"id": sid, "type": "Rule", "name": f"Core rule {r}", "faction": None, "external": True,
                                           "rule_number": r, "note": "Referenced by number but not present in the printed core rules."})
                    ids.append(sid)
            ids = list(dict.fromkeys(ids))
            u.meta["replaces_rule_ids"] = ids[:6]
            u.meta["replaces_rule_id"] = ids[0] if ids else None
            u.meta["update_mode"] = "append"
        elif u.doc_type == "faction_update":
            fslug = u.id.split(":")[1]
            faction = u.meta.get("faction") or fslug
            group = u.meta.get("update_group") or ""
            target = u.meta.get("update_target") or u.title
            ids: list[str] = []
            if re.search(r"\|\s*\**UNITS\**\s*\|", u.body):
                # Characteristic-change table: the last column lists the affected units.
                for row in u.body.splitlines():
                    cells = [c.strip() for c in row.strip().strip("|").split("|")]
                    if row.startswith("|") and len(cells) >= 2 and "---" not in row and cells[-1].strip("* ").upper() != "UNITS":
                        for name in re.split(r",\s*", cells[-1].replace("**", "")):
                            if 2 < len(name) < 60:
                                ids.append(target_for(fslug, faction, name, group, "Unit"))
            else:
                subject = target.split(",")[0]
                hint = "Unit" if "datasheet" in group.lower() else "Rule"
                det = re.sub(r"\s+detachment$", "", group, flags=re.I) if group.lower().endswith("detachment") else None
                first = target_for(fslug, faction, subject, group, hint)
                ids.append(first)
                if det and first.split(":")[2] == "ext":
                    stubs[first]["detachment"] = det
            ids = list(dict.fromkeys(ids))
            u.meta["replaces_rule_ids"] = ids[:40]
            u.meta["replaces_rule_id"] = ids[0] if ids else None
    return list(stubs.values())


def build(units: list[Unit], infos: dict[str, dict], stubs: list[dict], game: str) -> tuple[list[dict], list[dict]]:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    seen_edges: set[tuple] = set()

    def node(nid: str, **kw) -> None:
        if nid not in nodes:
            nodes[nid] = {"id": nid, "game": game, **kw}

    def edge(src: str, dst: str, typ: str, **kw) -> None:
        key = (src, dst, typ)
        if src == dst or key in seen_edges:
            return
        seen_edges.add(key)
        edges.append({"src": src, "dst": dst, "type": typ, **{k: v for k, v in kw.items() if v is not None}})

    defs: dict[str, str] = {}
    for u in units:
        info = infos[u.id]
        node(u.id, type=NODE_TYPES.get(u.doc_type, "Rule"), name=u.title, doc_type=u.doc_type,
             faction=u.meta.get("faction"), detachment=u.meta.get("detachment"), md_path=info["md_path"],
             source_file=info["source_file"], document_title=info.get("document_title"),
             document_version=info.get("document_version"), printed_version=info.get("printed_version"),
             effective_date=info.get("effective_date"), page_start=u.page_start, page_end=u.page_end,
             is_update=u.doc_type in ("faction_update", "universal_update"), section=u.meta.get("section"),
             rule_number=u.meta.get("rule_number"), external=False)
        if u.doc_type in ("core_rule", "core_reference", "core_section"):
            defs.setdefault(_singular(norm_name(re.sub(r"^\d+\.\s*", "", u.title))), u.id)
    for s in stubs:
        node(s["id"], type=s["type"], name=s["name"], faction=s.get("faction"), detachment=s.get("detachment"),
             external=True, is_update=False)
        fslug = s["id"].split(":")[1]
        if fslug != "core":
            node(f"{game}:faction:{fslug}", type="Faction", name=s.get("faction") or fslug, external=False, is_update=False)
            edge(s["id"], f"{game}:faction:{fslug}", "PART_OF")

    # Keyword lexicon from datasheets lets "WORLD EATERS CHARACTER" split into its two keywords.
    lexicon: set[str] = set()
    for u in units:
        for k in (u.meta.get("unit_keywords") or []) + (u.meta.get("faction_keywords") or []):
            lexicon.add(k.upper())

    def split_keywords(k: str) -> list[str]:
        if k in lexicon or " " not in k:
            return [k]
        words, out, i = k.split(), [], 0
        while i < len(words):
            for j in range(len(words), i, -1):
                cand = " ".join(words[i:j])
                if cand in lexicon or j == i + 1:
                    out.append(cand)
                    i = j
                    break
        return out if all(o in lexicon for o in out) else [k]

    for u in units:
        # Structure.
        if u.parent:
            if u.parent not in nodes and ":faction:" in u.parent:
                node(u.parent, type="Faction", name=u.meta.get("faction") or u.parent.split(":")[-1], external=False, is_update=False)
            if u.parent in nodes:
                edge(u.id, u.parent, "PART_OF")
        # Phases.
        phases = {u.meta["phase"]} if u.meta.get("phase") else set()
        scope = u.body
        if u.doc_type == "stratagem":
            m = re.search(r"\*\*WHEN:?\*\*:?(.*?)(?:\n\n|$)", u.body, re.S)
            scope = m.group(1) if m else u.body
        if u.doc_type != "core_rule" or not phases:
            phases |= {p.lower() for p in _PHASE.findall(scope)} if u.doc_type != "datasheet" else set()
        for p in sorted(phases):
            node(f"{game}:phase:{p}", type="Phase", name=f"{p.title()} phase", external=False, is_update=False)
            edge(u.id, f"{game}:phase:{p}", "OCCURS_IN")
        # Explicit cross references by printed rule number.
        for r in sorted(u.refs):
            rid = f"{game}:core:{r}"
            if rid in nodes and u.doc_type not in ("universal_update",):
                edge(u.id, rid, "REFERENCES")
        # Keywords and distances.
        for k in sorted({p for k in u.keywords for p in split_keywords(k)}):
            kid = f"{game}:keyword:{slugify(k)}"
            node(kid, type="Keyword", name=k, external=False, is_update=False)
            edge(u.id, kid, "HAS_KEYWORD" if u.doc_type == "datasheet" and k.title() in (u.meta.get("unit_keywords") or []) else "MENTIONS")
        if u.doc_type != "datasheet":
            for d in sorted(set(_DIST.findall(u.body)), key=int)[:8]:
                did = f"{game}:distance:{d}"
                node(did, type="Distance", name=f'{d}"', external=False, is_update=False)
                edge(u.id, did, "HAS_DISTANCE")
        # Semantic edges from wording.
        if u.doc_type == "datasheet":
            continue
        for sent in _sentences(u.body):
            ev = sent[:240]
            excluded: set[str] = set()
            for m in _EXCL_PAREN.finditer(sent):
                for b in _BOLD.findall(m.group(1)):
                    excluded.add(b)
            for m in _EXCL_NEG.finditer(sent):
                excluded.add(m.group(1))
            for b in excluded:
                b = b.strip(" .,:;")
                tid = defs.get(_singular(norm_name(b)))
                if tid is None and b.upper() == b and len(b) > 1:
                    tid = f"{game}:keyword:{slugify(b)}"
                    node(tid, type="Keyword", name=b, external=False, is_update=False)
                if tid:
                    edge(u.id, tid, "EXCLUDES", evidence=ev, confidence=0.6)
            for b in _BOLD.findall(sent):
                tid = defs.get(_singular(norm_name(b.strip(" .,:;"))))
                if not tid or tid == u.id or b in excluded:
                    continue
                if u.doc_type in ("faction_update", "universal_update") and tid in (u.meta.get("replaces_rule_ids") or []):
                    continue
                before = sent[: sent.find(b)]
                typ = "REQUIRES" if _COND.search(before[-120:]) or re.match(r"\*\*(ELIGIBLE IF|WHEN|TARGET)", sent) else "REFERENCES"
                edge(u.id, tid, typ, evidence=ev, confidence=0.6 if typ == "REQUIRES" else 0.8)
        # Errata: update -> amended rule.
        if u.doc_type in ("faction_update", "universal_update"):
            mode = u.meta.get("update_mode") or "append"
            for tid in u.meta.get("replaces_rule_ids") or []:
                if tid in nodes:
                    edge(u.id, tid, "OVERRIDES" if mode in ("override", "remove") else "MODIFIES", mode=mode,
                         evidence=(u.meta.get("update_target") or u.title)[:240], confidence=0.9 if ":ext:" not in tid else 0.7)
    return list(nodes.values()), edges
