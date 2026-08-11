"""Web Builder milestone demo (README.md sec. 6, item 4): approve the brand
document (hash-bound) -> Ops populates the catalog (Luna) -> Web Builder
generates the site (Sol) with grounding checks + independent Terra review ->
deploy requested through the gate -> admin approves the exact payload ->
REAL Cloudflare deploy, independently verified -> WEBSITE task DONE.
Requires gate AND workers running, and the Strategist demo run to exist."""

import json
import os
import time

import httpx

from . import env as _env  # noqa: F401
from .demo import BRIEF

WORKERS = f"http://localhost:{os.environ.get('WORKERS_PORT', '8081')}"
GATE = os.environ.get("GATE_URL", "http://localhost:8082").rstrip("/")


def api(base: str, path: str, *, method: str = "GET", body: dict | None = None) -> tuple[int, dict]:
    res = httpx.request(method, f"{base}{path}", json=body, timeout=600)
    return res.status_code, res.json()


def main() -> None:
    # Find the Strategist demo run via the gate-demo tenant.
    _, tenant = api(GATE, "/tenants/by-slug/gate-demo")
    tenant_id = tenant["tenant"]["id"]
    idempotency_key = f"demo-run-{tenant_id[:6]}"
    # Replay the EXACT Strategist-demo submission: same key, same payload, so
    # the run shell replays idempotently and hands back the run id.
    _, submit = api(
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
    if "runId" not in submit:
        raise RuntimeError(f"could not resolve demo run: {submit}")
    run_id = submit["runId"]
    print(f"run: {run_id} (replayed={submit.get('replayed')})")

    _, state = api(WORKERS, f"/runs/{run_id}")
    brand = state["brandDocument"]
    print(f"\n1) brand document v{brand['version_number']} hash={brand['content_hash'][:16]}...")
    print(f"   run status: {state['run']['status']}")

    if state["run"]["status"] == "AWAITING_BRAND_APPROVAL":
        print("\n2) administrator approves the brand document (hash-bound)...")
        status, approved = api(
            WORKERS,
            f"/runs/{run_id}/approve-brand",
            method="POST",
            body={"approvedBy": "saurabh (demo)", "contentHash": brand["content_hash"]},
        )
        print(f"   HTTP {status}: {approved}")
    else:
        print("\n2) brand already approved - continuing")
        api(
            WORKERS,
            f"/runs/{run_id}/approve-brand",
            method="POST",
            body={"approvedBy": "saurabh (demo)", "contentHash": brand["content_hash"]},
        )

    print("\n3) catalog (Luna) + site generation with review loop (Sol + Terra)...")
    deploy_action = None
    for _ in range(120):
        time.sleep(5)
        _, state = api(WORKERS, f"/runs/{run_id}")
        tasks = {t["task_type"]: t["status"] for t in state["tasks"]}
        print(f"   CATALOG={tasks.get('CATALOG_SETUP')}  WEBSITE={tasks.get('WEBSITE')}      ", end="\r")
        if tasks.get("WEBSITE") in ("AWAITING_DEPLOY_APPROVAL", "DONE", "FAILED"):
            break
    print(f"\n   catalog items: {len(state['catalog'])}")
    for item in state["catalog"]:
        print(f"     - {item['name']}: {item['day_rate']} cents/day, qty {item['available_qty']}")
    website_status = {t["task_type"]: t["status"] for t in state["tasks"]}.get("WEBSITE")
    if website_status == "FAILED":
        raise RuntimeError("web builder failed - check workers logs")

    if website_status == "AWAITING_DEPLOY_APPROVAL":
        print("\n4) deploy is pending at the gate; admin reviews the EXACT stored site...")
        website_task = next(t for t in state["tasks"] if t["task_type"] == "WEBSITE")
        site = json.loads(website_task["output_ref"])
        payload = {"projectName": site["projectName"], "files": site["files"]}
        print(f"   review rounds used: {site['reviewRounds']}")
        print(f"   site size: {len(site['files']['index.html'])} chars")

        _, approvals = api(GATE, "/approvals")
        pending = [p for p in approvals["pending"] if p["action_type"] == "deploy"]
        if not pending:
            raise RuntimeError("no pending deploy approval found")
        action = pending[0]
        print(f"   approving action {action['id']} hash={action['payload_hash'][:16]}...")
        status, result = api(
            GATE,
            f"/approvals/{action['id']}/approve",
            method="POST",
            body={
                "approvedBy": "saurabh (demo)",
                "payloadHash": action["payload_hash"],
                "payload": payload,
            },
        )
        if status != 200:
            raise RuntimeError(f"approve failed: {result}")
        deploy_action = result["action"]
        print(f"   deploy executed: {deploy_action['provider_reference']}")

    print("\n5) independent reality check:")
    _, state = api(WORKERS, f"/runs/{run_id}")
    url = state["deployment"]["live_url"]
    live = httpx.get(url, timeout=30)
    has_price = "£1.50" in live.text and "£140" in live.text
    print(f"   GET {url} -> HTTP {live.status_code}, real catalog prices on page: {has_price}")
    print(f"   WEBSITE task: {[t['status'] for t in state['tasks'] if t['task_type'] == 'WEBSITE'][0]}")

    print("\n6) the money & the audit:")
    print(f"   spent {state['run']['spent_microdollars']} of {state['run']['approved_budget_microdollars']} microdollars")

    print("\n7) idempotency - re-approving brand + replaying deploy request...")
    api(
        WORKERS,
        f"/runs/{run_id}/approve-brand",
        method="POST",
        body={"approvedBy": "saurabh (demo)", "contentHash": brand["content_hash"]},
    )
    time.sleep(8)
    _, state2 = api(WORKERS, f"/runs/{run_id}")
    print(f"   deployment unchanged: {state2['deployment']['live_url'] == url}")
    print(f"   catalog items still: {len(state2['catalog'])}")


if __name__ == "__main__":
    main()
