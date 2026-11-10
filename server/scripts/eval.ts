/**
 * Retrieval regression check: each question must surface the expected rule ids.
 * Runs without any LLM, so it is safe to run in CI:  npm run eval
 */
import { closeStore } from "../src/retrieval/qmd.js";
import { retrieve, warmup, type Mode } from "../src/retrieval/retriever.js";

interface Case {
  q: string;
  expect: string[]; // node ids (prefix match) that must appear among the sources
  update?: boolean; // at least one source must be a rules update
}

const CASES: Case[] = [
  { q: "Can a unit charge after disembarking from a transport that made a normal move?", expect: ["wh40k:core:18.04", "wh40k:universal:universal-update:move-types-for-disembarking"], update: true },
  { q: "What is the set-up distance for an emergency disembark?", expect: ["wh40k:core:18.05"] },
  { q: "How does embarking in a transport work?", expect: ["wh40k:core:18.02"] },
  { q: "Can my unit shoot after it advanced?", expect: ["wh40k:core:10."] },
  { q: "What does the Blast weapon ability do?", expect: ["wh40k:core:24.05"] },
  { q: "How does the anti keyword ability on weapons work?", expect: ["wh40k:core:24.03"] },
  { q: "When is a unit below half-strength?", expect: ["wh40k:core:rules-appendix"] },
  { q: "How do hazard rolls work?", expect: ["wh40k:core:06.03"] },
  { q: "What happens at the end of the battle round?", expect: ["wh40k:core:07.03"] },
  { q: "Can I use the same stratagem twice in one phase?", expect: ["wh40k:universal:universal-update:stratagems-that-can-be-used-more-than-once", "wh40k:core:15."], update: true },
  { q: "A rule lets me use a stratagem for 0CP, how does that work now?", expect: ["wh40k:universal:universal-update:modifying-a-stratagem"], update: true },
  { q: "When can a unit fall back?", expect: ["wh40k:core:09.07"] },
  { q: "How does the benefit of cover work?", expect: ["wh40k:core:13."] },
  { q: "Can a stratagem that brings back a destroyed unit be used more than once?", expect: ["wh40k:universal:universal-update:stratagems-that-add-new-units"], update: true },
  { q: "What is a stratagem that stops my unit being shot from more than 12 inches changed to?", expect: ["wh40k:universal:universal-update:stratagems-that-prevent-units"], update: true },
  { q: "How do I set up a unit from strategic reserves?", expect: ["wh40k:core:20."] },
];

const warm = await warmup();
console.log(`warm-up ${warm} ms\n`);
let failed = 0;
for (const mode of ["fast", "deep"] as Mode[]) {
  let pass = 0;
  const times: number[] = [];
  for (const c of CASES) {
    const r = await retrieve(c.q, { mode });
    times.push(r.timings.total_ms);
    const ids = r.sources.map((s) => s.id);
    const missing = c.expect.filter((e) => !ids.some((id) => id.startsWith(e)));
    const updOk = !c.update || r.sources.some((s) => s.is_update);
    if (!missing.length && updOk) pass++;
    else {
      failed++;
      console.log(`FAIL [${mode}] ${c.q}\n   missing: ${missing.join(", ") || "(update flag)"}\n   got: ${ids.slice(0, 7).join(", ")}`);
    }
  }
  times.sort((a, b) => a - b);
  console.log(`${mode}: ${pass}/${CASES.length} passed, retrieval p50 ${times[Math.floor(times.length / 2)]} ms, p95 ${times[Math.floor(times.length * 0.95)]} ms, max ${times.at(-1)} ms`);
}
await closeStore();
process.exit(failed ? 1 : 0);
