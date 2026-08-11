"""THE full-pipeline demo (the submission script, README.md sec. 8): a brief
goes in; the Strategist writes the brand; the admin approves it; Ops builds
the catalog; the Web Builder ships a site whose go-live REQUIRES a synthetic
purchase; the Marketer's pack passes grounding + self-review and is
hash-approved; then a customer completes a real checkout and the order lands.
Finally: replay everything and show nothing duplicates.

A NEW business (The Biscuit Barn, pet boarding) through the SAME system -
the point of EPYHIA is that you choose the customer, not a new project.

Requires gate, workers AND gateway running."""

import hashlib
import hmac
import json
import os
import time
import uuid

import httpx

from . import env as _env  # noqa: F401

WORKERS = f"http://localhost:{os.environ.get('WORKERS_PORT', '8081')}"
GATEWAY = f"http://localhost:{os.environ.get('GATEWAY_PORT', '8080')}"
GATE = os.environ.get("GATE_URL", "http://localhost:8082").rstrip("/")
SECRET = os.environ["STRIPE_WEBHOOK_SECRET"]

SLUG = "biscuit-barn"
BUSINESS_NAME = "The Biscuit Barn"
OWNER_EMAIL = "owner@biscuitbarn.example"
INTERACTIVE = False


def pause(msg: str) -> None:
    """--interactive: hold before each human approval so the dashboard's
    pending state is visible on camera."""
    if INTERACTIVE:
        input(f"\n>>> {msg} - press Enter to approve... ")


BRIEF = """Business: The Biscuit Barn - a small kennels and cattery in Harrogate, UK,
boarding dogs and cats for owners who travel.

Spaces (per-day rates, pay in full at booking):
- Standard kennel (dogs up to 20kg): GBP 22/day, 8 available
- Large kennel (big breeds): GBP 28/day, 4 available
- Cattery pod: GBP 16/day, 6 available
- Daycare day-pass (dogs, drop-off 8am, collect by 6pm): GBP 18/day, 10 available

How it works: owners book online and pay in full at booking. Drop-off and
collection at the Barn by the owners - we don't offer pet transport. Daily
walks and feeding included; owners bring their pet's usual food.

Customers: pet owners within ~20 miles of Harrogate who want somewhere small
and personal rather than a big impersonal facility.

Contact: hello@biscuitbarn.example, +44 1423 000000,
The Biscuit Barn, Ripon Road, Harrogate HG3.

Tone wishes: warm, reassuring, a little playful - owners are trusting us with
family members. No invented reviews, awards, or guarantees."""


def api(base: str, path: str, *, method: str = "GET", body: dict | None = None) -> tuple[int, dict]:
    res = httpx.request(method, f"{base}{path}", json=body, timeout=600)
    return res.status_code, res.json()


def wait_for(run_id: str, task: str, until: set[str], max_polls: int = 120) -> dict:
    state: dict = {}
    for _ in range(max_polls):
        time.sleep(5)
        _, state = api(WORKERS, f"/runs/{run_id}")
        tasks = {t["task_type"]: t["status"] for t in state["tasks"]}
        print(f"   {task}={tasks.get(task)}                       ", end="\r")
        if tasks.get(task) in until | {"FAILED"}:
            break
    print()
    if {t["task_type"]: t["status"] for t in state["tasks"]}.get(task) == "FAILED":
        raise RuntimeError(f"{task} FAILED - check workers logs")
    return state


