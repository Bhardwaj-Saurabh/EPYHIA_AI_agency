"""The Action Gate pipeline (DESIGN.md section 4):

    capability check -> approval check -> run budget -> idempotency
    -> audit + cost log -> execute

Every external effect in the system goes through request_action/approve_action.
"""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .capabilities import CAPABILITIES, REQUIRES_ADMIN_APPROVAL
from .db import pool
from .hashing import payload_hash

log = logging.getLogger("gate.pipeline")

Executor = Callable[[Any, "ExecutorContext"], "ExecutorResult"]


class GateError(Exception):
    def __init__(self, http_status: int, message: str) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.message = message


@dataclass
class ExecutorContext:
    tenant_id: str
    action_id: str
    mode: str


@dataclass
class ExecutorResult:
    provider_reference: str
    provider_cost_microdollars: int = 0


@dataclass
class PipelineConfig:
    executors: Mapping[str, Executor]
    capabilities: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: CAPABILITIES)
    requires_approval: frozenset[str] = field(default_factory=lambda: REQUIRES_ADMIN_APPROVAL)


def _mode() -> str:
    import os

    return os.environ.get("RUN_MODE", "TEST")


def _run_spend_microdollars(cur: Any, run_id: str) -> int:
    cur.execute(
        """SELECT COALESCE((SELECT SUM(cost_microdollars) FROM agent_calls WHERE run_id = %s), 0)
                + COALESCE((SELECT SUM(provider_cost_microdollars) FROM actions WHERE run_id = %s), 0)
                AS spent""",
        (run_id, run_id),
    )
    return int(cur.fetchone()["spent"])


def _execute(row: dict[str, Any], payload: Any, config: PipelineConfig) -> dict[str, Any]:
    executor = config.executors.get(row["action_type"])
    if executor is None:
        raise GateError(501, f"no executor implemented for action type '{row['action_type']}'")
    try:
        result = executor(
            payload,
            ExecutorContext(
                tenant_id=str(row["tenant_id"]), action_id=str(row["id"]), mode=row["mode"]
            ),
        )
    except GateError:
        _mark_failed(row)
        raise
    except Exception as err:
        _mark_failed(row)
        log.exception("action %s (%s) failed", row["id"], row["action_type"])
        raise GateError(502, f"action execution failed: {err}") from err

    with pool.connection() as conn:
        updated = conn.execute(
            """UPDATE actions
                  SET status = 'executed', provider_reference = %s,
                      provider_cost_microdollars = %s, executed_at = now()
                WHERE id = %s RETURNING *""",
            (result.provider_reference, result.provider_cost_microdollars, row["id"]),
        ).fetchone()
    return updated


def _mark_failed(row: dict[str, Any]) -> None:
    with pool.connection() as conn:
        conn.execute(
            "UPDATE actions SET status = 'failed', executed_at = now() WHERE id = %s",
            (row["id"],),
        )


def request_action(
    *,
    tenant_id: str,
    agent_name: str,
    action_type: str,
    payload: Any,
    idempotency_key: str,
    run_id: str | None = None,
    config: PipelineConfig,
) -> tuple[dict[str, Any], bool]:
    """Returns (audit_row, replayed). When approval is required the row comes
    back status=pending_approval and nothing executes. Replaying the same
    (tenant, action_type, idempotency_key) never executes twice."""

    # 1. Capability: may this agent request this action at all?
    allowed = config.capabilities.get(agent_name)
    if not allowed or action_type not in allowed:
        raise GateError(403, f"agent '{agent_name}' has no capability '{action_type}'")

    # 2. Mode: TEST by default; LIVE is rejected until explicitly supported.
    mode = _mode()
    if mode != "TEST":
        raise GateError(400, f"unsupported mode '{mode}' - the gate runs TEST-mode only")

    digest = payload_hash(payload)
    needs_approval = action_type in config.requires_approval
    replay = False

    with pool.connection() as conn, conn.cursor() as cur:
        # 3. Idempotency: one row per (tenant, action_type, key), locked.
        cur.execute(
            """SELECT * FROM actions
                WHERE tenant_id = %s AND action_type = %s AND idempotency_key = %s
                FOR UPDATE""",
            (tenant_id, action_type, idempotency_key),
        )
        row = cur.fetchone()

        if row is not None:
            if row["payload_hash"] != digest:
                raise GateError(
                    409,
                    f"idempotency key '{idempotency_key}' was already used with a different payload",
                )
            # executed -> replay result; pending_approval -> still waiting;
            # both short-circuit. approved/failed fall through to (re-)execution.
            if row["status"] in ("executed", "pending_approval"):
                return row, True
            replay = True
        else:
            # 4. Budget: when the action belongs to a run, enforce the cap.
            if run_id:
                cur.execute(
                    "SELECT approved_budget_microdollars FROM runs WHERE id = %s", (run_id,)
                )
                budget_row = cur.fetchone()
                if budget_row is None:
                    raise GateError(404, f"run {run_id} not found")
                spent = _run_spend_microdollars(cur, run_id)
                if spent >= int(budget_row["approved_budget_microdollars"]):
                    raise GateError(402, f"run {run_id} has exhausted its approved budget")

            # 5. Audit row - created before execution, so a crash leaves a trace.
            cur.execute(
                """INSERT INTO actions
                     (tenant_id, run_id, agent_name, action_type, payload_hash,
                      idempotency_key, mode, approval_status, status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING *""",
                (
                    tenant_id,
                    run_id,
                    agent_name,
                    action_type,
                    digest,
                    idempotency_key,
                    mode,
                    "PENDING" if needs_approval else "NOT_REQUIRED",
                    "pending_approval" if needs_approval else "approved",
                ),
            )
            row = cur.fetchone()

    # 6. Execute immediately only when no human approval is needed.
    if row["status"] == "pending_approval":
        return row, False
    return _execute(row, payload, config), replay


def approve_action(
    action_id: str,
    approved_by: str,
    approved_payload_hash: str,
    payload: Any,
    config: PipelineConfig,
) -> dict[str, Any]:
    """Approve a pending action. The approval is bound to the payload hash the
    approver saw - a mismatch (superseded payload) is rejected outright."""
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM actions WHERE id = %s FOR UPDATE", (action_id,))
        row = cur.fetchone()
        if row is None:
            raise GateError(404, f"action {action_id} not found")
        if row["status"] != "pending_approval":
            raise GateError(409, f"action {action_id} is '{row['status']}', not pending_approval")
        if row["payload_hash"] != approved_payload_hash:
            raise GateError(
                409,
                "approval hash does not match the action's payload - "
                "the payload was superseded; re-review it",
            )
        if payload_hash(payload) != row["payload_hash"]:
            raise GateError(409, "supplied payload does not match the approved hash")

        cur.execute(
            """UPDATE actions
                  SET approval_status = 'APPROVED', approved_by = %s, approved_at = now(),
                      status = 'approved'
                WHERE id = %s RETURNING *""",
            (approved_by, action_id),
        )
        row = cur.fetchone()

    return _execute(row, payload, config)


def get_action(action_id: str) -> dict[str, Any] | None:
    with pool.connection() as conn:
        return conn.execute("SELECT * FROM actions WHERE id = %s", (action_id,)).fetchone()


def pending_approvals() -> list[dict[str, Any]]:
    with pool.connection() as conn:
        return conn.execute(
            "SELECT * FROM actions WHERE status = 'pending_approval' ORDER BY created_at"
        ).fetchall()
