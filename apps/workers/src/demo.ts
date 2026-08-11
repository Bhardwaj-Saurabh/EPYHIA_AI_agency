// Strategist milestone demo (README.md sec. 6, item 3): a real party-rentals
// brief -> run shell -> real GPT-5.6 Sol call through the gate -> brand doc +
// task plan persisted by Ops -> cost visible against the approved budget.
// Requires gate AND workers running.
import "./env.js";

const WORKERS = `http://localhost:${process.env.WORKERS_PORT ?? 8081}`;
const GATE = (process.env.GATE_URL ?? "http://localhost:8082").replace(/\/+$/, "");

const BRIEF = `Business: BrightSide Party Rentals - party equipment rental for residential
and small-business events in and around Leeds, UK.

What we rent (per-day rates, pay in full at booking):
- Folding table (seats 8): GBP 12/day, 40 available
- Folding chair: GBP 1.50/day, 250 available
- Marquee tent 6m x 9m: GBP 140/day, 6 available
- Petrol generator 3kW: GBP 65/day, 8 available
- PA / speaker system with 2 mics: GBP 55/day, 5 available

Customers: families running birthdays/anniversaries and small businesses
running launches or street stalls, within ~25 miles of Leeds. Delivery not
included in v1 - customers collect or arrange their own transport.

Contact: hello@brightsideparty.example, +44 113 000 0000,
Unit 4, Kirkstall Industrial Park, Leeds LS4.

Tone wishes: warm, practical, no corporate jargon. Please don't invent
reviews or discounts we never mentioned.`;

async function api(base: string, path: string, init?: RequestInit) {
  const res = await fetch(`${base}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  return { status: res.status, body: (await res.json()) as any };
}

async function main(): Promise<void> {
  // Demo tenant via gate's DB? Tenants are created by Tier 1 signup later;
  // for the demo, reuse the gate-demo tenant created by the gate demo.
  const tenantRes = await fetch(`${GATE}/health/live`);
  if (!tenantRes.ok) throw new Error("gate is not running");

  // Look the tenant up through Neon is gate-internal; the demo uses the
  // slug-stable tenant the gate demo created. Fetch it via a tiny SQL-free
  // trick: create/reuse through the run itself is not possible, so this demo
  // expects TENANT_ID in env or falls back to querying the gate demo tenant.
  const tenantId = process.env.DEMO_TENANT_ID;
  if (!tenantId) {
    throw new Error(
      "set DEMO_TENANT_ID (the gate demo printed it, or query tenants by slug 'gate-demo')",
    );
  }

  console.log("1) submitting the brief (budget: $2.00 = 2,000,000 microdollars)...");
  const idempotencyKey = `demo-run-${tenantId.slice(0, 6)}`;
  const submit = await api(WORKERS, "/runs", {
    method: "POST",
    body: JSON.stringify({
      tenantId,
      brief: BRIEF,
      budgetMicrodollars: 2_000_000,
      approvedBy: "saurabh (demo)",
      idempotencyKey,
    }),
  });
  console.log(`   HTTP ${submit.status} runId=${submit.body.runId} replayed=${submit.body.replayed}`);
  const runId = submit.body.runId;

  console.log("2) polling while the Strategist works (real Sol call through the gate)...");
  let run: any;
  for (let i = 0; i < 60; i++) {
    await new Promise((r) => setTimeout(r, 3000));
    const state = await api(WORKERS, `/runs/${runId}`);
    run = state.body;
    process.stdout.write(`   status=${run.run.status}\r`);
    if (run.run.status !== "CREATED") break;
  }
  console.log(`\n   final status: ${run.run.status}`);

  if (run.run.status === "AWAITING_CLARIFICATION") {
    const q = run.tasks.find((t: any) => t.task_type === "CLARIFICATION");
    console.log("   strategist asked:", q?.output_ref);
    return;
  }

  console.log("\n3) what got persisted:");
  console.log(`   completed brief: ${String(run.run.completed_brief).length} chars`);
  console.log(`   brand document v${run.brandDocument?.version_number}: ${String(run.brandDocument?.full_text).length} chars`);
  console.log("   --- brand document (first 60 lines) ---");
  console.log(String(run.brandDocument?.full_text).split("\n").slice(0, 60).join("\n"));
  console.log("   --- tasks ---");
  for (const t of run.tasks) console.log(`   ${t.task_type}: ${t.status}`);

  console.log("\n4) the money:");
  console.log(`   spent ${run.run.spent_microdollars} of ${run.run.approved_budget_microdollars} microdollars`);

  console.log("\n5) replaying the same submission (idempotency)...");
  const replay = await api(WORKERS, "/runs", {
    method: "POST",
    body: JSON.stringify({
      tenantId,
      brief: BRIEF,
      budgetMicrodollars: 2_000_000,
      approvedBy: "saurabh (demo)",
      idempotencyKey,
    }),
  });
  console.log(`   HTTP ${replay.status} same run: ${replay.body.runId === runId}, replayed=${replay.body.replayed} (no second Sol call)`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
