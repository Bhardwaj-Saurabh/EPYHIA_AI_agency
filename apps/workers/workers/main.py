"""Tier 2 - Orchestration Runtime + agent workers. Holds no credentials;
reaches providers and storage only via the Action Gate."""

import logging
import os
import threading
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .gate_client import GateClientError, get_run, request_action
from .strategist import run_strategist

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
