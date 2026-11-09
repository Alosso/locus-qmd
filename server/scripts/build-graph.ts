/** Load corpus/<game>/graph.jsonl into the SQLite graph store. */
import { config } from "../src/config.js";
import { buildGraph, Graph } from "../src/retrieval/graph.js";

const res = await buildGraph(process.argv.includes("--force"));
console.log(`graph: ${res.nodes} nodes, ${res.edges} edges (${res.rebuilt ? "rebuilt" : "unchanged"}) -> ${config.graphDbPath}`);
const g = Graph.load();
const byType: Record<string, number> = {};
for (const n of g.nodes.values()) byType[n.type] = (byType[n.type] ?? 0) + 1;
console.log("node types:", byType);
