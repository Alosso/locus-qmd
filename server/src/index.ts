/**
 * Locus server: HTTP/SSE + WebSocket API, and (when LiveKit is configured) the voice
 * agent worker as a supervised child process.
 */
import { config } from "./config.js";
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { createServer } from "node:http";
import { fileURLToPath } from "node:url";
import { createApp } from "./api/http.js";
import { attachWebSocket } from "./api/ws.js";
import { buildGraph } from "./retrieval/graph.js";
import { buildIndex, closeStore, collectionNames } from "./retrieval/qmd.js";
import { warmup } from "./retrieval/retriever.js";

const voiceEnabled = !!(config.livekit.url && config.livekit.apiKey && config.livekit.apiSecret);
const state = { ready: false, voice: voiceEnabled };
const server = createServer(createApp(state));
attachWebSocket(server);

let worker: ChildProcess | null = null;
let stopping = false;

function startVoiceWorker(): void {
  const ext = import.meta.url.endsWith(".ts") ? "ts" : "js";
  const entry = fileURLToPath(new URL(`./voice/agent.${ext}`, import.meta.url));
  // The LiveKit worker forks one process per call; those processes ask this server for
  // retrieval over loopback, so the search index and embedding model are loaded once.
  worker = spawn(process.execPath, [...process.execArgv, entry, ext === "ts" ? "dev" : "start"], {
    stdio: "inherit",
    env: { ...process.env, LOCUS_INTERNAL_URL: `http://127.0.0.1:${config.port}`, LIVEKIT_AGENT_NAME: config.livekit.agentName },
  });
  worker.on("exit", (code) => {
    worker = null;
    if (stopping) return;
    console.error(`voice worker exited (code ${code}); restarting in 5 s`);
    setTimeout(startVoiceWorker, 5000);
  });
}

async function main(): Promise<void> {
  if (!collectionNames().length || !existsSync(config.graphJsonl)) {
    throw new Error(`No corpus at ${config.corpusDir}. Run the ingestion first (scripts/setup.sh).`);
  }
  server.listen(config.port, config.host, () => console.log(`locus listening on http://${config.host}:${config.port} (preparing index...)`));

  // Idempotent: a no-op when the index and graph on the data volume match the corpus.
  await buildIndex();
  const g = await buildGraph();
  console.log(`graph: ${g.nodes} nodes, ${g.edges} edges${g.rebuilt ? " (rebuilt)" : ""}`);
  console.log(`retrieval warm in ${await warmup()} ms`);
  state.ready = true;

  if (!config.authToken) console.warn("API_AUTH_TOKEN is not set: the API is open. Set it before exposing this server.");
  if (voiceEnabled) startVoiceWorker();
  else console.log("voice disabled (LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET not set)");
  console.log("ready");
}

async function shutdown(): Promise<void> {
  if (stopping) return;
  stopping = true;
  worker?.kill("SIGTERM");
  server.close();
  await closeStore().catch(() => {});
  process.exit(0);
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

main().catch((err) => {
  console.error("startup failed:", err);
  process.exit(1);
});
