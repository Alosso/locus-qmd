/** Provider-neutral streaming interface. Select with LLM_PROVIDER=anthropic|openai. */
import { config } from "../config.js";

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export interface StreamRequest {
  system: string;
  /** Prior turns as plain question/answer text (retrieved context is not replayed). */
  history: ChatTurn[];
  /** Final user turn, already containing the retrieved rules. */
  user: string;
  voice?: boolean;
  signal?: AbortSignal;
}

export interface LLMProvider {
  readonly name: string;
  model(voice?: boolean): string;
  /** Yields text deltas. Throws LLMUnavailable when no credentials are configured. */
  stream(req: StreamRequest): AsyncIterable<string>;
}

export class LLMUnavailable extends Error {}

let provider: LLMProvider | null = null;

export async function getProvider(): Promise<LLMProvider> {
  if (provider) return provider;
  if (config.llm.provider === "openai") {
    provider = new (await import("./openai.js")).OpenAIProvider();
  } else {
    provider = new (await import("./anthropic.js")).AnthropicProvider();
  }
  return provider;
}
