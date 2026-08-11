// Tier 2 - Orchestration Runtime + agent workers. Holds no credentials;
// reaches providers and storage only via the Action Gate.
import "./env.js";
import express from "express";
import { GateClientError, getRun, requestAction } from "./gateClient.js";
import { runStrategist } from "./strategist.js";

const app = express();
app.use(express.json({ limit: "1mb" }));
const port = Number(process.env.WORKERS_PORT ?? 8081);

function handle(res: express.Response, err: unknown): void {
  if (err instanceof GateClientError) {
    res.status(err.status).json({ error: err.message });
  } else {
    console.error("[workers] unexpected error:", err);
    res.status(500).json({ error: "internal error" });
  }
}

// Flow 1 step 2: deterministic run-shell creation, then the Strategist runs
// in the background. Poll GET /runs/:id for progress.
app.post("/runs", async (req, res) => {
  try {
    const { tenantId, brief, budgetMicrodollars, approvedBy, idempotencyKey } = req.body ?? {};
    if (!tenantId || !brief || !budgetMicrodollars || !approvedBy || !idempotencyKey) {
      res.status(400).json({
        error: "tenantId, brief, budgetMicrodollars, approvedBy, idempotencyKey required",
      });
      return;
    }

    const shell = await requestAction({
      tenantId,
      agentName: "system",
      actionType: "run_shell",
      payload: { brief, budgetMicrodollars, approvedBy, onboardingKey: idempotencyKey },
      idempotencyKey,
    });
    const runId = shell.action.provider_reference;
    if (!runId) throw new Error("run_shell returned no run id");

    if (!shell.replayed) {
      runStrategist(tenantId, runId, brief).catch((err) =>
        console.error(`[workers] strategist failed for run ${runId}:`, err),
      );
    }
    res.status(shell.replayed ? 200 : 202).json({ runId, replayed: shell.replayed });
  } catch (err) {
    handle(res, err);
  }
});

// Flow 1 step 3: the administrator answers the Strategist's questions.
app.post("/runs/:id/clarify", async (req, res) => {
  try {
    const { tenantId, answers } = req.body ?? {};
    if (!tenantId || !answers) {
      res.status(400).json({ error: "tenantId and answers required" });
      return;
    }
    const { run } = await getRun(req.params.id);
    if (run.status !== "AWAITING_CLARIFICATION") {
      res.status(409).json({ error: `run is '${run.status}', not AWAITING_CLARIFICATION` });
      return;
    }
    const outcome = await runStrategist(tenantId, run.id, run.original_brief, String(answers));
    res.json({ runId: run.id, outcome });
  } catch (err) {
    handle(res, err);
  }
});

app.get("/runs/:id", async (req, res) => {
  try {
    res.json(await getRun(req.params.id));
  } catch (err) {
    handle(res, err);
  }
});

app.get("/health/live", (_req, res) => {
  res.json({ status: "ok", app: "workers" });
});

app.get("/health/ready", (_req, res) => {
  const gateConfigured = Boolean(process.env.GATE_URL);
  res.json({ status: "ok", app: "workers", gate: gateConfigured ? "configured" : "not configured" });
});

app.listen(port, () => {
  console.log(`[workers] listening on :${port}`);
});
