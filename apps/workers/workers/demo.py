"""Strategist milestone demo (README.md sec. 6, item 3): a real party-rentals
brief -> run shell -> real GPT-5.6 Sol call through the gate -> brand doc +
task plan persisted by Ops -> cost visible against the approved budget.
Requires gate AND workers running. Pure HTTP client - no DB, no credentials."""

import os
import time

import httpx

from . import env as _env  # noqa: F401

WORKERS = f"http://localhost:{os.environ.get('WORKERS_PORT', '8081')}"
GATE = os.environ.get("GATE_URL", "http://localhost:8082").rstrip("/")

BRIEF = """Business: BrightSide Party Rentals - party equipment rental for residential
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
reviews or discounts we never mentioned."""


def api(base: str, path: str, *, method: str = "GET", body: dict | None = None) -> tuple[int, dict]:
    res = httpx.request(method, f"{base}{path}", json=body, timeout=300)
    return res.status_code, res.json()


def main() -> None:
    status, tenant = api(GATE, "/tenants/by-slug/gate-demo")
    if status != 200:
        raise RuntimeError("demo tenant not found - run the gate demo first (python -m gate.demo)")
    tenant_id = tenant["tenant"]["id"]

    print("1) submitting the brief (budget: $2.00 = 2,000,000 microdollars)...")
    idempotency_key = f"demo-run-{tenant_id[:6]}"
    status, submit = api(
        WORKERS,
        "/runs",
        method="POST",
        body={
            "tenantId": tenant_id,
            "brief": BRIEF,
            "budgetMicrodollars": 2_000_000,
            "approvedBy": "saurabh (demo)",
            "idempotencyKey": idempotency_key,
        },
    )
    run_id = submit["runId"]
    print(f"   HTTP {status} runId={run_id} replayed={submit['replayed']}")

    print("2) polling while the Strategist works (real Sol call through the gate)...")
    state: dict = {}
    for _ in range(60):
        time.sleep(3)
        _, state = api(WORKERS, f"/runs/{run_id}")
        print(f"   status={state['run']['status']}", end="\r")
        if state["run"]["status"] != "CREATED":
            break
    print(f"\n   final status: {state['run']['status']}")

    if state["run"]["status"] == "AWAITING_CLARIFICATION":
        q = next((t for t in state["tasks"] if t["task_type"] == "CLARIFICATION"), None)
        print("   strategist asked:", q and q["output_ref"])
        return

    print("\n3) what got persisted:")
    print(f"   completed brief: {len(state['run']['completed_brief'] or '')} chars")
    brand = state.get("brandDocument") or {}
    print(f"   brand document v{brand.get('version_number')}: {len(brand.get('full_text') or '')} chars")
    print("   --- brand document (first 40 lines) ---")
    print("\n".join((brand.get("full_text") or "").split("\n")[:40]))
    print("   --- tasks ---")
    for t in state["tasks"]:
        print(f"   {t['task_type']}: {t['status']}")

    print("\n4) the money:")
    print(f"   spent {state['run']['spent_microdollars']} of {state['run']['approved_budget_microdollars']} microdollars")

    print("\n5) replaying the same submission (idempotency)...")
    status, replay = api(
        WORKERS,
        "/runs",
        method="POST",
        body={
            "tenantId": tenant_id,
            "brief": BRIEF,
            "budgetMicrodollars": 2_000_000,
            "approvedBy": "saurabh (demo)",
            "idempotencyKey": idempotency_key,
        },
    )
    print(
        f"   HTTP {status} same run: {replay['runId'] == run_id}, "
        f"replayed={replay['replayed']} (no second Sol call)"
    )


if __name__ == "__main__":
    main()
