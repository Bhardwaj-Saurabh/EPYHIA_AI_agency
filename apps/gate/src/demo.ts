// End-to-end demo of the gate milestone (README.md sec. 6, item 2):
// request a deploy -> pending approval -> approve (hash-bound) -> REAL
// Cloudflare Pages deploy -> independent 200 verification -> audit row ->
// idempotent replay. Run the gate first: npm run dev -w @epyhia/gate
import "./env.js";
import { pool } from "./db.js";

const GATE = `http://localhost:${process.env.GATE_PORT ?? 8082}`;

async function api(path: string, init?: RequestInit): Promise<{ status: number; body: any }> {
  const res = await fetch(`${GATE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  return { status: res.status, body: await res.json() };
}

async function main(): Promise<void> {
  // Demo tenant (idempotent on slug).
  const tenant = await pool.query<{ id: string }>(
    `INSERT INTO tenants (name, email, business_name, business_slug)
     VALUES ('Gate Demo', 'demo@example.com', 'Gate Demo Biz', 'gate-demo')
     ON CONFLICT (business_slug) DO UPDATE SET name = EXCLUDED.name
     RETURNING id`,
  );
  const tenantId = tenant.rows[0]!.id;
  console.log(`tenant: ${tenantId}`);

  const suffix = tenantId.slice(0, 6);
  const payload = {
    projectName: `epyhia-gate-check-${suffix}`,
    files: {
      "/index.html": `<!doctype html><html><head><title>EPYHIA gate check</title></head>
<body><h1>EPYHIA Action Gate - first gated deploy</h1>
<p>This page exists to prove the gate pipeline end to end: capability check,
hash-bound human approval, idempotent execution, audit row, and independent
verification. It is a test artifact, not a business site.</p></body></html>`,
    },
  };
  const request = {
    tenantId,
    agentName: "web_builder",
    actionType: "deploy",
    payload,
    idempotencyKey: `demo-deploy-${suffix}`,
  };

  console.log("\n1) web_builder requests the deploy...");
  const first = await api("/actions", { method: "POST", body: JSON.stringify(request) });
  console.log(`   HTTP ${first.status} - status=${first.body.action.status}`);
  if (first.body.action.status === "executed") {
    console.log("   (already executed on a previous demo run - idempotency at work)");
  }

  const actionId = first.body.action.id;
  const hash = first.body.action.payload_hash;

  if (first.body.action.status === "pending_approval") {
    console.log("\n2) pending approvals visible to the admin:");
    const approvals = await api("/approvals");
    for (const a of approvals.body.pending) {
      console.log(`   ${a.id}  ${a.agent_name} wants '${a.action_type}'  hash=${a.payload_hash.slice(0, 16)}...`);
    }

    console.log("\n3) admin approves THIS exact payload (hash-bound). Deploying for real...");
    const approved = await api(`/approvals/${actionId}/approve`, {
      method: "POST",
      body: JSON.stringify({ approvedBy: "saurabh (demo)", payloadHash: hash, payload }),
    });
    if (approved.status !== 200) throw new Error(`approve failed: ${JSON.stringify(approved.body)}`);
    console.log(`   status=${approved.body.action.status}  url=${approved.body.action.provider_reference}`);
  }

  console.log("\n4) independent reality check from this client:");
  const action = await api(`/actions/${actionId}`);
  const url = action.body.action.provider_reference as string;
  const live = await fetch(url);
  console.log(`   GET ${url} -> HTTP ${live.status}`);

  console.log("\n5) replaying the SAME request (crash/retry simulation)...");
  const replay = await api("/actions", { method: "POST", body: JSON.stringify(request) });
  console.log(`   replayed=${replay.body.replayed}, same action id: ${replay.body.action.id === actionId}`);

  console.log("\n6) the audit trail:");
  const audit = await pool.query(
    `SELECT action_type, agent_name, mode, approval_status, approved_by, status,
            provider_reference, created_at, executed_at
       FROM actions WHERE id = $1`,
    [actionId],
  );
  console.table(audit.rows);
  const dep = await pool.query(
    `SELECT cloudflare_project_name, live_url, verified_at FROM deployments WHERE tenant_id = $1`,
    [tenantId],
  );
  console.table(dep.rows);

  const count = await pool.query(
    `SELECT count(*) FROM actions WHERE tenant_id = $1 AND action_type = 'deploy'`,
    [tenantId],
  );
  console.log(`deploy audit rows for this tenant: ${count.rows[0].count} (re-run created no duplicate)`);

  await pool.end();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
