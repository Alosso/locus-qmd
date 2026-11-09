# Locus: API server + voice worker. The Markdown corpus is baked into the image; the
# qmd index, embedding model cache and graph database live on the /data volume.
#
# Build from the repository root after running the ingestion (corpus/ must exist):
#   docker build -t locus .
FROM node:24-bookworm-slim AS build
WORKDIR /app/server
COPY server/package.json server/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY server/tsconfig.json ./
COPY server/src ./src
COPY server/scripts ./scripts
RUN npm run build && npm prune --omit=dev

FROM node:24-bookworm-slim
# libgomp: llama.cpp (qmd embeddings). tini: signal handling for the worker child processes.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates libgomp1 tini \
 && rm -rf /var/lib/apt/lists/*
ENV NODE_ENV=production \
    LOCUS_ROOT=/app \
    DATA_DIR=/data \
    PORT=8080 \
    HOST=0.0.0.0
WORKDIR /app
COPY --from=build /app/server/node_modules ./server/node_modules
COPY --from=build /app/server/dist ./server/dist
COPY server/package.json ./server/package.json
COPY games ./games
COPY web ./web
COPY corpus ./corpus
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
VOLUME /data
EXPOSE 8080
ENTRYPOINT ["tini", "--", "docker-entrypoint.sh"]
CMD ["node", "server/dist/src/index.js"]
