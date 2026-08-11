// Tier 1 - Public API Gateway. Public ingress, no credentials.
// Auth0 login, admin dashboard (static React build), checkout API, and raw
// Stripe webhook passthrough land here per DESIGN.md section 2.
import express from "express";

const app = express();
const port = Number(process.env.GATEWAY_PORT ?? 8080);

app.get("/health/live", (_req, res) => {
  res.json({ status: "ok", app: "gateway" });
});

app.get("/health/ready", (_req, res) => {
  const workersConfigured = Boolean(process.env.WORKERS_URL);
  res.json({
    status: "ok",
    app: "gateway",
    workers: workersConfigured ? "configured" : "not configured",
  });
});

app.listen(port, () => {
  console.log(`[gateway] listening on :${port}`);
});
