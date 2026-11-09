/**
 * Hybrid GraphRAG retrieval.
 *
 *   1. qmd hybrid search (BM25 + vectors, fused with RRF; optional cross-encoder rerank)
 *   2. map hits to graph nodes
 *   3. errata overlay: every hit pulls in the Update nodes that OVERRIDE or MODIFY it,
 *      and an Update hit pulls in the rule it amends
 *   4. one-hop expansion along REQUIRES / EXCLUDES / REFERENCES within a size budget
 *
 * `fast` mode (voice) skips reranking and is served from an LRU cache where possible.
 */
import "../config.js";
import { readFile } from "node:fs/promises";
import path from "node:path";
import type { HybridQueryResult } from "@tobilu/qmd";
import { config } from "../config.js";
import { Graph, type GraphEdge, type GraphNode } from "./graph.js";
import { collectionNames, getStore, qmdName } from "./qmd.js";

export type Mode = "fast" | "deep";

export interface Source {
  n: number;
  id: string;
  title: string;
  doc_type: string;
  document: string;
  document_version: string | null;
  printed_version: string | null;
  effective_date: string | null;
  section: string | null;
  rule_number: string | null;
  faction: string | null;
  pages: string;
  is_update: boolean;
  role: "match" | "update" | "amended_rule" | "related";
  relation?: string;
  amends?: string[];
  amended_by?: number[];
  score?: number;
}

export interface Block {
  source: Source;
  node: GraphNode;
  body: string;
  note?: string;
}

export interface Retrieval {
  query: string;
  mode: Mode;
  faction: string | null;
  blocks: Block[];
  sources: Source[];
  timings: { search_ms: number; graph_ms: number; total_ms: number; cached: boolean };
}

let graph: Graph | null = null;
export const getGraph = (): Graph => (graph ??= Graph.load());
export const reloadGraph = (): Graph => (graph = Graph.load());

const bodyCache = new Map<string, string>();

