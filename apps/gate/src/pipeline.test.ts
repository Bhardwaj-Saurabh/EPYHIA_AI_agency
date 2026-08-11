// Pipeline invariant tests, run against the real Neon dev database.
// Each test asserts persisted state, not mock call counts.
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { pool } from "./db.js";
import { payloadHash } from "./hash.js";
import { GateError, approveAction, requestAction, type PipelineConfig } from "./pipeline.js";

let tenantId: string;

const CAPS = {
  web_builder: ["deploy"],
  ops: ["noop"],
} as const;

beforeAll(async () => {
  const { rows } = await pool.query<{ id: string }>(
    `INSERT INTO tenants (name, email, business_name, business_slug)
     VALUES ('Test Tenant', 'test@example.com', 'Test Biz', 'test-' || substr(md5(random()::text), 1, 12))
     RETURNING id`,
  );
  tenantId = rows[0]!.id;
});

afterAll(async () => {
  await pool.query(`DELETE FROM deployments WHERE tenant_id = $1`, [tenantId]);
  await pool.query(`DELETE FROM actions WHERE tenant_id = $1`, [tenantId]);
  await pool.query(`DELETE FROM tenants WHERE id = $1`, [tenantId]);
  await pool.end();
});

function config(overrides?: Partial<PipelineConfig>): PipelineConfig & { calls: () => number } {
  let count = 0;
  return {
    executors: {
      noop: async () => {
        count += 1;
        return { providerReference: `noop-${count}` };
      },
      deploy: async () => {
        count += 1;
        return { providerReference: `deploy-${count}` };
      },
    },
    capabilities: CAPS,
    requiresApproval: new Set(["deploy"]),
    calls: () => count,
    ...overrides,
  };
}

describe("capability check", () => {
  it("rejects an agent requesting an action it has no capability for", async () => {
    const cfg = config();
    await expect(
      requestAction(
        {
          tenantId,
          agentName: "web_builder",
          actionType: "noop", // ops-only in CAPS
          payload: {},
          idempotencyKey: "cap-1",
        },
        cfg,
      ),
    ).rejects.toMatchObject({ httpStatus: 403 });
    expect(cfg.calls()).toBe(0);
  });
});

describe("idempotency", () => {
  it("executes once and replays the same row on retry", async () => {
    const cfg = config();
    const req = {
      tenantId,
      agentName: "ops",
      actionType: "noop",
      payload: { n: 1 },
      idempotencyKey: "idem-1",
    };
    const first = await requestAction(req, cfg);
    expect(first.row.status).toBe("executed");
    expect(first.replayed).toBe(false);

    const second = await requestAction(req, cfg);
    expect(second.replayed).toBe(true);
    expect(second.row.id).toBe(first.row.id);
    expect(second.row.provider_reference).toBe(first.row.provider_reference);
    expect(cfg.calls()).toBe(1); // the executor never ran twice

    const { rows } = await pool.query(
      `SELECT count(*) FROM actions WHERE tenant_id = $1 AND idempotency_key = 'idem-1'`,
      [tenantId],
    );
    expect(Number(rows[0].count)).toBe(1); // exactly one audit row
  });

  it("rejects the same key with a different payload", async () => {
    const cfg = config();
    const base = { tenantId, agentName: "ops", actionType: "noop", idempotencyKey: "idem-2" };
    await requestAction({ ...base, payload: { v: "a" } }, cfg);
    await expect(requestAction({ ...base, payload: { v: "b" } }, cfg)).rejects.toMatchObject({
      httpStatus: 409,
    });
  });
});

describe("approval binding", () => {
  it("holds approval-gated actions, rejects a stale hash, executes on the right one", async () => {
    const cfg = config();
    const payload = { projectName: "test-site", files: { "/index.html": "<h1>hi</h1>" } };
    const { row } = await requestAction(
      {
        tenantId,
        agentName: "web_builder",
        actionType: "deploy",
        payload,
        idempotencyKey: "appr-1",
      },
      cfg,
    );
    expect(row.status).toBe("pending_approval");
    expect(cfg.calls()).toBe(0); // nothing executed before a human approves

    // Approval carrying the WRONG hash (superseded payload) is rejected.
    await expect(
      approveAction(row.id, "admin@test", payloadHash({ tampered: true }), payload, cfg),
    ).rejects.toMatchObject({ httpStatus: 409 });

    // Approval with the exact reviewed hash executes.
    const approved = await approveAction(row.id, "admin@test", row.payload_hash, payload, cfg);
    expect(approved.status).toBe("executed");
    expect(approved.approved_by).toBe("admin@test");
    expect(cfg.calls()).toBe(1);
  });

  it("rejects approval whose payload does not match the approved hash", async () => {
    const cfg = config();
    const payload = { projectName: "test-2", files: { "/index.html": "x" } };
    const { row } = await requestAction(
      {
        tenantId,
        agentName: "web_builder",
        actionType: "deploy",
        payload,
        idempotencyKey: "appr-2",
      },
      cfg,
    );
    await expect(
      approveAction(row.id, "admin@test", row.payload_hash, { swapped: "payload" }, cfg),
    ).rejects.toMatchObject({ httpStatus: 409 });
    expect(cfg.calls()).toBe(0);
  });
});

describe("budget", () => {
  it("rejects new actions once a run's approved budget is spent", async () => {
    const cfg = config();
    const run = await pool.query<{ id: string }>(
      `INSERT INTO runs (tenant_id, original_brief, brief_hash, approved_budget_microdollars, budget_approved_by)
       VALUES ($1, 'brief', 'hash', 1000, 'admin@test') RETURNING id`,
      [tenantId],
    );
    const runId = run.rows[0]!.id;
    await pool.query(
      `INSERT INTO agent_calls (run_id, agent_name, model_id, model_tier, cost_microdollars, status)
       VALUES ($1, 'ops', 'test-model', 'luna', 1000, 'completed')`,
      [runId],
    );

    await expect(
      requestAction(
        {
          tenantId,
          runId,
          agentName: "ops",
          actionType: "noop",
          payload: {},
          idempotencyKey: "budget-1",
        },
        cfg,
      ),
    ).rejects.toMatchObject({ httpStatus: 402 });

    await pool.query(`DELETE FROM agent_calls WHERE run_id = $1`, [runId]);
    await pool.query(`DELETE FROM runs WHERE id = $1`, [runId]);
  });
});

describe("errors", () => {
  it("GateError carries an http status", () => {
    expect(new GateError(403, "x").httpStatus).toBe(403);
  });
});
