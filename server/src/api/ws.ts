/**
 * WebSocket chat: one connection is one conversation (history is kept server-side).
 *
 *   client -> { "type": "ask", "question": "...", "faction": "World Eaters"? }
 *             { "type": "cancel" } | { "type": "reset" }
 *   server -> { "type": "sources" | "delta" | "error" | "done", ... }  (same payloads as the SSE API)
 *
 * Authenticate with ?token=<API_AUTH_TOKEN> or an Authorization header.
 */
import type { Server } from "node:http";
import { WebSocketServer, type WebSocket } from "ws";
import { answer } from "../answer.js";
import type { ChatTurn } from "../llm/provider.js";
import { tokenOk } from "./http.js";

export function attachWebSocket(server: Server): WebSocketServer {
  const wss = new WebSocketServer({ noServer: true });

  server.on("upgrade", (req, socket, head) => {
    const url = new URL(req.url ?? "/", "http://localhost");
    if (url.pathname !== "/ws/chat") return void socket.destroy();
    const token = url.searchParams.get("token") ?? req.headers.authorization?.replace(/^Bearer\s+/i, "");
    if (!tokenOk(token ?? undefined)) {
      socket.write("HTTP/1.1 401 Unauthorized\r\n\r\n");
      return void socket.destroy();
    }
    wss.handleUpgrade(req, socket, head, (ws) => wss.emit("connection", ws, req));
  });

  wss.on("connection", (ws: WebSocket) => {
    const history: ChatTurn[] = [];
    let current: AbortController | null = null;
    const send = (obj: unknown) => ws.readyState === ws.OPEN && ws.send(JSON.stringify(obj));

    ws.on("message", async (raw) => {
      let msg: { type?: string; question?: string; faction?: string };
      try {
        msg = JSON.parse(raw.toString());
      } catch {
        return void send({ type: "error", code: "bad_request", message: "Messages must be JSON." });
      }
      if (msg.type === "cancel") return void current?.abort();
      if (msg.type === "reset") {
        current?.abort();
        history.length = 0;
        return void send({ type: "reset" });
      }
      const question = typeof msg.question === "string" ? msg.question.trim().slice(0, 2000) : "";
      if (msg.type !== "ask" || !question) {
        return void send({ type: "error", code: "bad_request", message: "Send { type: 'ask', question: '...' }." });
      }
      current?.abort();
      const abort = (current = new AbortController());
      let text = "";
      try {
        for await (const ev of answer({ question, history, faction: msg.faction ?? null, signal: abort.signal })) {
          if (ev.type === "delta") text += ev.text;
          send(ev);
        }
      } catch (err) {
        send({ type: "error", code: "internal", message: (err as Error).message });
      }
      if (text && !abort.signal.aborted) {
        history.push({ role: "user", content: question }, { role: "assistant", content: text });
        if (history.length > 16) history.splice(0, history.length - 16);
      }
      if (current === abort) current = null;
    });

    ws.on("close", () => current?.abort());
  });
  return wss;
}
