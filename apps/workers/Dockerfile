# Tier 2 - Orchestration Runtime + agents. No credentials in this image or its
# environment: it reaches everything through GATE_URL. Build context = repo root.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY . .
RUN uv sync --frozen --no-dev --package workers

ENV WORKERS_PORT=8081
EXPOSE 8081
CMD ["uv", "run", "--no-sync", "python", "-m", "workers.main"]
