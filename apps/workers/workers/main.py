"""Tier 2 - Orchestration Runtime + agent workers. Holds no credentials;
reaches providers and storage only via the Action Gate."""

import logging
import os
import threading
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .gate_client import GateClientError, approve_brand, get_run, request_action
from .marketer import build_marketing_pack
from .ops_agent import setup_catalog
from .strategist import run_strategist
from .web_builder import build_website

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("workers")
app = FastAPI(title="epyhia-workers")


@app.exception_handler(GateClientError)
def gate_client_error_handler(_request: Request, err: GateClientError) -> JSONResponse:
    return JSONResponse(status_code=err.status, content={"error": err.message})


@app.post("/runs")
def post_runs(body: dict[str, Any]) -> JSONResponse:
    """Flow 1 step 2: deterministic run-shell creation, then the Strategist
    runs in the background. Poll GET /runs/:id for progress."""
    tenant_id = body.get("tenantId")
    brief = body.get("brief")
    budget = body.get("budgetMicrodollars")
    approved_by = body.get("approvedBy")
    idempotency_key = body.get("idempotencyKey")
    if not all([tenant_id, brief, budget, approved_by, idempotency_key]):
        return JSONResponse(
            status_code=400,
            content={
                "error": "tenantId, brief, budgetMicrodollars, approvedBy, idempotencyKey required"
            },
        )

    shell = request_action(
        tenant_id=tenant_id,
        agent_name="system",
        action_type="run_shell",
        payload={
            "brief": brief,
            "budgetMicrodollars": budget,
            "approvedBy": approved_by,
            "onboardingKey": idempotency_key,
        },
        idempotency_key=idempotency_key,
    )
    run_id = shell["action"].get("provider_reference")
    if not run_id:
        return JSONResponse(status_code=500, content={"error": "run_shell returned no run id"})

    replayed = bool(shell.get("replayed"))
    if not replayed:
        threading.Thread(
            target=_strategist_bg, args=(tenant_id, run_id, brief), daemon=True
        ).start()
    return JSONResponse(
        status_code=200 if replayed else 202, content={"runId": run_id, "replayed": replayed}
    )


def _strategist_bg(tenant_id: str, run_id: str, brief: str) -> None:
    try:
        run_strategist(tenant_id, run_id, brief)
    except Exception:
        log.exception("strategist failed for run %s", run_id)


@app.post("/runs/{run_id}/approve-brand")
def post_approve_brand(run_id: str, body: dict[str, Any]) -> Any:
    """Flow 1 step 6: the administrator approves the brand document (bound to
    its content hash); the Runtime then starts the downstream generators."""
    approved_by = body.get("approvedBy")
    content_hash = body.get("contentHash")
    if not approved_by or not content_hash:
        return JSONResponse(status_code=400, content={"error": "approvedBy and contentHash required"})

    state = get_run(run_id)
    brand = state.get("brandDocument")
    if not brand:
        return JSONResponse(status_code=409, content={"error": "run has no brand document yet"})
    tenant_id = state["run"]["tenant_id"]

    result = approve_brand(brand["id"], approved_by, content_hash)

    threading.Thread(target=_downstream_bg, args=(tenant_id, run_id), daemon=True).start()
    return {"approved": True, "unblockedRuns": result["unblockedRuns"]}


def _downstream_bg(tenant_id: str, run_id: str) -> None:
    """Runs the post-approval pipeline: catalog first (the site and its
    grounding checks depend on it), then the Web Builder."""
    try:
        state = get_run(run_id)
        tasks = {t["task_type"]: t["status"] for t in state["tasks"]}
        if tasks.get("CATALOG_SETUP") == "PENDING":
            setup_catalog(tenant_id, run_id)
        if tasks.get("WEBSITE") in ("PENDING", "IN_PROGRESS", "FAILED"):
            outcome = build_website(tenant_id, run_id)
            log.info("web builder done for run %s: %s", run_id, outcome)
        state = get_run(run_id)
        tasks = {t["task_type"]: t["status"] for t in state["tasks"]}
        if tasks.get("MARKETING_PACK") in ("PENDING", "IN_PROGRESS", "FAILED"):
            outcome = build_marketing_pack(tenant_id, run_id)
            log.info("marketer done for run %s: %s", run_id, outcome)
    except Exception:
        log.exception("downstream pipeline failed for run %s", run_id)


