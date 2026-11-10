/**
 * Google Gemini (AI Studio key) through Google's OpenAI-compatible endpoint.
 * Set LLM_PROVIDER=google and GEMINI_API_KEY (GOOGLE_API_KEY is accepted too).
 */
import OpenAI from "openai";
import { config } from "../config.js";
import { LLMUnavailable, type LLMProvider, type StreamRequest } from "./provider.js";

export const GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/";
export const GEMINI_TEXT_MODEL = "gemini-2.5-flash";
export const GEMINI_VOICE_MODEL = "gemini-2.5-flash-lite";

export const geminiKey = (): string => process.env.GEMINI_API_KEY?.trim() || process.env.GOOGLE_API_KEY?.trim() || "";

export class GoogleProvider implements LLMProvider {
  readonly name = "google";
  private client: OpenAI | null = null;

  model(voice = false): string {
    return voice ? config.llm.voiceModel || GEMINI_VOICE_MODEL : config.llm.textModel || GEMINI_TEXT_MODEL;
  }

  async *stream(req: StreamRequest): AsyncIterable<string> {
    const apiKey = geminiKey();
    if (!apiKey) throw new LLMUnavailable("GEMINI_API_KEY is not set");
    this.client ??= new OpenAI({ apiKey, baseURL: GEMINI_BASE_URL });
    const stream = await this.client.chat.completions.create(
      {
        model: this.model(req.voice),
        stream: true,
        messages: [{ role: "system", content: req.system }, ...req.history, { role: "user", content: req.user }],
      },
      { signal: req.signal },
    );
    for await (const chunk of stream) {
      const delta = chunk.choices[0]?.delta?.content;
      if (delta) yield delta;
    }
  }
}
