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
    if op == "set_catalog":
        return _set_catalog(p, ctx.tenant_id)
    raise ValueError(f"unknown business_storage op '{op}'")


def task_storage_executor(payload: Any, ctx: ExecutorContext) -> ExecutorResult:
    """Runtime task bookkeeping (control-plane, agent 'system')."""
    p = payload if isinstance(payload, dict) else {}
    run_id = p.get("runId")
    task_type = p.get("taskType")
    status = p.get("status")
    if not run_id or not task_type or not status:
        raise ValueError("task_storage needs runId, taskType, status")
    with pool.connection() as conn, conn.cursor() as cur:
        if p.get("outputRef") is not None:
            cur.execute(
                """UPDATE tasks SET status = %s, output_ref = %s, updated_at = now()
                    WHERE run_id = %s AND task_type = %s AND tenant_id = %s""",
                (status, p["outputRef"], run_id, task_type, ctx.tenant_id),
            )
        else:
            cur.execute(
                """UPDATE tasks SET status = %s, updated_at = now()
                    WHERE run_id = %s AND task_type = %s AND tenant_id = %s""",
                (status, run_id, task_type, ctx.tenant_id),
            )
        if cur.rowcount == 0:
            raise ValueError(f"task ({run_id}, {task_type}) not found for tenant")
    return ExecutorResult(provider_reference=f"task:{task_type}:{status}")


def site_storage_executor(payload: Any, ctx: ExecutorContext) -> ExecutorResult:
    """Web Builder's site storage: persists generated site files onto the
    WEBSITE task so the admin can review the exact payload they approve."""
    p = payload if isinstance(payload, dict) else {}
    run_id = p.get("runId")
    files = p.get("files")
    if not run_id or not isinstance(files, dict) or not files:
        raise ValueError("site_storage needs runId and a non-empty files map")
    output = json.dumps(
        {"files": files, "reviewRounds": p.get("reviewRounds"), "projectName": p.get("projectName")}
    )
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE tasks SET output_ref = %s, updated_at = now()
                WHERE run_id = %s AND task_type = 'WEBSITE' AND tenant_id = %s""",
            (output, run_id, ctx.tenant_id),
        )
        if cur.rowcount == 0:
            raise ValueError(f"WEBSITE task for run {run_id} not found")
    return ExecutorResult(provider_reference=f"site:{run_id}")


def _set_catalog(p: dict[str, Any], tenant_id: str) -> ExecutorResult:
    """Populates rental_items from the completed brief (CATALOG_SETUP task) and
    records the business contact details on the tenant. Idempotent: replaces
    the tenant's catalog wholesale (safe pre-launch; reservations reference
    items only after checkout exists)."""
    run_id = p.get("runId")
    items = p.get("items")
    if not run_id or not isinstance(items, list) or not items:
        raise ValueError("set_catalog needs runId and a non-empty items list")
    for it in items:
        if not it.get("name") or not isinstance(it.get("dayRateCents"), int) or not isinstance(
            it.get("availableQty"), int
        ):
            raise ValueError("each item needs name, dayRateCents (int), availableQty (int)")

    contact = p.get("businessContact") or {}
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM reservation_items ri JOIN rental_items r ON ri.rental_item_id = r.id WHERE r.tenant_id = %s",
            (tenant_id,),
        )
        if cur.fetchone()["n"] > 0:
            raise ValueError("catalog already has reservations - refusing wholesale replace")
        cur.execute("DELETE FROM rental_items WHERE tenant_id = %s", (tenant_id,))
        for it in items:
            cur.execute(
                """INSERT INTO rental_items (tenant_id, name, description, available_qty, day_rate)
                   VALUES (%s, %s, %s, %s, %s)""",
                (tenant_id, it["name"], it.get("description"), it["availableQty"], it["dayRateCents"]),
            )
        if contact:
            cur.execute(
                """UPDATE tenants
                      SET business_email = COALESCE(%s, business_email),
                          business_phone = COALESCE(%s, business_phone),
                          business_address = COALESCE(%s, business_address)
                    WHERE id = %s""",
                (contact.get("email"), contact.get("phone"), contact.get("address"), tenant_id),
            )
        cur.execute(
            """UPDATE tasks SET status = 'DONE', updated_at = now()
                WHERE run_id = %s AND task_type = 'CATALOG_SETUP'""",
            (run_id,),
        )
    return ExecutorResult(provider_reference=f"catalog:{len(items)} items")


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
            """INSERT INTO brand_document (tenant_id, version_number, full_text, content_hash)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            (
                tenant_id,
                version,
                brand_document,
                hashlib.sha256(brand_document.encode("utf-8")).hexdigest(),
            ),
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


TEXT_ARTIFACT_TYPES = frozenset({"LANDING_COPY", "SOCIAL_POST", "LAUNCH_EMAIL", "VIDEO_STORYBOARD"})


def artifact_storage_executor(payload: Any, ctx: ExecutorContext) -> ExecutorResult:
    """Marketer's artifact storage: persists the reviewed marketing pack.
    Each artifact records its self-review and grounding status - only a pack
    whose artifacts all passed becomes approval-eligible (DESIGN.md sec. 5.8)."""
    p = payload if isinstance(payload, dict) else {}
    run_id = p.get("runId")
    brand_document_id = p.get("brandDocumentId")
    artifacts = p.get("artifacts")
    if not run_id or not brand_document_id or not isinstance(artifacts, list) or not artifacts:
        raise ValueError("artifact_storage needs runId, brandDocumentId, artifacts")

    with pool.connection() as conn, conn.cursor() as cur:
        for a in artifacts:
            a_type = a.get("type")
            if a_type not in TEXT_ARTIFACT_TYPES:
                raise ValueError(f"unsupported artifact type '{a_type}' for text storage")
            if not a.get("text"):
                raise ValueError(f"artifact {a_type} has empty text")
            cur.execute(
                """INSERT INTO marketing_artifacts
                     (tenant_id, run_id, brand_document_id, artifact_type, sequence_number,
                      channel, text_content, self_review_status, grounding_check_status,
                      review_feedback, approval_status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING')
                   ON CONFLICT (run_id, artifact_type, sequence_number) DO UPDATE
                     SET text_content = EXCLUDED.text_content,
                         channel = EXCLUDED.channel,
                         brand_document_id = EXCLUDED.brand_document_id,
                         self_review_status = EXCLUDED.self_review_status,
                         grounding_check_status = EXCLUDED.grounding_check_status,
                         review_feedback = EXCLUDED.review_feedback,
                         approval_status = 'PENDING',
                         updated_at = now()""",
                (
                    ctx.tenant_id,
                    run_id,
                    brand_document_id,
                    a_type,
                    a.get("sequenceNumber", 1),
                    a.get("channel"),
                    a["text"],
                    a.get("selfReviewStatus", "PENDING"),
                    a.get("groundingCheckStatus", "PENDING"),
                    a.get("reviewFeedback"),
                ),
            )
    return ExecutorResult(provider_reference=f"pack:{run_id}:{len(artifacts)} artifacts")


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
