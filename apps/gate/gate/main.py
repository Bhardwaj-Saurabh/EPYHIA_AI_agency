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
                """SELECT id, version_number, full_text, content_hash, approved_by, approved_at
                     FROM brand_document WHERE id = %s""",
                (run["brand_document_id"],),
            ).fetchone()
        tenant = conn.execute(
            """SELECT id, business_name, business_slug, business_email, business_phone,
                      business_address
                 FROM tenants WHERE id = %s""",
            (run["tenant_id"],),
        ).fetchone()
        catalog = conn.execute(
            """SELECT id, name, description, available_qty, day_rate
                 FROM rental_items WHERE tenant_id = %s ORDER BY name""",
            (run["tenant_id"],),
        ).fetchall()
        deployment = conn.execute(
            """SELECT cloudflare_project_name, live_url, verified_at
                 FROM deployments WHERE tenant_id = %s""",
            (run["tenant_id"],),
        ).fetchone()
    return _json(
        {
            "run": run,
            "tasks": tasks,
            "brandDocument": brand,
            "tenant": tenant,
            "catalog": catalog,
            "deployment": deployment,
        }
    )


@app.post("/brand/{doc_id}/approve")
def post_brand_approve(doc_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Flow 1 step 6: administrator approval bound to the brand-document id and
    content hash. Unblocks the run's downstream tasks."""
    approved_by = body.get("approvedBy")
    content_hash = body.get("contentHash")
    if not approved_by or not content_hash:
        raise GateError(400, "approvedBy and contentHash required")

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM brand_document WHERE id = %s FOR UPDATE", (doc_id,))
        doc = cur.fetchone()
        if doc is None:
            raise GateError(404, "brand document not found")
        if doc["content_hash"] != content_hash:
            raise GateError(
                409,
                "approval hash does not match this brand document version - re-review it",
            )
        if doc["approved_by"] is None:
            cur.execute(
                "UPDATE brand_document SET approved_by = %s, approved_at = now() WHERE id = %s",
                (approved_by, doc_id),
            )
        cur.execute(
            """UPDATE runs SET status = 'BRAND_APPROVED'
                WHERE brand_document_id = %s AND status = 'AWAITING_BRAND_APPROVAL'
                RETURNING id""",
            (doc_id,),
        )
        run_ids = [str(r["id"]) for r in cur.fetchall()]
        for run_id in run_ids:
            cur.execute(
                """UPDATE tasks SET status = 'PENDING', updated_at = now()
                    WHERE run_id = %s AND status = 'BLOCKED_ON_BRAND_APPROVAL'""",
                (run_id,),
            )
    return _json({"brandDocumentId": doc_id, "unblockedRuns": run_ids})


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


def _pack_rows(run_id: str) -> list[dict[str, Any]]:
    with pool.connection() as conn:
        return conn.execute(
            """SELECT id, artifact_type, sequence_number, channel, text_content,
                      self_review_status, grounding_check_status, review_feedback,
                      approval_status, approved_by, approved_at, brand_document_id
                 FROM marketing_artifacts
                WHERE run_id = %s AND text_content IS NOT NULL
                ORDER BY artifact_type, sequence_number""",
            (run_id,),
        ).fetchall()


def _pack_hash(rows: list[dict[str, Any]]) -> str:
    from .hashing import payload_hash

    return payload_hash(
        [
            {
                "type": r["artifact_type"],
                "seq": r["sequence_number"],
                "channel": r["channel"],
                "text": r["text_content"],
            }
            for r in rows
        ]
    )


@app.get("/marketing-pack/{run_id}")
def get_marketing_pack(run_id: str) -> dict[str, Any]:
    """The pack the administrator reviews, with the authoritative hash their
    approval binds to (DESIGN.md sec. 5.8)."""
    rows = _pack_rows(run_id)
    if not rows:
        raise GateError(404, "no marketing artifacts for this run")
    eligible = all(
        r["self_review_status"] == "PASSED" and r["grounding_check_status"] == "PASSED"
        for r in rows
    )
    return _json({"artifacts": rows, "packHash": _pack_hash(rows), "approvalEligible": eligible})


@app.post("/marketing-pack/{run_id}/approve")
def post_marketing_pack_approve(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Approval bound to the complete marketing-pack hash. Only packs whose
    artifacts all passed self-review AND grounding are approval-eligible."""
    approved_by = body.get("approvedBy")
    pack_hash = body.get("packHash")
    if not approved_by or not pack_hash:
        raise GateError(400, "approvedBy and packHash required")

    rows = _pack_rows(run_id)
    if not rows:
        raise GateError(404, "no marketing artifacts for this run")
    if any(
        r["self_review_status"] != "PASSED" or r["grounding_check_status"] != "PASSED"
        for r in rows
    ):
        raise GateError(409, "pack is not approval-eligible: self-review or grounding not passed")
    if _pack_hash(rows) != pack_hash:
        raise GateError(
            409, "approval hash does not match the current pack - it was superseded; re-review it"
        )

    with pool.connection() as conn:
        conn.execute(
            """UPDATE marketing_artifacts
                  SET approval_status = 'APPROVED', approved_by = %s, approved_at = now(),
                      updated_at = now()
                WHERE run_id = %s AND text_content IS NOT NULL""",
            (approved_by, run_id),
        )
        # Honest task state: the pack is approved, but the marketing deliverable
        # is only COMPLETE once the videos render (a separate paid approval).
        conn.execute(
            """UPDATE tasks SET status = 'AWAITING_VIDEO_RENDER', updated_at = now()
                WHERE run_id = %s AND task_type = 'MARKETING_PACK'""",
            (run_id,),
        )
    return _json({"approved": True, "artifacts": len(rows), "packHash": pack_hash})


@app.post("/webhooks/stripe")
async def post_stripe_webhook(request: Request) -> dict[str, Any]:
    """Raw body + signature arrive unchanged through Tier 1/Tier 2;
    verification happens HERE, before any processing (DESIGN.md sec. 2)."""
    from .executors.checkout import process_stripe_webhook

    raw = await request.body()
    signature = request.headers.get("stripe-signature", "")
    return process_stripe_webhook(raw, signature)


@app.get("/reservations/{reservation_id}")
def get_reservation(reservation_id: str) -> dict[str, Any]:
    """Flow 2 step 6: transaction status comes from the DB, never the redirect."""
    with pool.connection() as conn:
        resv = conn.execute(
            """SELECT id, status, total, start_date, end_date, stripe_checkout_session_id
                 FROM reservations WHERE id = %s""",
            (reservation_id,),
        ).fetchone()
        if resv is None:
            raise GateError(404, "reservation not found")
        order = conn.execute(
            """SELECT id, amount, currency, status, payment_timestamp, is_synthetic
                 FROM orders WHERE reservation_id = %s""",
            (reservation_id,),
        ).fetchone()
    return _json({"reservation": resv, "order": order})


@app.get("/tenants/{tenant_id}/orders")
def get_tenant_orders(tenant_id: str) -> dict[str, Any]:
    """Order evidence for the dashboard/eval: real and synthetic, separated."""
    with pool.connection() as conn:
        orders = conn.execute(
            """SELECT id, reservation_id, amount, currency, status, payment_timestamp,
                      is_synthetic, created_at
                 FROM orders WHERE tenant_id = %s ORDER BY created_at""",
            (tenant_id,),
        ).fetchall()
    real = [o for o in orders if not o["is_synthetic"]]
    synthetic = [o for o in orders if o["is_synthetic"]]
    return _json({"real": real, "synthetic": synthetic})


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
