// Tier 3 - Action Gate. Sole credential holder; no public ingress in prod.
import "./env.js";
import express from "express";
import { pool } from "./db.js";
import {
  GateError,
  approveAction,
  getAction,
  pendingApprovals,
  requestAction,
} from "./pipeline.js";
import { EXECUTORS } from "./executors/index.js";
import { handleModelCall } from "./modelcall.js";

const app = express();
app.use(express.json({ limit: "10mb" }));
const port = Number(process.env.GATE_PORT ?? 8082);

const CONFIG = { executors: EXECUTORS };

function handle(res: express.Response, err: unknown): void {
  if (err instanceof GateError) {
    res.status(err.httpStatus).json({ error: err.message });
  } else {
    console.error("[gate] unexpected error:", err);
    res.status(500).json({ error: "internal error" });
  }
}

app.post("/actions", async (req, res) => {
  try {
    const { tenantId, runId, agentName, actionType, payload, idempotencyKey } = req.body ?? {};
    if (!tenantId || !agentName || !actionType || !idempotencyKey) {
      res.status(400).json({ error: "tenantId, agentName, actionType, idempotencyKey required" });
      return;
    }
    const { row, replayed } = await requestAction(
      { tenantId, runId, agentName, actionType, payload, idempotencyKey },
      CONFIG,
    );
    res.status(row.status === "pending_approval" ? 202 : 200).json({ action: row, replayed });
  } catch (err) {
    handle(res, err);
  }
});

app.get("/actions/:id", async (req, res) => {
  try {
    const row = await getAction(req.params.id);
    if (!row) {
      res.status(404).json({ error: "not found" });
      return;
    }
    res.json({ action: row });
  } catch (err) {
    handle(res, err);
  }
});

app.get("/approvals", async (_req, res) => {
  try {
    res.json({ pending: await pendingApprovals() });
  } catch (err) {
    handle(res, err);
  }
});

app.post("/approvals/:id/approve", async (req, res) => {
  try {
    const { approvedBy, payloadHash, payload } = req.body ?? {};
    if (!approvedBy || !payloadHash) {
      res.status(400).json({ error: "approvedBy and payloadHash required" });
      return;
    }
    const row = await approveAction(req.params.id, approvedBy, payloadHash, payload, CONFIG);
    res.json({ action: row });
  } catch (err) {
    handle(res, err);
  }
});

app.post("/model_call", async (req, res) => {
  try {
    const result = await handleModelCall(req.body ?? {});
    res.json(result);
  } catch (err) {
    handle(res, err);
  }
});

// Read API for Tier 2/Tier 1 (they hold no DB credentials).
app.get("/runs/:id", async (req, res) => {
  try {
    const run = await pool.query(
      `SELECT r.*,
              (COALESCE((SELECT SUM(cost_microdollars) FROM agent_calls WHERE run_id = r.id), 0)
             + COALESCE((SELECT SUM(provider_cost_microdollars) FROM actions WHERE run_id = r.id), 0)
              )::bigint AS spent_microdollars
         FROM runs r WHERE r.id = $1`,
      [req.params.id],
    );
    if (!run.rows[0]) {
      res.status(404).json({ error: "run not found" });
      return;
    }
    const tasks = await pool.query(
      `SELECT id, task_type, status, output_ref, updated_at FROM tasks WHERE run_id = $1 ORDER BY task_type`,
      [req.params.id],
    );
    const brand = run.rows[0].brand_document_id
      ? await pool.query(`SELECT id, version_number, full_text FROM brand_document WHERE id = $1`, [
          run.rows[0].brand_document_id,
        ])
      : null;
    res.json({ run: run.rows[0], tasks: tasks.rows, brandDocument: brand?.rows[0] ?? null });
  } catch (err) {
    handle(res, err);
  }
});

app.get("/health/live", (_req, res) => {
  res.json({ status: "ok", app: "gate" });
});

app.get("/health/ready", async (_req, res) => {
  try {
    await pool.query("SELECT 1");
    res.json({ status: "ok", app: "gate", db: "connected" });
  } catch {
    res.status(503).json({ status: "degraded", app: "gate", db: "unreachable" });
  }
});

app.listen(port, () => {
  console.log(`[gate] listening on :${port} (mode: ${process.env.RUN_MODE ?? "TEST"})`);
});
