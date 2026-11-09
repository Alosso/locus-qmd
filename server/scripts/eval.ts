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
  { q: "What does the World Eaters stratagem Punish the Craven do?", expect: ["wh40k:world-eaters:stratagem:vessels-of-wrath-punish-the-craven"] },
  { q: "What is the invulnerable save of a World Eaters Defiler?", expect: ["wh40k:world-eaters:datasheet:defiler"] },
  { q: "World Eaters Brazen Engines detachment rule", expect: ["wh40k:world-eaters:detachment-rule:brazen-engines-rampaging-terrors"] },
  { q: "What changed for the World Eaters Berzerker's Wrath stratagem?", expect: ["wh40k:world-eaters:faction-update:berzerker"], update: true },
  { q: "What toughness do World Eaters Chaos Terminators have after the update?", expect: ["wh40k:world-eaters:faction-update:datasheets"], update: true },
  { q: "Adepta Sororitas Champions of Faith Righteous Purpose", expect: ["wh40k:adepta-sororitas:detachment-rule:champions-of-faith-righteous-purpose"] },
  { q: "Which stratagems does the Butchers of Khorne detachment of the World Eaters have?", expect: ["wh40k:world-eaters:detachment:butchers-of-khorne", "wh40k:world-eaters:stratagem:butchers-of-khorne"] },
  { q: "Space Marines Captain Titus Righteous Fury ability update", expect: ["wh40k:space-marines:faction-update:captain-titus"], update: true },
  { q: "Aeldari Dragon Knights weapons", expect: ["wh40k:aeldari:datasheet:dragon-knights"] },
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
