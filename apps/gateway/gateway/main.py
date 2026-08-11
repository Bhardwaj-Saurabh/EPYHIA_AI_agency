"""Tier 1 - Public API Gateway. Public ingress, no credentials.
Auth0 login, admin dashboard (static React build), checkout API, and raw
Stripe webhook passthrough land here per DESIGN.md section 2."""

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI

REPO_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(REPO_ROOT / ".env")

app = FastAPI(title="epyhia-gateway")


@app.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "ok", "app": "gateway"}


@app.get("/health/ready")
def health_ready() -> dict[str, str]:
    workers = "configured" if os.environ.get("WORKERS_URL") else "not configured"
    return {"status": "ok", "app": "gateway", "workers": workers}


def serve() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("GATEWAY_PORT", "8080")))


if __name__ == "__main__":
    serve()
