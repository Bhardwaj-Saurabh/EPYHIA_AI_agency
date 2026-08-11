// The Action Gate pipeline (DESIGN.md section 4):
//   capability check -> approval check -> run budget -> idempotency -> audit + cost log -> execute
// Every external effect in the system goes through requestAction/approveAction.
import type pg from "pg";
import { pool } from "./db.js";
import { payloadHash } from "./hash.js";
import { CAPABILITIES, REQUIRES_ADMIN_APPROVAL } from "./capabilities.js";

export interface ActionRequest {
  tenantId: string;
  runId?: string | null;
  agentName: string;
  actionType: string;
  payload: unknown;
  idempotencyKey: string;
}

export interface ExecutorResult {
  providerReference: string;
  providerCostMicrodollars?: number;
  output?: unknown;
}

export type Executor = (
  payload: unknown,
  ctx: { tenantId: string; actionId: string; mode: string },
) => Promise<ExecutorResult>;

export interface PipelineConfig {
  executors: Record<string, Executor>;
  capabilities?: Readonly<Record<string, readonly string[]>>;
  requiresApproval?: ReadonlySet<string>;
}

export class GateError extends Error {
  constructor(
    public readonly httpStatus: number,
    message: string,
  ) {
    super(message);
  }
}

export interface ActionRow {
  id: string;
  tenant_id: string;
  run_id: string | null;
  agent_name: string;
  action_type: string;
  payload_hash: string;
  idempotency_key: string;
  mode: string;
  approval_status: string;
  approved_by: string | null;
  approved_at: Date | null;
  provider_reference: string | null;
  provider_cost_microdollars: string;
  status: string;
  created_at: Date;
  executed_at: Date | null;
}

const MODE = (): string => process.env.RUN_MODE ?? "TEST";

async function runSpendMicrodollars(client: pg.PoolClient, runId: string): Promise<bigint> {
  const { rows } = await client.query<{ spent: string }>(
    `SELECT COALESCE((SELECT SUM(cost_microdollars) FROM agent_calls WHERE run_id = $1), 0)
          + COALESCE((SELECT SUM(provider_cost_microdollars) FROM actions WHERE run_id = $1), 0)
          AS spent`,
    [runId],
  );
  return BigInt(rows[0]?.spent ?? "0");
}

async function execute(
  row: ActionRow,
  payload: unknown,
  config: PipelineConfig,
): Promise<ActionRow> {
  const executor = config.executors[row.action_type];
  if (!executor) {
    throw new GateError(501, `no executor implemented for action type '${row.action_type}'`);
  }
  try {
    const result = await executor(payload, {
      tenantId: row.tenant_id,
      actionId: row.id,
      mode: row.mode,
    });
    const { rows } = await pool.query<ActionRow>(
      `UPDATE actions
         SET status = 'executed',
             provider_reference = $2,
             provider_cost_microdollars = $3,
             executed_at = now()
       WHERE id = $1
       RETURNING *`,
      [row.id, result.providerReference, result.providerCostMicrodollars ?? 0],
    );
    return rows[0]!;
  } catch (err) {
    await pool.query(`UPDATE actions SET status = 'failed', executed_at = now() WHERE id = $1`, [
      row.id,
    ]);
    console.error(`[gate] action ${row.id} (${row.action_type}) failed:`, err);
    throw new GateError(502, `action execution failed: ${err instanceof Error ? err.message : String(err)}`);
  }
}

/**
 * Request an action through the gate. Returns the audit row; when approval is
 * required the row comes back status=pending_approval and nothing executes.
 * Replaying the same (tenant, action_type, idempotency_key) never executes twice.
 */
