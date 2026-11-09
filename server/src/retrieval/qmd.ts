/** qmd store: BM25 (FTS5) + vector search + RRF fusion + optional reranking, in-process. */
import "../config.js";
import { existsSync } from "node:fs";
import path from "node:path";
import { createStore, type QMDStore } from "@tobilu/qmd";
import { config, readManifest } from "../config.js";

const CONTEXTS: Record<string, string> = {
  core: "Core Rules of the game: numbered sections and rules (e.g. 18.04 Disembark Move) that apply to every army.",
  updates: "Universal rules updates and errata that amend the Core Rules and rules shared by all factions.",
  factions: "Faction packs: detachments, stratagems, enhancements, datasheets, plus faction rules updates and FAQs.",
};

export function collectionNames(): string[] {
  const set = new Set(Object.values(readManifest()).map((m) => m.collection));
  return [...set].filter((c) => existsSync(path.join(config.corpusDir, c))).sort();
}

export const qmdName = (collection: string): string => `${config.game}-${collection}`;

let store: Promise<QMDStore> | null = null;

export function getStore(): Promise<QMDStore> {
  store ??= createStore({
    dbPath: config.qmdDbPath,
    config: {
      collections: Object.fromEntries(
        collectionNames().map((c) => [qmdName(c), { path: path.join(config.corpusDir, c), pattern: "**/*.md" }]),
      ),
    },
  });
  return store;
}

/** (Re)index the Markdown corpus and embed any new or changed chunks. */
export async function buildIndex(log: (msg: string) => void = console.log): Promise<void> {
  const s = await getStore();
  for (const c of collectionNames()) {
    if (CONTEXTS[c]) await s.addContext(qmdName(c), "/", CONTEXTS[c]);
  }
  const t0 = Date.now();
  const upd = await s.update();
  log(`qmd update: ${upd.indexed} new, ${upd.updated} updated, ${upd.unchanged} unchanged, ${upd.removed} removed (${Date.now() - t0} ms)`);
  if (upd.needsEmbedding > 0) {
    log(`qmd embed: ${upd.needsEmbedding} documents need vectors (the first run downloads the embedding model)`);
    let last = 0;
    const t1 = Date.now();
    const res = await s.embed({
      onProgress: (p) => {
        const now = Date.now();
        if (now - last > 15000) {
          last = now;
          log(`  embedding... ${JSON.stringify(p)}`);
        }
      },
    });
    log(`qmd embed done in ${Math.round((Date.now() - t1) / 1000)} s: ${JSON.stringify(res)}`);
  }
}

export async function closeStore(): Promise<void> {
  if (store) await (await store).close();
  store = null;
}
