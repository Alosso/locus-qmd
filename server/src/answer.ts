/** One question -> retrieval -> grounded streamed answer. Shared by REST, WebSocket and voice. */
import { config } from "./config.js";
import { getProvider, LLMUnavailable, type ChatTurn } from "./llm/provider.js";
import { TEXT_SYSTEM, userTurn, VOICE_SYSTEM } from "./llm/prompts.js";
import { citedNumbers, renderContext } from "./retrieval/context.js";
import { retrieve, type Retrieval, type Source } from "./retrieval/retriever.js";

export type AnswerEvent =
  | { type: "sources"; sources: Source[]; faction: string | null; timings: Retrieval["timings"] }
  | { type: "delta"; text: string }
  | { type: "done"; cited: number[]; model: string | null; timings: { retrieval_ms: number; first_token_ms: number | null; total_ms: number } }
  | { type: "error"; message: string; code: string };

export interface AnswerRequest {
  question: string;
  history?: ChatTurn[];
  faction?: string | null;
  voice?: boolean;
  signal?: AbortSignal;
}

/**
 * Follow-ups such as "and if it advanced instead?" carry no searchable subject, so the
 * previous question is prepended for retrieval only (the model still sees the real turn).
 */
export function retrievalQuery(question: string, history: ChatTurn[] = []): string {
  const prev = [...history].reverse().find((t) => t.role === "user")?.content;
  return prev && question.trim().split(/\s+/).length < 9 ? `${prev} ${question}` : question;
}

export async function* answer(req: AnswerRequest): AsyncGenerator<AnswerEvent> {
  const t0 = performance.now();
  const history = (req.history ?? []).slice(-8);
  const r = await retrieve(retrievalQuery(req.question, history), { mode: req.voice ? "fast" : "deep", faction: req.faction });
  yield { type: "sources", sources: r.sources, faction: r.faction, timings: r.timings };
  const retrievalMs = Math.round(performance.now() - t0);

  let text = "";
  let firstToken: number | null = null;
  let model: string | null = null;
  try {
    const provider = await getProvider();
    model = provider.model(req.voice);
    const deltas = provider.stream({
      system: req.voice ? VOICE_SYSTEM : TEXT_SYSTEM,
      history,
      user: userTurn(req.question, renderContext(r)),
      voice: req.voice,
      signal: req.signal,
    });
    for await (const d of deltas) {
      firstToken ??= Math.round(performance.now() - t0);
      text += d;
      yield { type: "delta", text: d };
    }
  } catch (err) {
    if (req.signal?.aborted) return;
    const unavailable = err instanceof LLMUnavailable;
    yield {
      type: "error",
      code: unavailable ? "llm_not_configured" : "llm_error",
      message: unavailable
        ? `No LLM credentials (${(err as Error).message}). Retrieval works; set the key for ${config.llm.provider} to get answers.`
        : `The language model request failed: ${(err as Error).message}`,
    };
  }
  yield {
    type: "done",
    cited: citedNumbers(text, r.sources.length),
    model,
    timings: { retrieval_ms: retrievalMs, first_token_ms: firstToken, total_ms: Math.round(performance.now() - t0) },
  };
}
