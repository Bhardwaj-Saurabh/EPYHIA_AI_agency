// Tier 3 - Action Gate. Sole credential holder; no public ingress in prod.
// The gate pipeline (capability -> approval -> budget -> idempotency -> audit -> execute)
// lands here next, per DESIGN.md section 4.
import express from "express";

const app = express();
const port = Number(process.env.GATE_PORT ?? 8082);

app.get("/health/live", (_req, res) => {
  res.json({ status: "ok", app: "gate" });
});

app.get("/health/ready", (_req, res) => {
  // Will report DB connectivity once the gate is wired to Neon.
  const dbConfigured = Boolean(process.env.DATABASE_URL);
  res.json({ status: "ok", app: "gate", db: dbConfigured ? "configured" : "not configured" });
});

app.listen(port, () => {
  console.log(`[gate] listening on :${port} (mode: ${process.env.RUN_MODE ?? "TEST"})`);
});
