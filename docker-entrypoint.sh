#!/bin/sh
# Prepare the persistent volume, then start the server.
#
# The server itself brings the index up to date on boot: `qmd update` + `embed` only touch
# Markdown files that changed since the last boot, and the graph is reloaded only when
# graph.jsonl changed. First boot on an empty volume downloads the embedding model
# (about 300 MB) and embeds the whole corpus, which takes a few minutes.
set -eu

DATA_DIR="${DATA_DIR:-/data}"
mkdir -p "$DATA_DIR/qmd" "$DATA_DIR/cache"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$DATA_DIR/cache}"

if [ ! -w "$DATA_DIR" ]; then
  echo "error: $DATA_DIR is not writable; mount a volume there" >&2
  exit 1
fi
if [ ! -f "/app/corpus/${GAME:-wh40k}/graph.jsonl" ]; then
  echo "error: no corpus in the image; run the ingestion before building (scripts/setup.sh)" >&2
  exit 1
fi

echo "locus: data volume $(du -sh "$DATA_DIR" 2>/dev/null | cut -f1) used at $DATA_DIR"
exec "$@"