export async function requestAction(
  req: ActionRequest,
  config: PipelineConfig,
): Promise<{ row: ActionRow; replayed: boolean }> {
  const capabilities = config.capabilities ?? CAPABILITIES;
  const requiresApproval = config.requiresApproval ?? REQUIRES_ADMIN_APPROVAL;

  // 1. Capability: may this agent request this action at all?
  const allowed = capabilities[req.agentName];
  if (!allowed || !allowed.includes(req.actionType)) {
    throw new GateError(403, `agent '${req.agentName}' has no capability '${req.actionType}'`);
  }

  // 2. Mode: TEST by default; LIVE is rejected until explicitly supported.
  const mode = MODE();
  if (mode !== "TEST") {
    throw new GateError(400, `unsupported mode '${mode}' - the gate runs TEST-mode only`);
  }

  const hash = payloadHash(req.payload);
  const needsApproval = requiresApproval.has(req.actionType);

  const client = await pool.connect();
  let row: ActionRow;
  let replay = false;
  try {
    await client.query("BEGIN");

    // 3. Idempotency: one row per (tenant, action_type, key), locked.
    const existing = await client.query<ActionRow>(
      `SELECT * FROM actions
        WHERE tenant_id = $1 AND action_type = $2 AND idempotency_key = $3
        FOR UPDATE`,
      [req.tenantId, req.actionType, req.idempotencyKey],
    );

    if (existing.rows[0]) {
      row = existing.rows[0];
      if (row.payload_hash !== hash) {
        throw new GateError(
          409,
          `idempotency key '${req.idempotencyKey}' was already used with a different payload`,
        );
      }
      // executed -> replay result; pending_approval -> still waiting; both
      // short-circuit. approved/failed fall through to (re-)execution below.
      if (row.status === "executed" || row.status === "pending_approval") {
        await client.query("COMMIT");
        return { row, replayed: true };
      }
      replay = true;
    } else {
      // 4. Budget: when the action belongs to a run, enforce the approved cap.
      if (req.runId) {
        const budget = await client.query<{ approved_budget_microdollars: string }>(
          `SELECT approved_budget_microdollars FROM runs WHERE id = $1`,
          [req.runId],
        );
        if (!budget.rows[0]) throw new GateError(404, `run ${req.runId} not found`);
        const spent = await runSpendMicrodollars(client, req.runId);
        if (spent >= BigInt(budget.rows[0].approved_budget_microdollars)) {
          throw new GateError(402, `run ${req.runId} has exhausted its approved budget`);
        }
      }

      // 5. Audit row - created before execution, so even a crash leaves a trace.
      const inserted = await client.query<ActionRow>(
        `INSERT INTO actions
           (tenant_id, run_id, agent_name, action_type, payload_hash, idempotency_key,
            mode, approval_status, status)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
         RETURNING *`,
        [
          req.tenantId,
          req.runId ?? null,
          req.agentName,
          req.actionType,
          hash,
          req.idempotencyKey,
          mode,
          needsApproval ? "PENDING" : "NOT_REQUIRED",
          needsApproval ? "pending_approval" : "approved",
        ],
      );
      row = inserted.rows[0]!;
    }

    await client.query("COMMIT");
  } catch (err) {
    await client.query("ROLLBACK");
    throw err;
  } finally {
    client.release();
  }

  // 6. Execute immediately only when no human approval is needed.
  if (row.status === "pending_approval") return { row, replayed: false };
  const executed = await execute(row, req.payload, config);
  return { row: executed, replayed: replay };
}

/**
 * Approve a pending action. The approval is bound to the payload hash the
 * approver saw - a mismatch (superseded payload) is rejected outright.
 * The caller must supply the original payload so the executor can run.
 */
export async function approveAction(
  actionId: string,
  approvedBy: string,
  approvedPayloadHash: string,
  payload: unknown,
  config: PipelineConfig,
): Promise<ActionRow> {
  const client = await pool.connect();
  let row: ActionRow;
  try {
    await client.query("BEGIN");
    const { rows } = await client.query<ActionRow>(
      `SELECT * FROM actions WHERE id = $1 FOR UPDATE`,
      [actionId],
    );
    if (!rows[0]) throw new GateError(404, `action ${actionId} not found`);
    row = rows[0];

    if (row.status !== "pending_approval") {
      throw new GateError(409, `action ${actionId} is '${row.status}', not pending_approval`);
    }
    if (row.payload_hash !== approvedPayloadHash) {
      throw new GateError(
        409,
        "approval hash does not match the action's payload - the payload was superseded; re-review it",
      );
    }
    if (payloadHash(payload) !== row.payload_hash) {
      throw new GateError(409, "supplied payload does not match the approved hash");
    }

    const updated = await client.query<ActionRow>(
      `UPDATE actions
         SET approval_status = 'APPROVED', approved_by = $2, approved_at = now(),
             status = 'approved'
       WHERE id = $1
       RETURNING *`,
      [actionId, approvedBy],
    );
    row = updated.rows[0]!;
    await client.query("COMMIT");
  } catch (err) {
    await client.query("ROLLBACK");
    throw err;
  } finally {
    client.release();
  }

  return execute(row, payload, config);
}

export async function getAction(actionId: string): Promise<ActionRow | null> {
  const { rows } = await pool.query<ActionRow>(`SELECT * FROM actions WHERE id = $1`, [actionId]);
  return rows[0] ?? null;
}

export async function pendingApprovals(): Promise<ActionRow[]> {
  const { rows } = await pool.query<ActionRow>(
    `SELECT * FROM actions WHERE status = 'pending_approval' ORDER BY created_at`,
  );
  return rows;
}