@app.post("/runs/{run_id}/clarify")
def post_clarify(run_id: str, body: dict[str, Any]) -> Any:
    """Flow 1 step 3: the administrator answers the Strategist's questions."""
    tenant_id = body.get("tenantId")
    answers = body.get("answers")
    if not tenant_id or not answers:
        return JSONResponse(status_code=400, content={"error": "tenantId and answers required"})
    state = get_run(run_id)
    if state["run"]["status"] != "AWAITING_CLARIFICATION":
        return JSONResponse(
            status_code=409,
            content={"error": f"run is '{state['run']['status']}', not AWAITING_CLARIFICATION"},
        )
    outcome = run_strategist(tenant_id, run_id, state["run"]["original_brief"], str(answers))
    return {"runId": run_id, "outcome": outcome}


@app.get("/runs/{run_id}")
def get_run_route(run_id: str) -> Any:
    return get_run(run_id)


@app.post("/runs/{run_id}/rebuild-website")
def post_rebuild_website(run_id: str, body: dict[str, Any]) -> Any:
    """Regenerate + redeploy the site (e.g. after a template upgrade). The new
    version goes through the same review loop and a fresh hash-bound deploy
    approval - redeploys overwrite, never duplicate (DESIGN.md sec. 10)."""
    tenant_id = body.get("tenantId")
    if not tenant_id:
        return JSONResponse(status_code=400, content={"error": "tenantId required"})

    def _rebuild() -> None:
        try:
            outcome = build_website(tenant_id, run_id)
            log.info("website rebuild for run %s: %s", run_id, outcome)
        except Exception:
            log.exception("website rebuild failed for run %s", run_id)

    threading.Thread(target=_rebuild, daemon=True).start()
    return JSONResponse(status_code=202, content={"rebuilding": run_id})


# ---- Customer path: deterministic passthrough under Ops' capability scope.
# No model call sits anywhere in the payment path (DESIGN.md sec. 2).

GATE = os.environ.get("GATE_URL", "http://localhost:8082").rstrip("/")


@app.post("/checkout")
def post_checkout(body: dict[str, Any]) -> Any:
    tenant_id = body.get("tenantId")
    checkout_key = body.get("checkoutKey")
    if not tenant_id and body.get("businessSlug"):
        # Tenant identity comes from the site/host mapping, never the browser's
        # say-so on ids (Flow 2 step 1). Slug -> tenant via the gate's read API.
        import httpx

        res = httpx.get(f"{GATE}/tenants/by-slug/{body['businessSlug']}", timeout=30)
        if res.status_code != 200:
            return JSONResponse(status_code=404, content={"error": "unknown business"})
        tenant_id = res.json()["tenant"]["id"]
    if not tenant_id or not checkout_key:
        return JSONResponse(status_code=400, content={"error": "tenantId and checkoutKey required"})
    result = request_action(
        tenant_id=tenant_id,
        agent_name="ops",
        action_type="checkout_session",
        payload={
            "items": body.get("items"),
            "startDate": body.get("startDate"),
            "endDate": body.get("endDate"),
            "customer": body.get("customer"),
            "siteUrl": body.get("siteUrl"),
        },
        idempotency_key=f"checkout-{checkout_key}",
    )
    ref = result["action"].get("provider_reference") or ""
    session_id, _, rest = ref.partition("|")
    reservation_id, _, rest = rest.partition("|")
    total, _, url = rest.partition("|")
    return {
        "reservationId": reservation_id,
        "totalPence": int(total) if total else None,
        "checkoutUrl": url,
        "replayed": result.get("replayed", False),
    }


@app.post("/webhooks/stripe")
async def post_stripe_webhook(request: Request) -> Any:
    """Forward the RAW body and signature unchanged to the gate - verification
    happens there (DESIGN.md sec. 2)."""
    import httpx

    raw = await request.body()
    res = httpx.post(
        f"{GATE}/webhooks/stripe",
        content=raw,
        headers={
            "stripe-signature": request.headers.get("stripe-signature", ""),
            "content-type": "application/json",
        },
        timeout=60,
    )
    return JSONResponse(status_code=res.status_code, content=res.json())


@app.get("/reservations/{reservation_id}")
def get_reservation(reservation_id: str) -> Any:
    import httpx

    res = httpx.get(f"{GATE}/reservations/{reservation_id}", timeout=30)
    return JSONResponse(status_code=res.status_code, content=res.json())


@app.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "ok", "app": "workers"}


@app.get("/health/ready")
def health_ready() -> dict[str, str]:
    gate = "configured" if os.environ.get("GATE_URL") else "not configured"
    return {"status": "ok", "app": "workers", "gate": gate}


def serve() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("WORKERS_PORT", "8081")))


if __name__ == "__main__":
    serve()
