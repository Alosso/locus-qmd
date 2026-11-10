/**
 * Google Gemini (AI Studio key) through Google's OpenAI-compatible endpoint.
 * Set LLM_PROVIDER=google and GEMINI_API_KEY (GOOGLE_API_KEY is accepted too).
 */
import OpenAI from "openai";
import { config } from "../config.js";
import { LLMUnavailable, type LLMProvider, type StreamRequest } from "./provider.js";

export const GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/";
export const GEMINI_TEXT_MODEL = "gemini-3.8-flash";
export const GEMINI_VOICE_MODEL = "gemini-3.5-flash-lite";
/** Tried in order when the chosen model is overloaded or rate limited before any output. */
const FALLBACKS = (process.env.GEMINI_FALLBACK_MODELS ?? "gemini-3.5-flash-lite,gemini-flash-lite-latest")
  .split(",")
  .map((m) => m.trim())
  .filter(Boolean);

export const geminiKey = (): string => process.env.GEMINI_API_KEY?.trim() || process.env.GOOGLE_API_KEY?.trim() || "";

export class GoogleProvider implements LLMProvider {
  readonly name = "google";
  private client: OpenAI | null = null;
  private served: Record<string, string> = {};

  /** The model that actually answered last (differs from the configured one after a fallback). */
  model(voice = false): string {
    const wanted = voice ? config.llm.voiceModel || GEMINI_VOICE_MODEL : config.llm.textModel || GEMINI_TEXT_MODEL;
    return this.served[wanted] ?? wanted;
  }

  async *stream(req: StreamRequest): AsyncIterable<string> {
    const apiKey = geminiKey();
    if (!apiKey) throw new LLMUnavailable("GEMINI_API_KEY is not set");
    // Retries are handled here by moving to the next model, which is faster than waiting
    // out an overloaded one (the SDK default would back off on the same model first).
    this.client ??= new OpenAI({ apiKey, baseURL: GEMINI_BASE_URL, maxRetries: 0 });
    const wanted = req.voice ? config.llm.voiceModel || GEMINI_VOICE_MODEL : config.llm.textModel || GEMINI_TEXT_MODEL;
    const chain = [wanted, ...FALLBACKS.filter((m) => m !== wanted)];
    let lastErr: unknown;
    for (const model of chain) {
      let started = false;
      try {
        const stream = await this.client.chat.completions.create(
          {
            model,
            stream: true,
            messages: [{ role: "system", content: req.system }, ...req.history, { role: "user", content: req.user }],
          },
          // An overloaded model can hold a request for a minute; give up early and move on.
          { signal: req.signal, timeout: req.voice ? 5_000 : 12_000 },
        );
        for await (const chunk of stream) {
          const delta = chunk.choices[0]?.delta?.content;
          if (delta) {
            started = true;
            yield delta;
          }
        }
        if (model === wanted) delete this.served[wanted];
        else this.served[wanted] = model;
        return;
      } catch (err) {
        lastErr = err;
        const status = err instanceof OpenAI.APIError ? err.status : undefined;
        const transient = status === 429 || status === 500 || status === 503 || err instanceof OpenAI.APIConnectionError;
        if (started || !transient || req.signal?.aborted) throw err;
        console.warn(`gemini: ${model} unavailable (${status ?? "connection"}), trying the next model`);
      }
    }
    throw lastErr;
  }
}
