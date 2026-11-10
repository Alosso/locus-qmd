/**
 * Ask one question from the shell and print the cited answer.
 *
 *   npm run ask -- "Can a unit charge after disembarking?"
 *   npm run ask -- --voice "Can I shoot after advancing?"
 */
import { answer } from "../src/answer.js";
import { sourceLabel } from "../src/retrieval/context.js";
import { closeStore } from "../src/retrieval/qmd.js";
import type { Source } from "../src/retrieval/retriever.js";

const args = process.argv.slice(2);
const voice = args.includes("--voice");
const question = args.filter((a) => !a.startsWith("--")).join(" ").trim();
if (!question) {
  console.error('usage: npm run ask -- [--voice] "your question"');
  process.exit(2);
}

let sources: Source[] = [];
for await (const ev of answer({ question, voice })) {
  if (ev.type === "sources") sources = ev.sources;
  else if (ev.type === "delta") process.stdout.write(ev.text);
  else if (ev.type === "error") console.error(`\n[${ev.code}] ${ev.message}`);
  else if (ev.type === "done") {
    console.log("\n");
    for (const s of sources) {
      const mark = voice || ev.cited.includes(s.n) ? "*" : " ";
      console.log(`${mark} [${s.n}] ${sourceLabel(s)}${s.is_update ? "  (rules update)" : ""}`);
    }
    console.log(`\n${ev.model} · retrieval ${ev.timings.retrieval_ms} ms · first token ${ev.timings.first_token_ms ?? "-"} ms · total ${ev.timings.total_ms} ms`);
  }
}
await closeStore();
