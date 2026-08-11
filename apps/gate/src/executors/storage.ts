// Storage executors. Tier 2 holds no DB credentials, so all persistence is a
// gated action. run_shell is deterministic control-plane work (Flow 1 step 2);
// business_storage carries Ops' persistence ops (Flow 1 step 5).
import { createHash } from "node:crypto";
import { pool } from "../db.js";
import type { Executor } from "../pipeline.js";

interface RunShellPayload {
  brief: string;
  budgetMicrodollars: number;
  approvedBy: string;
  onboardingKey: string;
}

// Creates the onboarding request + run shell in one transaction, idempotent on
// (tenant_id, onboardingKey). Returns the existing run on replay.
export const runShellExecutor: Executor = async (payload, ctx) => {
  const p = payload as Partial<RunShellPayload>;
  if (!p?.brief || !p.budgetMicrodollars || !p.approvedBy || !p.onboardingKey) {
    throw new Error("run_shell needs brief, budgetMicrodollars, approvedBy, onboardingKey");
  }

  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    const existing = await client.query<{ run_id: string }>(
      `SELECT run_id FROM onboarding_requests WHERE tenant_id = $1 AND idempotency_key = $2 FOR UPDATE`,
      [ctx.tenantId, p.onboardingKey],
    );
    if (existing.rows[0]) {
      await client.query("COMMIT");
      return { providerReference: existing.rows[0].run_id };
    }

    const briefHash = createHash("sha256").update(p.brief).digest("hex");
    const run = await client.query<{ id: string }>(
      `INSERT INTO runs (tenant_id, original_brief, brief_hash, approved_budget_microdollars, budget_approved_by, status)
       VALUES ($1, $2, $3, $4, $5, 'CREATED') RETURNING id`,
      [ctx.tenantId, p.brief, briefHash, p.budgetMicrodollars, p.approvedBy],
    );
    const runId = run.rows[0]!.id;
    await client.query(
      `INSERT INTO onboarding_requests (tenant_id, idempotency_key, run_id) VALUES ($1, $2, $3)`,
      [ctx.tenantId, p.onboardingKey, runId],
    );
    await client.query("COMMIT");
    return { providerReference: runId };
  } catch (err) {
    await client.query("ROLLBACK");
    throw err;
  } finally {
    client.release();
  }
};

const TASK_TYPES = new Set(["CATALOG_SETUP", "WEBSITE", "MARKETING_PACK", "CHECKOUT"]);

interface FinalizeRunPayload {
  op: "finalize_run";
  runId: string;
  completedBrief: string;
  brandDocument: string;
  taskTypes: string[];
}

interface RecordQuestionsPayload {
  op: "record_questions";
  runId: string;
  questions: string[];
}

// Ops' persistence operations, each a single transaction.
export const businessStorageExecutor: Executor = async (payload, ctx) => {
  const p = payload as { op?: string } | null;
  if (p?.op === "finalize_run") return finalizeRun(payload as FinalizeRunPayload, ctx.tenantId);
  if (p?.op === "record_questions")
    return recordQuestions(payload as RecordQuestionsPayload, ctx.tenantId);
  throw new Error(`unknown business_storage op '${p?.op}'`);
};

async function finalizeRun(p: FinalizeRunPayload, tenantId: string) {
  if (!p.runId || !p.completedBrief || !p.brandDocument || !Array.isArray(p.taskTypes)) {
    throw new Error("finalize_run needs runId, completedBrief, brandDocument, taskTypes");
  }
  const badTypes = p.taskTypes.filter((t) => !TASK_TYPES.has(t));
  if (badTypes.length > 0) throw new Error(`unknown task types: ${badTypes.join(", ")}`);

  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    const version = await client.query<{ next: number }>(
      `SELECT COALESCE(MAX(version_number), 0) + 1 AS next FROM brand_document WHERE tenant_id = $1`,
      [tenantId],
    );
    const doc = await client.query<{ id: string }>(
      `INSERT INTO brand_document (tenant_id, version_number, full_text)
       VALUES ($1, $2, $3) RETURNING id`,
      [tenantId, version.rows[0]!.next, p.brandDocument],
    );
    const updated = await client.query(
      `UPDATE runs
          SET completed_brief = $2, brand_document_id = $3, status = 'AWAITING_BRAND_APPROVAL'
        WHERE id = $1 AND tenant_id = $4`,
      [p.runId, p.completedBrief, doc.rows[0]!.id, tenantId],
    );
    if (updated.rowCount === 0) throw new Error(`run ${p.runId} not found for tenant`);

    for (const taskType of p.taskTypes) {
      await client.query(
        `INSERT INTO tasks (tenant_id, run_id, task_type, status)
         VALUES ($1, $2, $3, 'BLOCKED_ON_BRAND_APPROVAL')
         ON CONFLICT (run_id, task_type)
           DO UPDATE SET status = 'BLOCKED_ON_BRAND_APPROVAL', updated_at = now()`,
        [tenantId, p.runId, taskType],
      );
    }
    // A clarification round, if any, is now resolved.
    await client.query(
      `UPDATE tasks SET status = 'DONE', updated_at = now()
        WHERE run_id = $1 AND task_type = 'CLARIFICATION'`,
      [p.runId],
    );
    await client.query("COMMIT");
    return { providerReference: doc.rows[0]!.id };
  } catch (err) {
    await client.query("ROLLBACK");
    throw err;
  } finally {
    client.release();
  }
}

async function recordQuestions(p: RecordQuestionsPayload, tenantId: string) {
  if (!p.runId || !Array.isArray(p.questions) || p.questions.length === 0) {
    throw new Error("record_questions needs runId and a non-empty questions list");
  }
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    await client.query(
      `INSERT INTO tasks (tenant_id, run_id, task_type, status, output_ref)
       VALUES ($1, $2, 'CLARIFICATION', 'AWAITING_ADMIN', $3)
       ON CONFLICT (run_id, task_type)
         DO UPDATE SET status = 'AWAITING_ADMIN', output_ref = $3, updated_at = now()`,
      [tenantId, p.runId, JSON.stringify({ questions: p.questions })],
    );
    const updated = await client.query(
      `UPDATE runs SET status = 'AWAITING_CLARIFICATION' WHERE id = $1 AND tenant_id = $2`,
      [p.runId, tenantId],
    );
    if (updated.rowCount === 0) throw new Error(`run ${p.runId} not found for tenant`);
    await client.query("COMMIT");
    return { providerReference: `clarification:${p.runId}` };
  } catch (err) {
    await client.query("ROLLBACK");
    throw err;
  } finally {
    client.release();
  }
}
