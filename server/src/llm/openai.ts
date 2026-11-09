import OpenAI from "openai";
import { config } from "../config.js";
import { LLMUnavailable, type LLMProvider, type StreamRequest } from "./provider.js";

export class OpenAIProvider implements LLMProvider {
  readonly name = "openai";
  private client: OpenAI | null = null;

  model(voice = false): string {
    return voice ? config.llm.voiceModel || "gpt-4.1-mini" : config.llm.textModel || "gpt-4.1";
  }

  async *stream(req: StreamRequest): AsyncIterable<string> {
    if (!process.env.OPENAI_API_KEY) throw new LLMUnavailable("OPENAI_API_KEY is not set");
    this.client ??= new OpenAI();
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
