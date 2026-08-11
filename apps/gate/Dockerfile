# Tier 3 - the Action Gate. Build context is the REPO ROOT (uv workspace).
# The only image that also carries Node: the gate shells out to wrangler for
# Cloudflare Pages deploys (the one Node dependency, DESIGN.md sec. 13).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN uv sync --frozen --no-dev --package gate \
    && npm ci --omit=dev

ENV GATE_PORT=8082
EXPOSE 8082
CMD ["uv", "run", "--no-sync", "python", "-m", "gate.main"]
