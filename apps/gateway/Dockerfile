# Tier 1 - public API gateway + admin dashboard (static React build).
# No credentials: checkout and webhooks pass through to Tier 2 untouched.
# Build context = repo root.
# The dashboard is an npm workspace of the repo root (hoisted lockfile).
FROM node:22-slim AS dashboard
WORKDIR /ui
COPY package.json package-lock.json ./
COPY apps/dashboard/package.json apps/dashboard/package.json
RUN npm ci --workspace epyhia-dashboard
COPY apps/dashboard/ apps/dashboard/
RUN npm run build --workspace epyhia-dashboard

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY . .
COPY --from=dashboard /ui/apps/dashboard/dist apps/dashboard/dist
RUN uv sync --frozen --no-dev --package gateway

ENV GATEWAY_PORT=8080
EXPOSE 8080
CMD ["uv", "run", "--no-sync", "python", "-m", "gateway.main"]
