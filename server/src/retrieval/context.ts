/** Render retrieved blocks as the context the model answers from, with numbered citations. */
import type { Block, Retrieval, Source } from "./retriever.js";

export function sourceLabel(s: Source): string {
  const doc = s.document + (s.printed_version ? ` v${s.printed_version}` : "");
  const where = [s.section, s.rule_number ? `${s.rule_number} ${s.title}` : s.title].filter(Boolean).join(" · ");
  return `${doc} · ${where}${s.pages ? ` · p.${s.pages}` : ""}`;
}

function header(b: Block, byId: Map<string, Block>): string {
  const s = b.source;
  const lines = [`[${s.n}] ${sourceLabel(s)}`];
  if (s.is_update) {
    const verb = s.relation === "OVERRIDES" ? "REPLACES the printed wording of" : "ADDS TO / CLARIFIES";
    const targets = (s.amends ?? []).map((id) => byId.get(id)).filter((t): t is Block => !!t);
    const names = targets.map((t) => `[${t.source.n}] ${t.source.title}`);
    lines.push(
      `RULES UPDATE${s.effective_date ? ` (legal from ${s.effective_date}${s.document_version ? `, document ${s.document_version}` : ""})` : ""}` +
        (names.length ? `: ${verb} ${names.join(", ")}. Where they differ, this update is the current rule.` : ". This is the current wording."),
    );
  } else if (s.amended_by?.length) {
    lines.push(`NOTE: amended by ${s.amended_by.map((n) => `[${n}]`).join(", ")}. Read this entry together with the update; the update takes precedence.`);
  }
  if (s.role === "related" && s.relation) lines.push(`(Related rule: ${s.relation}.)`);
  if (b.note) lines.push(`NOTE: ${b.note}`);
  return lines.join("\n");
}

export function renderContext(r: Retrieval): string {
  if (!r.blocks.length) return "(No matching rules were found in the index.)";
  const byId = new Map(r.blocks.map((b) => [b.node.id, b]));
  // Keep each update directly after the first rule it amends.
  const ordered: Block[] = [];
  const placed = new Set<number>();
  const place = (b: Block) => {
    if (placed.has(b.source.n)) return;
    placed.add(b.source.n);
    ordered.push(b);
    for (const n of b.source.amended_by ?? []) {
      const u = r.blocks.find((x) => x.source.n === n);
      if (u) place(u);
    }
  };
  r.blocks.forEach(place);
  return ordered.map((b) => `${header(b, byId)}\n\n${b.body}`).join("\n\n----------\n\n");
}

/** Citation numbers the answer actually used, e.g. "[1][3]" -> [1, 3]. */
export function citedNumbers(answer: string, max: number): number[] {
  const seen = new Set<number>();
  for (const m of answer.matchAll(/\[(\d{1,2})\]/g)) {
    const n = Number(m[1]);
    if (n >= 1 && n <= max) seen.add(n);
  }
  return [...seen].sort((a, b) => a - b);
}
