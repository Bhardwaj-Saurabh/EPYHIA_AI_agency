// Tier 2 - Orchestration Runtime + agent workers (Strategist, Web Builder,
// Marketer, Ops). Holds no credentials; reaches providers only via the gate.
import express from "express";

const app = express();
const port = Number(process.env.WORKERS_PORT ?? 8081);

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
