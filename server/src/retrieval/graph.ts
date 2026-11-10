/**
 * Knowledge graph store.
 *
 * graph.jsonl (written by the ingestion pipeline) is loaded into SQLite on the data
 * volume; at boot the tables are read into adjacency maps so traversal during a query
 * is pure in-memory lookups.
 *
 * Entities:      Section, Rule, Phase, Unit, Keyword, Stratagem, Enhancement, Detachment,
 *                Faction, Distance, Update, FAQ
 * Relationships: MODIFIES, OVERRIDES, REQUIRES, EXCLUDES (semantic)
 *                PART_OF, REFERENCES, HAS_KEYWORD, MENTIONS, OCCURS_IN, HAS_DISTANCE (structural)
 */
import { createHash } from "node:crypto";
import { createReadStream, existsSync, readFileSync } from "node:fs";
import { createInterface } from "node:readline";
import Database from "better-sqlite3";
import { config } from "../config.js";

export interface GraphNode {
  id: string;
  type: string;
  name: string;
  game: string;
  doc_type?: string | null;
  faction?: string | null;
  detachment?: string | null;
  md_path?: string | null;
  source_file?: string | null;
  document_title?: string | null;
  document_version?: string | null;
  printed_version?: string | null;
  effective_date?: string | null;
  page_start?: number | null;
  page_end?: number | null;
  section?: string | null;
  rule_number?: string | null;
  is_update: boolean;
  external: boolean;
}

export interface GraphEdge {
  src: string;
  dst: string;
  type: string;
  mode?: string | null;
  evidence?: string | null;
  confidence?: number | null;
}

export const SEMANTIC_EDGES = ["MODIFIES", "OVERRIDES", "REQUIRES", "EXCLUDES"] as const;

const SCHEMA = `
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS nodes (
  id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT NOT NULL, game TEXT NOT NULL,
  doc_type TEXT, faction TEXT, detachment TEXT, md_path TEXT, source_file TEXT,
  document_title TEXT, document_version TEXT, printed_version TEXT, effective_date TEXT,
  page_start INTEGER, page_end INTEGER, section TEXT, rule_number TEXT,
  is_update INTEGER NOT NULL DEFAULT 0, external INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS edges (
  src TEXT NOT NULL, dst TEXT NOT NULL, type TEXT NOT NULL, mode TEXT, evidence TEXT, confidence REAL,
  PRIMARY KEY (src, dst, type)
);
CREATE INDEX IF NOT EXISTS edges_dst ON edges (dst, type);
CREATE INDEX IF NOT EXISTS nodes_path ON nodes (md_path);
CREATE INDEX IF NOT EXISTS nodes_type ON nodes (type, faction);
`;

const NODE_COLS = [
  "id", "type", "name", "game", "doc_type", "faction", "detachment", "md_path", "source_file", "document_title",
  "document_version", "printed_version", "effective_date", "page_start", "page_end", "section", "rule_number",
  "is_update", "external",
] as const;

function fileHash(p: string): string {
  return createHash("sha256").update(readFileSync(p)).digest("hex");
}

/** Load graph.jsonl into SQLite. Skips the work when the file has not changed. */
export async function buildGraph(force = false): Promise<{ nodes: number; edges: number; rebuilt: boolean }> {
  if (!existsSync(config.graphJsonl)) throw new Error(`graph file not found: ${config.graphJsonl} (run the ingestion first)`);
  const db = new Database(config.graphDbPath);
  db.pragma("journal_mode = WAL");
  db.exec(SCHEMA);
  const hash = fileHash(config.graphJsonl);
  const prev = db.prepare("SELECT value FROM meta WHERE key = 'source_hash'").get() as { value: string } | undefined;
  const count = () => ({
    nodes: (db.prepare("SELECT count(*) c FROM nodes").get() as { c: number }).c,
    edges: (db.prepare("SELECT count(*) c FROM edges").get() as { c: number }).c,
  });
  if (!force && prev?.value === hash) {
    const c = count();
    db.close();
    return { ...c, rebuilt: false };
  }
  const insNode = db.prepare(
    `INSERT OR REPLACE INTO nodes (${NODE_COLS.join(",")}) VALUES (${NODE_COLS.map((c) => "@" + c).join(",")})`,
  );
  const insEdge = db.prepare(
    "INSERT OR REPLACE INTO edges (src, dst, type, mode, evidence, confidence) VALUES (@src, @dst, @type, @mode, @evidence, @confidence)",
  );
  const rows: any[] = [];
  const rl = createInterface({ input: createReadStream(config.graphJsonl, "utf8") });
  for await (const line of rl) if (line.trim()) rows.push(JSON.parse(line));
  db.transaction(() => {
    db.exec("DELETE FROM nodes; DELETE FROM edges;");
    for (const r of rows) {
      if (r.kind === "node") {
        const row: Record<string, unknown> = {};
        for (const c of NODE_COLS) row[c] = r[c] ?? null;
        row.is_update = r.is_update ? 1 : 0;
        row.external = r.external ? 1 : 0;
        insNode.run(row);
      } else {
        insEdge.run({ mode: null, evidence: null, confidence: null, ...r });
      }
    }
    db.prepare("INSERT OR REPLACE INTO meta (key, value) VALUES ('source_hash', ?)").run(hash);
  })();
  const c = count();
  db.close();
  return { ...c, rebuilt: true };
}

const normPath = (p: string): string => p.toLowerCase().replace(/\\/g, "/").replace(/[^a-z0-9/]+/g, "-");

