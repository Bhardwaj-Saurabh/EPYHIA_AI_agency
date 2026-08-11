"""Storage executors. Tier 2 holds no DB credentials, so all persistence is a
gated action. run_shell is deterministic control-plane work (Flow 1 step 2);
business_storage carries Ops' persistence ops (Flow 1 step 5)."""

import hashlib
import json
from typing import Any

from ..db import pool
from ..pipeline import ExecutorContext, ExecutorResult

TASK_TYPES = frozenset({"CATALOG_SETUP", "WEBSITE", "MARKETING_PACK", "CHECKOUT"})


def run_shell_executor(payload: Any, ctx: ExecutorContext) -> ExecutorResult:
    """Creates the onboarding request + run shell in one transaction,
    idempotent on (tenant_id, onboardingKey). Returns the existing run on replay."""
    p = payload if isinstance(payload, dict) else {}
    brief = p.get("brief")
    budget = p.get("budgetMicrodollars")
    approved_by = p.get("approvedBy")
    onboarding_key = p.get("onboardingKey")
    if not brief or not budget or not approved_by or not onboarding_key:
        raise ValueError("run_shell needs brief, budgetMicrodollars, approvedBy, onboardingKey")

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT run_id FROM onboarding_requests
                WHERE tenant_id = %s AND idempotency_key = %s FOR UPDATE""",
            (ctx.tenant_id, onboarding_key),
        )
        existing = cur.fetchone()
        if existing is not None:
            return ExecutorResult(provider_reference=str(existing["run_id"]))

        brief_hash = hashlib.sha256(brief.encode("utf-8")).hexdigest()
        cur.execute(
            """INSERT INTO runs
                 (tenant_id, original_brief, brief_hash, approved_budget_microdollars,
                  budget_approved_by, status)
               VALUES (%s, %s, %s, %s, %s, 'CREATED') RETURNING id""",
            (ctx.tenant_id, brief, brief_hash, int(budget), approved_by),
        )
        run_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO onboarding_requests (tenant_id, idempotency_key, run_id) "
            "VALUES (%s, %s, %s)",
            (ctx.tenant_id, onboarding_key, run_id),
        )
        return ExecutorResult(provider_reference=str(run_id))


def business_storage_executor(payload: Any, ctx: ExecutorContext) -> ExecutorResult:
    """Ops' persistence operations, each a single transaction."""
    p = payload if isinstance(payload, dict) else {}
    op = p.get("op")
    if op == "finalize_run":
        return _finalize_run(p, ctx.tenant_id)
    if op == "record_questions":
        return _record_questions(p, ctx.tenant_id)
    raise ValueError(f"unknown business_storage op '{op}'")


def _finalize_run(p: dict[str, Any], tenant_id: str) -> ExecutorResult:
    run_id = p.get("runId")
    completed_brief = p.get("completedBrief")
    brand_document = p.get("brandDocument")
    task_types = p.get("taskTypes")
    if not run_id or not completed_brief or not brand_document or not isinstance(task_types, list):
        raise ValueError("finalize_run needs runId, completedBrief, brandDocument, taskTypes")
    bad = [t for t in task_types if t not in TASK_TYPES]
    if bad:
        raise ValueError(f"unknown task types: {', '.join(bad)}")

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT COALESCE(MAX(version_number), 0) + 1 AS next
                 FROM brand_document WHERE tenant_id = %s""",
            (tenant_id,),
        )
        version = cur.fetchone()["next"]
        cur.execute(
            """INSERT INTO brand_document (tenant_id, version_number, full_text)
               VALUES (%s, %s, %s) RETURNING id""",
            (tenant_id, version, brand_document),
        )
        doc_id = cur.fetchone()["id"]
        cur.execute(
            """UPDATE runs
                  SET completed_brief = %s, brand_document_id = %s,
                      status = 'AWAITING_BRAND_APPROVAL'
                WHERE id = %s AND tenant_id = %s""",
            (completed_brief, doc_id, run_id, tenant_id),
        )
        if cur.rowcount == 0:
            raise ValueError(f"run {run_id} not found for tenant")

        for task_type in task_types:
            cur.execute(
                """INSERT INTO tasks (tenant_id, run_id, task_type, status)
                   VALUES (%s, %s, %s, 'BLOCKED_ON_BRAND_APPROVAL')
                   ON CONFLICT (run_id, task_type)
                     DO UPDATE SET status = 'BLOCKED_ON_BRAND_APPROVAL', updated_at = now()""",
                (tenant_id, run_id, task_type),
            )
        # A clarification round, if any, is now resolved.
        cur.execute(
            """UPDATE tasks SET status = 'DONE', updated_at = now()
                WHERE run_id = %s AND task_type = 'CLARIFICATION'""",
            (run_id,),
        )
        return ExecutorResult(provider_reference=str(doc_id))


def _record_questions(p: dict[str, Any], tenant_id: str) -> ExecutorResult:
    run_id = p.get("runId")
    questions = p.get("questions")
    if not run_id or not isinstance(questions, list) or not questions:
        raise ValueError("record_questions needs runId and a non-empty questions list")

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO tasks (tenant_id, run_id, task_type, status, output_ref)
               VALUES (%s, %s, 'CLARIFICATION', 'AWAITING_ADMIN', %s)
               ON CONFLICT (run_id, task_type)
                 DO UPDATE SET status = 'AWAITING_ADMIN', output_ref = EXCLUDED.output_ref,
                               updated_at = now()""",
            (tenant_id, run_id, json.dumps({"questions": questions})),
        )
        cur.execute(
            "UPDATE runs SET status = 'AWAITING_CLARIFICATION' WHERE id = %s AND tenant_id = %s",
            (run_id, tenant_id),
        )
        if cur.rowcount == 0:
            raise ValueError(f"run {run_id} not found for tenant")
        return ExecutorResult(provider_reference=f"clarification:{run_id}")
