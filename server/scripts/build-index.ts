/** Build or refresh the qmd index (full-text + vectors) from corpus/<game>. */
import { config } from "../src/config.js";
import { buildIndex, closeStore, collectionNames, getStore } from "../src/retrieval/qmd.js";

console.log(`corpus: ${config.corpusDir}\nindex:  ${config.qmdDbPath}\ncollections: ${collectionNames().join(", ")}`);
await buildIndex();
const status = await (await getStore()).getStatus();
console.log("status:", JSON.stringify(status).slice(0, 600));
await closeStore();
