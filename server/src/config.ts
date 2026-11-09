/**
 * Central configuration. Import this module first: it sets XDG_CACHE_HOME so qmd keeps
 * its GGUF models on the persistent data volume instead of the container filesystem.
 */
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import dotenv from "dotenv";

function findRoot(): string {
  if (process.env.LOCUS_ROOT) return path.resolve(process.env.LOCUS_ROOT);
  let dir = path.dirname(fileURLToPath(import.meta.url));
  for (let i = 0; i < 6; i++) {
    if (existsSync(path.join(dir, "games"))) return dir;
    dir = path.dirname(dir);
  }
  return process.cwd();
}

export const ROOT = findRoot();
dotenv.config({ path: path.join(ROOT, ".env"), quiet: true });

const env = (k: string, d = ""): string => process.env[k]?.trim() || d;
const num = (k: string, d: number): number => (process.env[k] ? Number(process.env[k]) : d);

const dataDir = path.resolve(ROOT, env("DATA_DIR", "data"));
mkdirSync(path.join(dataDir, "qmd"), { recursive: true });
process.env.XDG_CACHE_HOME ||= path.join(dataDir, "cache");

const game = env("GAME", "wh40k");
const corpusDir = path.resolve(ROOT, env("CORPUS_DIR", "corpus"), game);

export interface ManifestEntry {
  source_file: string;
  sha256: string;
  document_version: string | null;
  printed_version: string | null;
  effective_date: string | null;
  document_title: string | null;
  doc_slug: string;
  collection: string;
  units: number;
}

export function readManifest(): Record<string, ManifestEntry> {
  const p = path.join(corpusDir, "_manifest.json");
  return existsSync(p) ? JSON.parse(readFileSync(p, "utf8")) : {};
}

export const config = {
  root: ROOT,
  game,
  corpusDir,
  dataDir,
  qmdDbPath: path.join(dataDir, "qmd", `${game}.sqlite`),
  graphDbPath: path.join(dataDir, `${game}-graph.sqlite`),
  graphJsonl: path.join(corpusDir, "graph.jsonl"),
  webDir: path.join(ROOT, "web"),
  port: num("PORT", 8080),
  host: env("HOST", "0.0.0.0"),
  authToken: env("API_AUTH_TOKEN"),
  llm: {
    provider: env("LLM_PROVIDER", "anthropic") as "anthropic" | "openai",
    textModel: env("LLM_TEXT_MODEL"),
    voiceModel: env("LLM_VOICE_MODEL"),
  },
  retrieval: {
    topK: num("RETRIEVAL_TOP_K", 6),
    voiceTopK: num("RETRIEVAL_VOICE_TOP_K", 4),
    // Cross-encoder reranking adds seconds per query on CPU; enable it only on GPU hosts.
    rerank: env("RETRIEVAL_RERANK", "false") === "true",
    contextChars: num("RETRIEVAL_CONTEXT_CHARS", 14000),
    voiceContextChars: num("RETRIEVAL_VOICE_CONTEXT_CHARS", 6000),
  },
  livekit: {
    url: env("LIVEKIT_URL"),
    apiKey: env("LIVEKIT_API_KEY"),
    apiSecret: env("LIVEKIT_API_SECRET"),
    agentName: env("LIVEKIT_AGENT_NAME", "locus-rules"),
    stt: env("STT_MODEL", "deepgram/nova-3"),
    tts: env("TTS_MODEL", "cartesia/sonic-3"),
    ttsVoice: env("TTS_VOICE"),
  },
};

export type Config = typeof config;
