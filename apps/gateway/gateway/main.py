"""Tier 1 - Public API Gateway. Public ingress, no credentials.
Auth0 login and the admin dashboard (static React build) land here later;
today it carries the customer checkout path and raw Stripe webhook intake
(DESIGN.md section 2). Everything is deterministic passthrough to Tier 2."""

import os
import uuid
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

REPO_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(REPO_ROOT / ".env")

app = FastAPI(title="epyhia-gateway")
WORKERS = os.environ.get("WORKERS_URL", "http://localhost:8081").rstrip("/")

# The generated business sites live on *.pages.dev and call /api/checkout
# cross-origin. Nothing here is credentialed - Tier 1 holds no secrets.
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://[a-z0-9-]+\.pages\.dev|http://localhost(:\d+)?",
    allow_methods=["GET", "POST"],
    allow_headers=["content-type"],
)


# ---- Admin dashboard API: read-only proxies via Tier 2 (Tier 3 is reachable
# only from Tier 2, DESIGN.md sec. 2). Writes (approvals) are DISABLED unless
# DASHBOARD_WRITES=on - never set on the public deployment; the admin runs the
# stack locally to approve. Auth0 replaces this switch later.

WRITES = os.environ.get("DASHBOARD_WRITES", "").lower() == "on"


@app.get("/admin/api/config")
def admin_config() -> dict[str, Any]:
    return {"writesEnabled": WRITES}


@app.get("/admin/api/{path:path}")
def admin_read(path: str) -> Any:
    allowed = path == "runs" or path == "approvals" or (
        path.startswith("runs/") and path.count("/") <= 2
    )
    if not allowed:
        return JSONResponse(status_code=404, content={"error": "unknown admin view"})
    res = httpx.get(f"{WORKERS}/admin/{path}", timeout=30)
    return JSONResponse(status_code=res.status_code, content=res.json())


@app.post("/admin/api/approvals/{action_id}/approve")
def admin_approve(action_id: str, body: dict[str, Any]) -> Any:
    if not WRITES:
        return JSONResponse(
            status_code=403,
            content={"error": "approvals are disabled on the public deployment - "
                     "run the dashboard locally to approve"},
        )
    res = httpx.post(f"{WORKERS}/admin/approvals/{action_id}/approve", json=body, timeout=600)
    return JSONResponse(status_code=res.status_code, content=res.json())


# The admin dashboard (React build) is served here when present - the browser
# gets a UI at "/", API callers keep JSON at /api/info.
DASHBOARD_DIST = REPO_ROOT / "apps" / "dashboard" / "dist"
if DASHBOARD_DIST.is_dir():
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=DASHBOARD_DIST / "assets"), name="assets")

    @app.get("/")
    def dashboard_index() -> Any:
        return FileResponse(DASHBOARD_DIST / "index.html")
else:

    @app.get("/")
    def root_fallback() -> dict[str, Any]:
        return api_info()


@app.get("/api/info")
def api_info() -> dict[str, Any]:
    """A human or script asking what this is should learn, not see a 404."""
    return {
        "service": "EPYHIA — a one-person AI agency (Assignment 4, FDE track)",
        "thisApp": "Tier 1 public API gateway (no credentials). Tier 2 agents and the "
        "Tier 3 Action Gate are private - no public inbound.",
        "demoBusiness": {
            "name": "The Biscuit Barn (pet boarding, Harrogate)",
            "liveSite": "https://epyhia-biscuit-barn.pages.dev",
            "note": "book a stay with Stripe test card 4242 4242 4242 4242 - "
            "the order persists to a real database via webhook",
        },
        "endpoints": {
            "POST /api/checkout": "booking form -> server-priced Stripe test session",
            "POST /webhooks/stripe": "raw signature-verified webhook intake",
            "GET /api/reservations/{id}": "reservation + order status from the DB",
            "GET /health/live | /health/ready": "health",
        },
        "source": "https://github.com/Bhardwaj-Saurabh/EPYHIA_AI_agency",
    }


@app.post("/api/checkout")
def post_checkout(body: dict[str, Any]) -> Any:
    """The generated site's booking form posts here. The browser supplies item
    ids, quantities, dates and customer details - never a price or tenant id."""
    body.setdefault("checkoutKey", uuid.uuid4().hex)
    # Only pass through the fields the contract allows; anything price-shaped
    # from the client is dropped on the floor.
    forward = {
        "businessSlug": body.get("businessSlug"),
        "tenantId": body.get("tenantId"),
        "items": body.get("items"),
        "startDate": body.get("startDate"),
        "endDate": body.get("endDate"),
        "customer": body.get("customer"),
        "siteUrl": body.get("siteUrl"),
        "checkoutKey": body["checkoutKey"],
    }
    res = httpx.post(f"{WORKERS}/checkout", json=forward, timeout=120)
    return JSONResponse(status_code=res.status_code, content=res.json())


@app.post("/webhooks/stripe")
async def post_stripe_webhook(request: Request) -> Any:
    """Stripe webhook intake: forward the RAW body and signature unchanged
    down the chain; signature verification happens in Tier 3."""
    raw = await request.body()
    res = httpx.post(
        f"{WORKERS}/webhooks/stripe",
        content=raw,
        headers={
            "stripe-signature": request.headers.get("stripe-signature", ""),
            "content-type": "application/json",
        },
        timeout=60,
    )
    return JSONResponse(status_code=res.status_code, content=res.json())


@app.get("/api/reservations/{reservation_id}")
def get_reservation(reservation_id: str) -> Any:
    """Customer-facing status: answered from the DB, not the redirect."""
    res = httpx.get(f"{WORKERS}/reservations/{reservation_id}", timeout=30)
    return JSONResponse(status_code=res.status_code, content=res.json())


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