/** Markdown body of a node without frontmatter, title lines and the keyword footer. */
export async function nodeBody(node: GraphNode): Promise<string> {
  if (!node.md_path) return "";
  const hit = bodyCache.get(node.id);
  if (hit !== undefined) return hit;
  let text = "";
  try {
    text = await readFile(path.join(config.corpusDir, node.md_path), "utf8");
  } catch {
    return "";
  }
  text = text.replace(/^---\n[\s\S]*?\n---\n/, "");
  text = text.replace(/^\s*# .*\n+## .*\n+(\*[^\n]*stratagem\*\n+|\*Rules update from[^\n]*\n+)?/, "");
  text = text.replace(/\n### Keywords\n[\s\S]*$/, "");
  text = text.replace(/<!-- page:\d+ -->\n?/g, "").replace(/\n{3,}/g, "\n\n").trim();
  bodyCache.set(node.id, text);
  return text;
}

function pages(n: GraphNode): string {
  if (n.page_start == null) return "";
  return n.page_end && n.page_end !== n.page_start ? `${n.page_start}-${n.page_end}` : `${n.page_start}`;
}

function toSource(n: number, node: GraphNode, role: Source["role"], extra: Partial<Source> = {}): Source {
  return {
    n,
    id: node.id,
    title: node.name,
    doc_type: node.doc_type ?? node.type,
    document: node.document_title ?? "Core Rules",
    document_version: node.document_version ?? null,
    printed_version: node.printed_version ?? null,
    effective_date: node.effective_date ?? null,
    section: node.section ?? node.detachment ?? null,
    rule_number: node.rule_number ?? null,
    faction: node.faction ?? null,
    pages: pages(node),
    is_update: node.is_update,
    role,
    ...extra,
  };
}

/** Factions named in the question, e.g. "can my World Eaters ..." -> ["World Eaters"]. */
export function detectFactions(query: string): string[] {
  const q = ` ${query.toLowerCase().replace(/[^a-z0-9' ]+/g, " ")} `;
  return getGraph()
    .factions()
    .filter((f) => {
      const name = f.toLowerCase();
      return q.includes(` ${name} `) || q.includes(` ${name.replace(/s$/, "")} `);
    });
}

const cache = new Map<string, Retrieval>();
const CACHE_MAX = 300;

async function search(query: string, collections: string[], limit: number, rerank: boolean): Promise<HybridQueryResult[]> {
  if (!collections.length) return [];
  const store = await getStore();
  try {
    return await store.search({
      // Explicit sub-queries: BM25 for exact rule and unit names, vectors for paraphrase.
      // This also skips qmd's query-expansion model, which costs far more than it adds here.
      queries: [
        { type: "lex", query },
        { type: "vec", query },
      ],
      collections,
      limit,
      rerank,
    });
  } catch (err) {
    if (!rerank) throw err;
    console.warn("rerank failed, falling back to RRF only:", (err as Error).message);
    return search(query, collections, limit, false);
  }
}

export async function retrieve(query: string, opts: { mode?: Mode; faction?: string | null; limit?: number } = {}): Promise<Retrieval> {
  const mode = opts.mode ?? "deep";
  const t0 = performance.now();
  const g = getGraph();
  const named = opts.faction ? [opts.faction] : detectFactions(query);
  const faction = named[0] ?? null;
  const key = `${mode}|${named.join(",")}|${query.trim().toLowerCase()}`;
  const cached = cache.get(key);
  if (cached) return { ...cached, timings: { ...cached.timings, total_ms: Math.round(performance.now() - t0), cached: true } };

  const k = opts.limit ?? (mode === "fast" ? config.retrieval.voiceTopK : config.retrieval.topK);
  const rerank = mode === "deep" && config.retrieval.rerank;
  const all = collectionNames();
  const general = all.filter((c) => c !== "factions").map(qmdName);
  const factionCols = all.filter((c) => c === "factions").map(qmdName);

  // Rules that apply to everyone and faction content are searched separately, so a
  // general question is not crowded out by 24 factions' worth of datasheets.
  const [genHits, facHits] = await Promise.all([
    search(query, general, k, rerank),
    search(query, factionCols, named.length ? k * 6 : k, rerank),
  ]);
  const tSearch = performance.now();

  const want = new Set(named.map((f) => f.toLowerCase()));
  const toNodes = (hits: HybridQueryResult[]) =>
    hits.map((h) => ({ node: g.nodeForFile(h.file), score: h.score })).filter((x): x is { node: GraphNode; score: number } => !!x.node);
  const gen = toNodes(genHits);
  let fac = toNodes(facHits);
  if (want.size) fac = fac.filter((x) => want.has((x.node.faction ?? "").toLowerCase()));
  const facQuota = want.size ? Math.ceil(k * 0.67) : 1;
  // The universal-updates collection is tiny, so vector search always returns something from
  // it. Keep such a hit only near the top; errata reach their rules through the graph anyway.
  const genKept = gen.filter((x, i) => !x.node.is_update || i < 2);
  const picked: Array<{ node: GraphNode; score: number }> = [];
  const first = want.size ? fac.slice(0, facQuota) : genKept.slice(0, k - facQuota);
  const second = want.size ? genKept.slice(0, Math.max(1, k - first.length)) : fac.slice(0, facQuota);
  for (const x of [...first, ...second]) if (!picked.some((p) => p.node.id === x.node.id)) picked.push(x);

  // Assemble blocks with the errata overlay.
  const blocks: Block[] = [];
  const index = new Map<string, Block>();
  const budget = mode === "fast" ? config.retrieval.voiceContextChars : config.retrieval.contextChars;
  let used = 0;

  const add = async (node: GraphNode, role: Source["role"], extra: Partial<Source> = {}, cap = 6000): Promise<Block | null> => {
    const existing = index.get(node.id);
    if (existing) return existing;
    let body = await nodeBody(node);
    if (!body && !node.external) return null;
    if (body.length > cap) body = body.slice(0, cap) + "\n[... truncated]";
    if (used + body.length > budget && blocks.length >= 2) return null;
    used += body.length;
    const block: Block = { source: toSource(blocks.length + 1, node, role, extra), node, body };
    blocks.push(block);
    index.set(node.id, block);
    return block;
  };

  const attachUpdates = async (target: Block): Promise<void> => {
    for (const { node: upd, edge } of g.updatesFor(target.node.id).slice(0, 4)) {
      const ub = await add(upd, "update", { relation: edge.type, amends: [target.node.id] });
      if (!ub) continue;
      ub.source.amends = [...new Set([...(ub.source.amends ?? []), target.node.id])];
      target.source.amended_by = [...new Set([...(target.source.amended_by ?? []), ub.source.n])];
    }
  };

  for (const { node, score } of picked) {
    if (node.is_update) {
      // An update on its own is half the story: bring the rule it amends (when we have it).
      const targets = g.outgoing(node.id, ["OVERRIDES", "MODIFIES"]);
      const ub = await add(node, "match", { score, amends: targets.map((e) => e.dst) });
      if (!ub) continue;
      const missing: string[] = [];
      for (const e of targets.slice(0, 2)) {
        const t = g.get(e.dst);
        if (!t) continue;
        if (t.external) {
          missing.push(t.name);
          continue;
        }
        const tb = await add(t, "amended_rule", { relation: e.type }, 3000);
        if (tb) tb.source.amended_by = [...new Set([...(tb.source.amended_by ?? []), ub.source.n])];
      }
      if (missing.length) ub.note = `The original text of ${missing.join(", ")} is in a publication that is not in this index; only the amended wording is available.`;
    } else {
      const b = await add(node, "match", { score });
      if (b) await attachUpdates(b);
    }
  }

  // Entity linking: a unit or stratagem named in the question brings its errata with it,
  // even when the update text itself (e.g. a characteristics table) is a poor text match.
  for (const ent of g.mentions(query, named)) {
    if (ent.external) {
      for (const { node: upd, edge } of g.updatesFor(ent.id).slice(0, 3)) {
        const ub = await add(upd, "update", { relation: edge.type, amends: [ent.id] });
        if (ub) ub.note ??= `Amends ${ent.name}, whose original text is in a publication that is not in this index.`;
      }
    } else {
      const eb = await add(ent, "match", { relation: "named in the question" });
      if (eb) await attachUpdates(eb);
    }
  }

  // One hop along the semantic edges for the best matches.
  const hop = mode === "fast" ? 1 : 2;
  for (const { node } of picked.slice(0, 2)) {
    if (node.is_update) continue;
    for (const { node: rel, edge } of g.related(node.id, hop)) {
      if (index.has(rel.id)) continue;
      const rb = await add(rel, "related", { relation: `${edge.type} from ${node.name}` }, 2500);
      if (rb) await attachUpdates(rb);
    }
  }

  const tEnd = performance.now();
  const result: Retrieval = {
    query,
    mode,
    faction,
    blocks,
    sources: blocks.map((b) => b.source),
    timings: {
      search_ms: Math.round(tSearch - t0),
      graph_ms: Math.round(tEnd - tSearch),
      total_ms: Math.round(tEnd - t0),
      cached: false,
    },
  };
  cache.set(key, result);
  if (cache.size > CACHE_MAX) cache.delete(cache.keys().next().value!);
  return result;
}

export function clearCaches(): void {
  cache.clear();
  bodyCache.clear();
}

/** Load the embedding model and graph before the first user request. */
export async function warmup(): Promise<number> {
  const t0 = performance.now();
  getGraph();
  await retrieve("advance move", { mode: "fast" });
  cache.clear();
  return Math.round(performance.now() - t0);
}

/** One rule with everything the graph knows about it (used by GET /api/rules/:id). */
export async function ruleDetail(id: string): Promise<null | {
  node: GraphNode;
  body: string;
  updates: Array<{ node: GraphNode; edge: GraphEdge; body: string }>;
  outgoing: Array<{ edge: GraphEdge; name: string | undefined }>;
  incoming: Array<{ edge: GraphEdge; name: string | undefined }>;
}> {
  const g = getGraph();
  const node = g.get(id);
  if (!node) return null;
  const structural = ["MENTIONS", "HAS_KEYWORD", "HAS_DISTANCE"];
  return {
    node,
    body: await nodeBody(node),
    updates: await Promise.all(g.updatesFor(id).map(async (u) => ({ ...u, body: await nodeBody(u.node) }))),
    outgoing: g.outgoing(id).filter((e) => !structural.includes(e.type)).map((edge) => ({ edge, name: g.get(edge.dst)?.name })),
    incoming: g.incoming(id).filter((e) => !structural.includes(e.type)).slice(0, 60).map((edge) => ({ edge, name: g.get(edge.src)?.name })),
  };
}
