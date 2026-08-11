"""Tier 3 - Action Gate. Sole credential holder; no public ingress in prod."""

import logging
import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .db import pool
from .executors import EXECUTORS
from .modelcall import handle_model_call
from .pipeline import (
    GateError,
    PipelineConfig,
    approve_action,
    get_action,
    pending_approvals,
    request_action,
)

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="epyhia-gate")
CONFIG = PipelineConfig(executors=EXECUTORS)


@app.exception_handler(GateError)
def gate_error_handler(_request: Request, err: GateError) -> JSONResponse:
    return JSONResponse(status_code=err.http_status, content={"error": err.message})


@app.post("/actions")
def post_actions(body: dict[str, Any]) -> JSONResponse:
    tenant_id = body.get("tenantId")
    agent_name = body.get("agentName")
    action_type = body.get("actionType")
    idempotency_key = body.get("idempotencyKey")
    if not tenant_id or not agent_name or not action_type or not idempotency_key:
        raise GateError(400, "tenantId, agentName, actionType, idempotencyKey required")

    row, replayed = request_action(
        tenant_id=tenant_id,
        run_id=body.get("runId"),
        agent_name=agent_name,
        action_type=action_type,
        payload=body.get("payload"),
        idempotency_key=idempotency_key,
        config=CONFIG,
    )
    status = 202 if row["status"] == "pending_approval" else 200
    return JSONResponse(status_code=status, content=_json({"action": row, "replayed": replayed}))


@app.get("/actions/{action_id}")
def get_action_route(action_id: str) -> dict[str, Any]:
    row = get_action(action_id)
    if row is None:
        raise GateError(404, "not found")
    return _json({"action": row})


@app.get("/approvals")
def get_approvals() -> dict[str, Any]:
    return _json({"pending": pending_approvals()})


@app.post("/approvals/{action_id}/approve")
def post_approve(action_id: str, body: dict[str, Any]) -> dict[str, Any]:
    approved_by = body.get("approvedBy")
    approved_hash = body.get("payloadHash")
    if not approved_by or not approved_hash:
        raise GateError(400, "approvedBy and payloadHash required")
    row = approve_action(action_id, approved_by, approved_hash, body.get("payload"), CONFIG)
    return _json({"action": row})


@app.post("/model_call")
def post_model_call(body: dict[str, Any]) -> dict[str, Any]:
    return handle_model_call(body)


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    """Read API for Tier 2/Tier 1 (they hold no DB credentials)."""
    with pool.connection() as conn:
        run = conn.execute(
            """SELECT r.*,
                      (COALESCE((SELECT SUM(cost_microdollars) FROM agent_calls WHERE run_id = r.id), 0)
                     + COALESCE((SELECT SUM(provider_cost_microdollars) FROM actions WHERE run_id = r.id), 0)
                      )::bigint AS spent_microdollars
                 FROM runs r WHERE r.id = %s""",
            (run_id,),
        ).fetchone()
        if run is None:
            raise GateError(404, "run not found")
        tasks = conn.execute(
            """SELECT id, task_type, status, output_ref, updated_at
                 FROM tasks WHERE run_id = %s ORDER BY task_type""",
            (run_id,),
        ).fetchall()
        brand = None
        if run["brand_document_id"]:
            brand = conn.execute(
                "SELECT id, version_number, full_text FROM brand_document WHERE id = %s",
                (run["brand_document_id"],),
            ).fetchone()
    return _json({"run": run, "tasks": tasks, "brandDocument": brand})


@app.get("/tenants/by-slug/{slug}")
def get_tenant_by_slug(slug: str) -> dict[str, Any]:
    with pool.connection() as conn:
        tenant = conn.execute(
            "SELECT id, name, business_name, business_slug FROM tenants WHERE business_slug = %s",
            (slug,),
        ).fetchone()
    if tenant is None:
        raise GateError(404, "tenant not found")
    return _json({"tenant": tenant})


@app.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "ok", "app": "gate"}


@app.get("/health/ready")
def health_ready() -> Any:
    try:
        with pool.connection() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok", "app": "gate", "db": "connected"}
    except Exception:
        return JSONResponse(
            status_code=503, content={"status": "degraded", "app": "gate", "db": "unreachable"}
        )


def _json(obj: Any) -> Any:
    """Make DB rows JSON-safe (UUID, datetime, Decimal)."""
    from fastapi.encoders import jsonable_encoder

    return jsonable_encoder(obj)


def serve() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("GATE_PORT", "8082")))


if __name__ == "__main__":
    serve()
