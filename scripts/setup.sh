#!/usr/bin/env bash
# Locus setup: from a fresh clone (plus your PDFs) to a running server.
#
#   bash scripts/setup.sh            # install, ingest, index
#   bash scripts/setup.sh --llm      # also run the Claude clean-up pass during ingestion
#   bash scripts/setup.sh --force    # re-parse every PDF, ignoring the parse cache
#
# Works on Linux, macOS and Windows (Git Bash). Safe to re-run: every step is incremental.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
GAME="${GAME:-wh40k}"
INGEST_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --llm|--force) INGEST_ARGS+=("$arg") ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n==> %s\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "error: $1 is required but not installed" >&2; exit 1; }; }

step "Checking prerequisites"
need node; need npm
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
[ "$NODE_MAJOR" -ge 22 ] || { echo "error: Node.js 22 or newer is required (found $(node -v))" >&2; exit 1; }
PY="$(command -v python3 || command -v python || true)"
[ -n "$PY" ] || { echo "error: Python 3.11 or newer is required" >&2; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || { echo "error: Python 3.11 or newer is required (found $("$PY" -V))" >&2; exit 1; }
echo "node $(node -v), $("$PY" -V)"

step "Source documents"
PDFS="$(find files -iname '*.pdf' 2>/dev/null | wc -l | tr -d ' ')"
if [ "$PDFS" -eq 0 ]; then
  echo "error: no PDFs under files/. Put the rulebooks where games/$GAME/game.yml expects them:" >&2
  grep -E '^\s+- path:' "games/$GAME/game.yml" | sed 's/^/  /' >&2
  exit 1
fi
echo "$PDFS PDF(s) found"

step "Python environment (ingestion)"
[ -d .venv ] || "$PY" -m venv .venv
if [ -x .venv/Scripts/python.exe ]; then VPY=.venv/Scripts/python.exe; else VPY=.venv/bin/python; fi
"$VPY" -m pip install --quiet --disable-pip-version-check -r ingest/requirements.txt

step "Node dependencies (server)"
if [ -f server/package-lock.json ]; then npm --prefix server ci --no-fund --no-audit; else npm --prefix server install --no-fund --no-audit; fi

step "Environment file"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "created .env from .env.example: add your API keys there"
else
  echo ".env already exists"
fi

step "Ingesting PDFs -> corpus/$GAME (Markdown + graph)"
PYTHONPATH=ingest PYTHONIOENCODING=utf-8 "$VPY" -m locus_ingest run --game "$GAME" "${INGEST_ARGS[@]}"

step "Building the qmd index (full-text + embeddings)"
echo "The first run downloads the embedding model (about 300 MB)."
npm --prefix server run --silent index

step "Building the knowledge graph"
npm --prefix server run --silent graph

step "Checking retrieval"
npm --prefix server run --silent eval || echo "warning: some retrieval checks failed (see above)"

cat <<EOF

Setup complete.

  Start the server:    npm --prefix server run serve
  Open the client:     http://localhost:8080
  Ask from a shell:    curl -N -X POST http://localhost:8080/api/chat \\
                         -H 'Content-Type: application/json' \\
                         -d '{"question":"Can a unit charge after disembarking?"}'

Add ANTHROPIC_API_KEY (answers) and the LIVEKIT_* values (voice) to .env first.
After adding or replacing PDFs, re-run this script; only changed files are processed.
EOF
