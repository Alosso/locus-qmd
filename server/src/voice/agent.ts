/**
 * LiveKit voice agent: streaming STT -> retrieval -> LLM -> streaming TTS over WebRTC.
 *
 * Started by src/index.ts as a child process (`agent.js start`). The LiveKit worker then
 * forks one job process per call. Retrieval is requested from the main server over
 * loopback, so the qmd index, embedding model and graph are loaded exactly once.
 *
 * Latency design (target: first audio < 1 s after the player stops talking):
 *   - turn detection model + short endpointing delay instead of a long silence timeout
 *   - preemptive generation: the LLM starts on the predicted end of turn
 *   - retrieval without reranking (about 75 ms warm), cached for the preemptive retry
 *   - a small, fast LLM with reasoning off and a spoken-style prompt (short answers)
 *   - TTS consumes the token stream sentence by sentence
 */
import { config } from "../config.js";
import { fileURLToPath } from "node:url";
import { ReadableStream } from "node:stream/web";
import { cli, defineAgent, inference, llm, ServerOptions, voice, type JobContext, type JobProcess } from "@livekit/agents";
import * as anthropicPlugin from "@livekit/agents-plugin-anthropic";
import * as livekitPlugin from "@livekit/agents-plugin-livekit";
import * as openaiPlugin from "@livekit/agents-plugin-openai";
import * as silero from "@livekit/agents-plugin-silero";
import { getProvider, type ChatTurn } from "../llm/provider.js";
import { userTurn, VOICE_SYSTEM } from "../llm/prompts.js";
import { retrievalQuery } from "../answer.js";
import type { Source } from "../retrieval/retriever.js";

const INTERNAL = process.env.LOCUS_INTERNAL_URL ?? `http://127.0.0.1:${config.port}`;

interface Retrieved {
  sources: Source[];
  context: string;
  timings: { total_ms: number };
}

async function fetchRules(query: string, faction: string): Promise<Retrieved> {
  const url = new URL("/api/search", INTERNAL);
  url.searchParams.set("q", query);
  url.searchParams.set("mode", "fast");
  url.searchParams.set("context", "1");
  if (faction) url.searchParams.set("faction", faction);
  const res = await fetch(url, {
    headers: config.authToken ? { Authorization: `Bearer ${config.authToken}` } : {},
    signal: AbortSignal.timeout(4000),
  });
  if (!res.ok) throw new Error(`retrieval failed: HTTP ${res.status}`);
  return (await res.json()) as Retrieved;
}

/** Chat history as alternating user/assistant turns starting with a user turn. */
function turnsFrom(chatCtx: llm.ChatContext): ChatTurn[] {
  const turns: ChatTurn[] = [];
  for (const item of chatCtx.items) {
    if (item.type !== "message" || (item.role !== "user" && item.role !== "assistant")) continue;
    const content = (item.textContent ?? "").trim();
    if (!content) continue;
    const last = turns.at(-1);
    if (last && last.role === item.role) last.content += ` ${content}`;
    else turns.push({ role: item.role, content });
  }
  while (turns.length && turns[0].role !== "user") turns.shift();
  return turns;
}

class RulesAgent extends voice.Agent {
  constructor(
    private readonly ctx: JobContext,
    private readonly faction: string,
  ) {
    super({ instructions: VOICE_SYSTEM });
  }

  /**
   * Replaces the default LLM step. Every reply is grounded: the rules are retrieved for
   * the latest user turn and placed in the prompt, so the model never answers unaided.
   */
  override async llmNode(chatCtx: llm.ChatContext): Promise<ReadableStream<string> | null> {
    const turns = turnsFrom(chatCtx);
    const last = turns.at(-1);
    if (!last || last.role !== "user") return null;
    const history = turns.slice(0, -1).slice(-6);
    const question = last.content;
    const ctx = this.ctx;
    const faction = this.faction;

    return new ReadableStream<string>({
      async start(controller) {
        try {
          const t0 = performance.now();
          const rules = await fetchRules(retrievalQuery(question, history), faction);
          // Citations go to the client as data; reading them aloud would waste the player's time.
          const payload = JSON.stringify({ type: "sources", question, sources: rules.sources });
          void ctx.room.localParticipant
            ?.publishData(new TextEncoder().encode(payload), { reliable: true, topic: "locus.sources" })
            .catch(() => {});
          const provider = await getProvider();
          let first = true;
          for await (const delta of provider.stream({ system: VOICE_SYSTEM, history, user: userTurn(question, rules.context), voice: true })) {
            if (first) {
              first = false;
              console.log(`voice turn: retrieval ${rules.timings.total_ms} ms, first token ${Math.round(performance.now() - t0)} ms`);
            }
            controller.enqueue(delta);
          }
        } catch (err) {
          console.error("voice turn failed:", err);
          controller.enqueue("Sorry, I could not look that up just now. Please ask again.");
        }
        controller.close();
      },
    });
  }
}

export default defineAgent({
  prewarm: async (proc: JobProcess) => {
    proc.userData.vad = await silero.VAD.load();
  },
  entry: async (ctx: JobContext) => {
    await ctx.connect();
    let faction = "";
    try {
      faction = JSON.parse(ctx.job.metadata || "{}").faction ?? "";
    } catch {
      /* no metadata */
    }
    const lk = config.livekit;
    // The session needs an LLM object for its bookkeeping; generation itself runs in llmNode above.
    const sessionLlm =
      config.llm.provider === "openai"
        ? new openaiPlugin.LLM({ model: config.llm.voiceModel || "gpt-4.1-mini" })
        : new anthropicPlugin.LLM({ model: config.llm.voiceModel || "claude-haiku-5-5" });
    const session = new voice.AgentSession({
      // "provider/model" strings are served by LiveKit Inference with the LiveKit key alone.
      stt: lk.stt as inference.STTModelString,
      tts: lk.ttsVoice ? new inference.TTS({ model: lk.tts as inference.TTSModels, voice: lk.ttsVoice }) : (lk.tts as inference.TTSModelString),
      llm: sessionLlm,
      vad: ctx.proc.userData.vad as silero.VAD,
      turnHandling: {
        turnDetection: new livekitPlugin.turnDetector.MultilingualModel(),
        endpointing: { minDelay: 300, maxDelay: 2500 },
        preemptiveGeneration: { enabled: true },
      },
    });
    await session.start({ agent: new RulesAgent(ctx, faction), room: ctx.room });
    session.say("Rules assistant ready. What do you need?", { allowInterruptions: true });
  },
});

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  cli.runApp(
    new ServerOptions({
      agent: fileURLToPath(import.meta.url),
      agentName: config.livekit.agentName,
      // Each idle job process holds the VAD and turn-detection models in memory.
      numIdleProcesses: Number(process.env.VOICE_IDLE_PROCESSES ?? 1),
    }),
  );
}
