/** REST + Server-Sent Events API. */
import { timingSafeEqual } from "node:crypto";
import { existsSync } from "node:fs";
import express, { type NextFunction, type Request, type Response } from "express";
import { AccessToken, RoomAgentDispatch, RoomConfiguration } from "livekit-server-sdk";
import { answer } from "../answer.js";
import { config, readManifest } from "../config.js";
import type { ChatTurn } from "../llm/provider.js";
import { renderContext } from "../retrieval/context.js";
import { buildGraph } from "../retrieval/graph.js";
import { buildIndex } from "../retrieval/qmd.js";
import { clearCaches, getGraph, reloadGraph, retrieve, ruleDetail } from "../retrieval/retriever.js";

export function tokenOk(token: string | undefined): boolean {
  if (!config.authToken) return true; // open in local development
  if (!token) return false;
  const a = Buffer.from(token);
  const b = Buffer.from(config.authToken);
  return a.length === b.length && timingSafeEqual(a, b);
}

function requireAuth(req: Request, res: Response, next: NextFunction): void {
  const header = req.headers.authorization?.replace(/^Bearer\s+/i, "");
  if (tokenOk(header)) return next();
  res.status(401).json({ error: "unauthorized", message: "Send the API token as 'Authorization: Bearer <token>'." });
}

function parseHistory(raw: unknown): ChatTurn[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((t): t is ChatTurn => !!t && (t.role === "user" || t.role === "assistant") && typeof t.content === "string")
    .map((t) => ({ role: t.role, content: t.content.slice(0, 4000) }))
    .slice(-8);
}

let reindexing: Promise<unknown> | null = null;

export function createApp(state: { ready: boolean; voice: boolean }): express.Express {
  const app = express();
  app.disable("x-powered-by");
  app.use(express.json({ limit: "200kb" }));

  app.get("/healthz", (_req, res) => {
    res.status(state.ready ? 200 : 503).json({ ok: state.ready, game: config.game, graph: getGraph().size, voice: state.voice });
  });

  if (existsSync(config.webDir)) app.use(express.static(config.webDir));

  const api = express.Router();
  api.use(requireAuth);

  /** Streamed, cited answer. Events: sources -> delta* -> (error)? -> done */
  api.post("/chat", async (req, res) => {
    const question = typeof req.body?.question === "string" ? req.body.question.trim() : "";
    if (!question) return void res.status(400).json({ error: "bad_request", message: "Body must include a non-empty 'question'." });
    res.writeHead(200, {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    });
    const abort = new AbortController();
    res.on("close", () => abort.abort());
    try {
      for await (const ev of answer({
        question: question.slice(0, 2000),
        history: parseHistory(req.body.history),
        faction: typeof req.body.faction === "string" ? req.body.faction : null,
        signal: abort.signal,
      })) {
        const { type, ...data } = ev;
        res.write(`event: ${type}\ndata: ${JSON.stringify(data)}\n\n`);
      }
    } catch (err) {
      res.write(`event: error\ndata: ${JSON.stringify({ code: "internal", message: (err as Error).message })}\n\n`);
    }
    res.end();
  });

  /** Retrieval only: what the model would be shown for this question. */
  api.get("/search", async (req, res) => {
    const q = String(req.query.q ?? "").trim();
    if (!q) return void res.status(400).json({ error: "bad_request", message: "Query parameter 'q' is required." });
    const r = await retrieve(q.slice(0, 2000), {
      mode: req.query.mode === "fast" ? "fast" : "deep",
      faction: typeof req.query.faction === "string" ? req.query.faction : null,
    });
    res.json({
      query: r.query,
      faction: r.faction,
      timings: r.timings,
      sources: r.sources,
      ...(req.query.context === "1" ? { context: renderContext(r) } : {}),
    });
  });

  /** One rule with its current errata and graph relationships. */
  api.get("/rules/:id", async (req, res) => {
    const detail = await ruleDetail(req.params.id);
    if (!detail) return void res.status(404).json({ error: "not_found", message: `No rule with id ${req.params.id}.` });
    res.json(detail);
  });

  api.get("/meta", (_req, res) => {
    res.json({ game: config.game, factions: getGraph().factions(), documents: readManifest(), graph: getGraph().size, voice: state.voice });
  });

  /** Short-lived LiveKit token; joining the room dispatches the voice agent to it. */
  api.post("/voice/token", async (req, res) => {
    const lk = config.livekit;
    if (!lk.url || !lk.apiKey || !lk.apiSecret) {
      return void res.status(503).json({ error: "voice_not_configured", message: "Set LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET to enable voice." });
    }
    const room = `locus-${crypto.randomUUID().slice(0, 8)}`;
    const identity = `player-${crypto.randomUUID().slice(0, 8)}`;
    const at = new AccessToken(lk.apiKey, lk.apiSecret, { identity, ttl: "15m" });
    at.addGrant({ roomJoin: true, room, canPublish: true, canSubscribe: true, canPublishData: true });
    const faction = typeof req.body?.faction === "string" ? req.body.faction : "";
    at.roomConfig = new RoomConfiguration({
      agents: [new RoomAgentDispatch({ agentName: lk.agentName, metadata: JSON.stringify({ faction }) })],
    });
    res.json({ url: lk.url, token: await at.toJwt(), room, identity });
  });

  /** Re-read corpus/ after an ingestion run: refresh the qmd index and the graph. */
  api.post("/admin/reindex", async (_req, res) => {
    if (reindexing) return void res.status(409).json({ error: "busy", message: "A reindex is already running." });
    const log: string[] = [];
    reindexing = (async () => {
      await buildIndex((m) => log.push(m));
      const g = await buildGraph();
      reloadGraph();
      clearCaches();
      return g;
    })();
    try {
      res.json({ ok: true, graph: await reindexing, log });
    } catch (err) {
      res.status(500).json({ error: "reindex_failed", message: (err as Error).message, log });
    } finally {
      reindexing = null;
    }
  });

  app.use("/api", api);
  app.use((err: Error, _req: Request, res: Response, _next: NextFunction) => {
    console.error(err);
    if (!res.headersSent) res.status(500).json({ error: "internal", message: err.message });
  });
  return app;
}
