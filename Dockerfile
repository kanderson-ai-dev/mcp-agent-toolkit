# ── frontend build stage ─────────────────────────────────────────────────
# The web console (src/web) serves frontend/dist/ statically — build it in
# its own stage so the agent-only path stays untouched and the web service
# ships a real production bundle.
FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Example chips are injected at build time from the versioned eval dataset.
COPY evaluation/dataset.json /app/evaluation/dataset.json
RUN npm run build

# ── build stage ──────────────────────────────────────────────────────────
FROM node:22-alpine AS build
WORKDIR /app

# better-sqlite3 compiles a native binding on musl; needs a toolchain
RUN apk add --no-cache python3 make g++

COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json tsconfig.build.json ./
COPY src ./src
COPY scripts ./scripts
COPY data/sandbox ./data/sandbox

# Compile, then seed the fixture DB while devDependencies (tsx) are present,
# then drop dev deps so node_modules below is production-only.
RUN npm run build \
 && npm run seed:db \
 && npm prune --omit=dev

# ── runtime stage ────────────────────────────────────────────────────────
FROM node:22-alpine
ENV NODE_ENV=production
WORKDIR /app

COPY --from=build /app/package.json ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY --from=build /app/data/demo.db ./data/demo.db
COPY --from=build /app/data/sandbox ./data/sandbox
# Web console static bundle — src/web serves this when present.
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# The sandbox dir is the only writable surface the tools can touch.
RUN chown -R node:node /app/data
USER node

# MCP runs over stdio: the client process spawns the server as a child
# (dist/server/index.js) — both sides of the protocol live in this image.
ENTRYPOINT ["node", "dist/client/index.js"]
CMD ["Which security and observability reports exist in the internal database? Save a cited one-paragraph summary to briefing.md"]
