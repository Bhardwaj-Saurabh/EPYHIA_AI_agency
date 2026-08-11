# Tier 1 - public API gateway. No credentials: checkout and webhooks pass
# through to Tier 2 untouched. Build context = repo root.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY . .
RUN uv sync --frozen --no-dev --package gateway

ENV GATEWAY_PORT=8080
EXPOSE 8080
CMD ["uv", "run", "--no-sync", "python", "-m", "gateway.main"]