export class Graph {
  readonly nodes = new Map<string, GraphNode>();
  private out = new Map<string, GraphEdge[]>();
  private inc = new Map<string, GraphEdge[]>();
  private byPath = new Map<string, string>();
  private byName = new Map<string, string[]>();

  static load(): Graph {
    const g = new Graph();
    if (!existsSync(config.graphDbPath)) return g;
    const db = new Database(config.graphDbPath, { readonly: true });
    for (const r of db.prepare("SELECT * FROM nodes").all() as any[]) {
      const n: GraphNode = { ...r, is_update: !!r.is_update, external: !!r.external };
      g.nodes.set(n.id, n);
      if (n.md_path) g.byPath.set(normPath(n.md_path), n.id);
      const key = n.name.toLowerCase();
      g.byName.set(key, [...(g.byName.get(key) ?? []), n.id]);
    }
    for (const e of db.prepare("SELECT * FROM edges").all() as GraphEdge[]) {
      (g.out.get(e.src) ?? g.out.set(e.src, []).get(e.src)!).push(e);
      (g.inc.get(e.dst) ?? g.inc.set(e.dst, []).get(e.dst)!).push(e);
    }
    db.close();
    return g;
  }

  get size(): { nodes: number; edges: number } {
    let edges = 0;
    for (const l of this.out.values()) edges += l.length;
    return { nodes: this.nodes.size, edges };
  }

  get(id: string): GraphNode | undefined {
    return this.nodes.get(id);
  }

  /** Resolve a qmd hit ("qmd://wh40k-core/core-rules/18-transports/18-04-….md") to its node. */
  nodeForFile(file: string): GraphNode | undefined {
    let p = normPath(file.replace(/^qmd:\/\//, ""));
    const prefix = `${config.game}-`;
    if (p.startsWith(prefix)) p = p.slice(prefix.length);
    const id = this.byPath.get(p) ?? [...this.byPath.entries()].find(([k]) => p.endsWith(k))?.[1];
    return id ? this.nodes.get(id) : undefined;
  }

  findByName(name: string): GraphNode[] {
    return (this.byName.get(name.toLowerCase()) ?? []).map((id) => this.nodes.get(id)!).filter(Boolean);
  }

  outgoing(id: string, types?: readonly string[]): GraphEdge[] {
    const l = this.out.get(id) ?? [];
    return types ? l.filter((e) => types.includes(e.type)) : l;
  }

  incoming(id: string, types?: readonly string[]): GraphEdge[] {
    const l = this.inc.get(id) ?? [];
    return types ? l.filter((e) => types.includes(e.type)) : l;
  }

  /** Update nodes that amend `id`, newest first. Errata always travel with the rule they change. */
  updatesFor(id: string): Array<{ node: GraphNode; edge: GraphEdge }> {
    return this.incoming(id, ["OVERRIDES", "MODIFIES"])
      .map((edge) => ({ node: this.nodes.get(edge.src)!, edge }))
      .filter((u) => u.node?.is_update)
      .sort((a, b) => (b.node.effective_date ?? "").localeCompare(a.node.effective_date ?? ""));
  }

  /** Rules this node depends on or cites, strongest relationship first. */
  related(id: string, limit = 6): Array<{ node: GraphNode; edge: GraphEdge }> {
    const rank: Record<string, number> = { REQUIRES: 0, EXCLUDES: 1, REFERENCES: 2 };
    return this.outgoing(id, ["REQUIRES", "EXCLUDES", "REFERENCES"])
      .map((edge) => ({ node: this.nodes.get(edge.dst)!, edge }))
      .filter((r) => r.node && r.node.md_path && !r.node.external)
      .sort((a, b) => rank[a.edge.type] - rank[b.edge.type])
      .slice(0, limit);
  }

  /**
   * Entity linking: named units, stratagems, enhancements and detachments that appear
   * verbatim in the question (including ones we only know from update documents).
   */
  mentions(query: string, factions: string[] = [], limit = 3): GraphNode[] {
    this.entityNames ??= [...this.nodes.values()]
      .filter((n) => ["Unit", "Stratagem", "Enhancement", "Detachment"].includes(n.type) || (n.external && n.type === "Rule"))
      .filter((n) => n.name.length >= 6)
      .map((n) => ({ key: ` ${n.name.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim()} `, id: n.id }))
      .sort((a, b) => b.key.length - a.key.length);
    const q = ` ${query.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim()} `;
    const want = new Set(factions.map((f) => f.toLowerCase()));
    const out: GraphNode[] = [];
    let rest = q;
    for (const e of this.entityNames) {
      if (!rest.includes(e.key)) continue;
      const hits = this.entityNames.filter((x) => x.key === e.key).map((x) => this.nodes.get(x.id)!);
      const scoped = want.size ? hits.filter((n) => want.has((n.faction ?? "").toLowerCase())) : hits;
      // Without a faction in the question an ambiguous name (shared by several factions) is skipped.
      if (scoped.length && (want.size || scoped.length === 1)) out.push(...scoped.slice(0, 1));
      rest = rest.replace(e.key, " ");
      if (out.length >= limit) break;
    }
    return out;
  }

  private entityNames?: Array<{ key: string; id: string }>;

  factions(): string[] {
    return [...this.nodes.values()].filter((n) => n.type === "Faction" && n.name.toLowerCase() !== "universal").map((n) => n.name).sort();
  }
}
