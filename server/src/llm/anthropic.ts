import Anthropic from "@anthropic-ai/sdk";
import { config } from "../config.js";
import { LLMUnavailable, type LLMProvider, type StreamRequest } from "./provider.js";

const TEXT_MODEL = "claude-sonnet-5-5";
const VOICE_MODEL = "claude-haiku-5-5";
type Effort = "low" | "medium" | "high" | "xhigh" | "max";

export class AnthropicProvider implements LLMProvider {
  readonly name = "anthropic";
  private client: Anthropic | null = null;

  model(voice = false): string {
    return voice ? config.llm.voiceModel || VOICE_MODEL : config.llm.textModel || TEXT_MODEL;
  }

  private getClient(): Anthropic {
    if (!process.env.ANTHROPIC_API_KEY && !process.env.ANTHROPIC_AUTH_TOKEN) {
      throw new LLMUnavailable("ANTHROPIC_API_KEY is not set");
    }
    return (this.client ??= new Anthropic());
  }

  async *stream(req: StreamRequest): AsyncIterable<string> {
    const client = this.getClient();
    const model = this.model(req.voice);
    const haiku = model.includes("haiku");
    const effort = (process.env[req.voice ? "LLM_VOICE_EFFORT" : "LLM_TEXT_EFFORT"] || (req.voice ? "low" : "medium")) as Effort;
    const stream = client.messages.stream(
      {
        model,
        max_tokens: req.voice ? 1024 : 16000,
        // The instructions never change, so they are cached; the retrieved rules vary per turn.
        system: [{ type: "text", text: req.system, cache_control: { type: "ephemeral" } }],
        messages: [...req.history, { role: "user", content: req.user }],
        output_config: { effort },
        // Voice needs the first token fast. Haiku accepts disabled thinking at this effort;
        // Sonnet 5.5 and Opus 5.5 reject it, so there the effort level is the only control.
        ...(req.voice && haiku ? { thinking: { type: "disabled" as const } } : {}),
      },
      { signal: req.signal },
    );
    for await (const event of stream) {
      if (event.type === "content_block_delta" && event.delta.type === "text_delta") yield event.delta.text;
    }
    const final = await stream.finalMessage();
    if (final.stop_reason === "refusal") yield "\n\n(The model declined to answer this question.)";
    if (final.stop_reason === "max_tokens") yield "\n\n(The answer was cut off at the length limit.)";
  }
}