def main() -> None:
    print("=== 0) tenant onboarding ===")
    _, t = api(
        GATE,
        "/tenants",
        method="POST",
        body={
            "name": f"{BUSINESS_NAME} Owner",
            "email": OWNER_EMAIL,
            "businessName": BUSINESS_NAME,
            "businessSlug": SLUG,
        },
    )
    tenant_id = t["tenant"]["id"]
    print(f"tenant: {tenant_id}")

    print("\n=== 1) the brief goes in (budget $2.00) ===")
    status, submit = api(
        WORKERS,
        "/runs",
        method="POST",
        body={
            "tenantId": tenant_id,
            "brief": BRIEF,
            "budgetMicrodollars": 2_000_000,
            "approvedBy": "saurabh",
            "idempotencyKey": f"{SLUG}-run-{tenant_id[:6]}",
        },
    )
    run_id = submit["runId"]
    print(f"HTTP {status} run: {run_id} (replayed={submit['replayed']})")

    print("\n=== 2) Strategist (Sol) writes the brand ===")
    state: dict = {}
    for _ in range(60):
        time.sleep(3)
        _, state = api(WORKERS, f"/runs/{run_id}")
        print(f"   run={state['run']['status']}                  ", end="\r")
        if state["run"]["status"] != "CREATED":
            break
    print()
    if state["run"]["status"] == "AWAITING_CLARIFICATION":
        raise RuntimeError("strategist asked for clarification - extend the brief")
    brand = state["brandDocument"]
    print(f"brand document v{brand['version_number']}: {len(brand['full_text'])} chars")

    pause("brand document is ready (see the dashboard)")
    print("\n=== 3) admin approves the brand (hash-bound) ===")
    _, out = api(
        WORKERS,
        f"/runs/{run_id}/approve-brand",
        method="POST",
        body={"approvedBy": "saurabh", "contentHash": brand["content_hash"]},
    )
    print(out)

    print("\n=== 4) catalog + site (deploy waits for approval) ===")
    state = wait_for(run_id, "WEBSITE", {"AWAITING_DEPLOY_APPROVAL"})
    print(f"catalog: {[(c['name'], c['day_rate']) for c in state['catalog']]}")

    pause("deploy is pending approval (see the dashboard)")
    print(
        "\n=== 5) admin approves the exact reviewed site; go-live requires the synthetic purchase ==="
    )
    website_task = next(t for t in state["tasks"] if t["task_type"] == "WEBSITE")
    site = json.loads(website_task["output_ref"])
    payload = {"projectName": site["projectName"], "files": site["files"]}
    _, approvals = api(GATE, "/approvals")
    action = next(p for p in approvals["pending"] if p["action_type"] == "deploy")
    status, result = api(
        GATE,
        f"/approvals/{action['id']}/approve",
        method="POST",
        body={"approvedBy": "saurabh", "payloadHash": action["payload_hash"], "payload": payload},
    )
    if status != 200:
        raise RuntimeError(f"deploy approve failed: {result}")
    live_url = result["action"]["provider_reference"]
    print(f"LIVE (200 + synthetic purchase verified): {live_url}")

    print("\n=== 6) Marketer pack -> hash-bound approval ===")
    state = wait_for(run_id, "MARKETING_PACK", {"AWAITING_PACK_APPROVAL"})
    _, pack = api(GATE, f"/marketing-pack/{run_id}")
    print(f"artifacts: {len(pack['artifacts'])}, eligible: {pack['approvalEligible']}")
    pause("marketing pack awaits approval (see the dashboard)")
    _, out = api(
        GATE,
        f"/marketing-pack/{run_id}/approve",
        method="POST",
        body={"approvedBy": "saurabh", "packHash": pack["packHash"]},
    )
    print(out)

    print("\n=== 6b) launch video - deterministic render, approval-gated ===")
    _, req = api(
        WORKERS, f"/runs/{run_id}/render-videos", method="POST", body={"tenantId": tenant_id}
    )
    if req.get("status") == "pending_approval":
        pause("video render awaits approval (exact storyboard, hash-bound)")
        import re as _re

        _, pk = api(GATE, f"/marketing-pack/{run_id}")
        storyboard = next(
            a["text_content"]
            for a in pk["artifacts"]
            if a["artifact_type"] == "VIDEO_STORYBOARD" and a["approved_by"]
        )
        payload = {
            "runId": run_id,
            "storyboard": storyboard,
            "businessName": BUSINESS_NAME,
            "brandColors": _re.findall(r"#[0-9A-Fa-f]{6}", state["brandDocument"]["full_text"])[:6],
            "siteUrl": live_url,
            "contactEmail": state["tenant"]["business_email"],
        }
        status, out = api(
            GATE,
            f"/approvals/{req['actionId']}/approve",
            method="POST",
            body={"approvedBy": "saurabh", "payloadHash": req["payloadHash"], "payload": payload},
        )
        if status != 200:
            raise RuntimeError(f"video approve failed: {out}")
        print(f"rendered + stored: {out['action']['provider_reference']}")
    else:
        print(f"video render: {req}")

    print("\n=== 7) a customer books via the public chain and pays ===")
    cattery = min(state["catalog"], key=lambda c: c["day_rate"])
    checkout_key = uuid.uuid4().hex
    body = {
        "businessSlug": SLUG,
        "items": [{"rentalItemId": cattery["id"], "qty": 1}],
        "startDate": "2026-09-14",
        "endDate": "2026-09-18",
        "customer": {"name": "Cat Owner", "email": "catowner@example.com"},
        "siteUrl": live_url,
        "checkoutKey": checkout_key,
    }
    _, out = api(GATEWAY, "/api/checkout", method="POST", body=body)
    reservation_id, total = out["reservationId"], out["totalPence"]
    print(f"reservation {reservation_id}: {total}p (5 days x {cattery['day_rate']}p)")
    print(f"payable Stripe test session: {out['checkoutUrl'][:60]}...")

    with_session = httpx.get(f"{GATE}/reservations/{reservation_id}", timeout=30).json()
    session_id = with_session["reservation"]["stripe_checkout_session_id"]
    event = {
        "id": f"evt_demo_{uuid.uuid4().hex[:16]}",
        "object": "event",
        "type": "checkout.session.completed",
        "created": int(time.time()),
        "data": {
            "object": {
                "id": session_id,
                "object": "checkout.session",
                "payment_status": "paid",
                "amount_total": total,
                "currency": "gbp",
                "metadata": {"reservation_id": reservation_id},
            }
        },
    }
    payload_b = json.dumps(event).encode()
    ts = int(time.time())
    mac = hmac.new(SECRET.encode(), f"{ts}.".encode() + payload_b, hashlib.sha256).hexdigest()
    res = httpx.post(
        f"{GATEWAY}/webhooks/stripe",
        content=payload_b,
        headers={"stripe-signature": f"t={ts},v1={mac}", "content-type": "application/json"},
        timeout=60,
    )
    print(f"webhook: {res.json()}")
    order = httpx.get(f"{GATEWAY}/api/reservations/{reservation_id}", timeout=30).json()["order"]
    print(
        f"ORDER: {order['amount']}p {order['currency']} {order['status']} synthetic={order['is_synthetic']}"
    )

    print("\n=== 8) re-run everything; nothing duplicates ===")
    _, replay = api(
        WORKERS,
        "/runs",
        method="POST",
        body={
            "tenantId": tenant_id,
            "brief": BRIEF,
            "budgetMicrodollars": 2_000_000,
            "approvedBy": "saurabh",
            "idempotencyKey": f"{SLUG}-run-{tenant_id[:6]}",
        },
    )
    print(f"run replayed: {replay['replayed']} (same: {replay['runId'] == run_id})")
    res = httpx.post(
        f"{GATEWAY}/webhooks/stripe",
        content=payload_b,
        headers={"stripe-signature": f"t={ts},v1={mac}", "content-type": "application/json"},
        timeout=60,
    )
    print(f"webhook replay: {res.json()}")
    _, orders = api(GATE, f"/tenants/{tenant_id}/orders")
    print(f"orders: real={len(orders['real'])} synthetic={len(orders['synthetic'])}")

    _, state = api(WORKERS, f"/runs/{run_id}")
    print(
        f"\nrun spend: {state['run']['spent_microdollars']} of {state['run']['approved_budget_microdollars']} microdollars"
    )
    print(f"live site: {live_url}")


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    ap = argparse.ArgumentParser(description="EPYHIA full-pipeline demo")
    ap.add_argument("--slug", default=SLUG)
    ap.add_argument("--business", default=BUSINESS_NAME)
    ap.add_argument("--email", default=OWNER_EMAIL)
    ap.add_argument(
        "--brief-file", help="path to a plain-text brief (default: the Biscuit Barn brief)"
    )
    ap.add_argument("--interactive", action="store_true", help="pause before each human approval")
    args = ap.parse_args()
    SLUG, BUSINESS_NAME, OWNER_EMAIL = args.slug, args.business, args.email
    INTERACTIVE = args.interactive
    if args.brief_file:
        BRIEF = Path(args.brief_file).read_text()
    main()
