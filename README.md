# Locus

Hybrid GraphRAG rules assistant for tabletop games (Warhammer 40,000 first), with a streaming text API and a LiveKit voice interface.

```
PDFs -> ingest (Python) -> corpus/<game>/**/*.md + graph.jsonl
     -> qmd index (BM25 + vectors + RRF, SQLite) + graph.sqlite
     -> retriever (qmd hits -> graph nodes -> errata overlay -> related rules)
     -> LLM (Claude by default; OpenAI or Google Gemini optional) -> SSE / WebSocket / LiveKit voice
```

Scope: the Core Rules and the Universal Rules Updates (`files/core rules`). Faction packs are not indexed, because they hold only additions and errata to the Codexes rather than complete faction rules; the parser for them is still in the repo and can be switched on in `games/wh40k/game.yml`.

The rulebook PDFs and the Markdown derived from them are not in this repository (see `.gitignore`). Put your PDFs under `files/` as listed in `games/wh40k/game.yml`.

## Setup

```bash
bash scripts/setup.sh
```

This installs dependencies, parses the PDFs, builds the qmd index and the graph, and runs the retrieval checks. Then add keys to `.env` and start the server:

```bash
npm --prefix server run serve
```

Open http://localhost:8080 for the test client.

## Ingestion

`python -m locus_ingest run --game wh40k [--force] [--llm] [--only "<glob>"]` (with `PYTHONPATH=ingest`).

- One Markdown file per rule unit, with frontmatter: `id`, `doc_type`, `document_version`, `page_start`, `is_update`, `replaces_rule_id`, `update_mode`, ...
- A newer PDF of the same document family replaces the old output; the old output moves to `corpus/<game>/_archive`.
- `--llm` re-transcribes units flagged `extraction_confidence: low` with Claude (cached).
- `corpus/<game>/qa.json` reports unit counts, low-confidence units and update targets outside the corpus.

To add a game, copy `games/wh40k` and adjust `game.yml`; add a profile under `ingest/locus_ingest/profiles` if the layout differs.

## API

All `/api` routes take `Authorization: Bearer $API_AUTH_TOKEN` when the token is set.

| Route | Purpose |
|---|---|
| `POST /api/chat` `{question, history?, faction?}` | SSE stream: `sources`, `delta`, `error`, `done` |
| `WS /ws/chat?token=` | `{type:"ask",question}`; same events as JSON frames, history kept per connection |
| `GET /api/search?q=&mode=fast\|deep&context=1` | Retrieval only |
| `GET /api/rules/:id` | A rule with its updates and graph edges |
| `POST /api/voice/token` | LiveKit token; joining the room dispatches the voice agent |
| `POST /api/admin/reindex` | Refresh index and graph after an ingestion run |
| `GET /healthz` | Readiness |

## Voice

Set `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`. The server starts the agent worker (`server/src/voice/agent.ts`) as a child process. Speech models run through LiveKit Inference; the answer LLM is the configured provider.

## Checks

```bash
npm --prefix server run eval    # 20 retrieval questions, no LLM needed
npm --prefix server run bench   # retrieval and first-token latency
```

## Deploy (Fly.io)

Run the ingestion locally first so `corpus/` exists, then:

```bash
fly launch --no-deploy --copy-config
```

```bash
fly volumes create locus_data --size 3
```

```bash
fly secrets set ANTHROPIC_API_KEY=... API_AUTH_TOKEN=... LIVEKIT_URL=... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=...
```

```bash
fly deploy
```

The first boot downloads the embedding model to the volume and embeds the corpus, which takes a few minutes.
