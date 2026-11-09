/**
 * Latency benchmark for the voice path: retrieval and time to first LLM token.
 * (Speech-to-text endpointing and text-to-speech add to this in a live call.)
 *
 *   npm run bench            # retrieval only when no LLM key is configured
 */
import { answer } from "../src/answer.js";
import { closeStore } from "../src/retrieval/qmd.js";
import { warmup } from "../src/retrieval/retriever.js";

const QUESTIONS = [
  "Can I shoot after advancing?",
  "How far can a unit disembark from a transport?",
  "What does lethal hits do?",
  "Can a battle-shocked unit use a stratagem?",
  "How does deep strike work?",
  "When can I fall back and still charge?",
  "What is engagement range?",
  "How do I measure line of sight to a vehicle?",
];

const pct = (xs: number[], p: number) => [...xs].sort((a, b) => a - b)[Math.min(xs.length - 1, Math.floor(xs.length * p))];

console.log(`warm-up (model load): ${await warmup()} ms`);
const retrieval: number[] = [];
const firstToken: number[] = [];
for (const q of QUESTIONS) {
  for await (const ev of answer({ question: q, voice: true })) {
    if (ev.type === "sources") retrieval.push(ev.timings.total_ms);
    if (ev.type === "done" && ev.timings.first_token_ms != null) firstToken.push(ev.timings.first_token_ms);
    if (ev.type === "error" && ev.code !== "llm_not_configured") console.warn(ev.message);
  }
}
console.log(`retrieval:   p50 ${pct(retrieval, 0.5)} ms, p95 ${pct(retrieval, 0.95)} ms (n=${retrieval.length})`);
if (firstToken.length) {
  console.log(`first token: p50 ${pct(firstToken, 0.5)} ms, p95 ${pct(firstToken, 0.95)} ms (retrieval included)`);
} else {
  console.log("first token: not measured (no LLM credentials configured)");
}
await closeStore();
