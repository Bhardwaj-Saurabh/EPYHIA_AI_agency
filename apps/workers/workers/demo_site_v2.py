"""Site v2 demo: the booking form closes the customer loop, and go-live
verification now REQUIRES the synthetic end-to-end purchase (DESIGN.md sec.
5.7 / failure catalogue #8). Rebuild -> review loop -> hash-bound deploy
approval -> wrangler deploy -> 200 check -> synthetic purchase persists a
synthetic-flagged order -> only then is the deployment verified.

Requires all three services running."""

import json
import os
import time

import httpx

from . import env as _env  # noqa: F401

WORKERS = f"http://localhost:{os.environ.get('WORKERS_PORT', '8081')}"
GATE = os.environ.get("GATE_URL", "http://localhost:8082").rstrip("/")
RUN_ID = "17a0e9a4-47c6-4fe9-a341-cad9edb01a5b"


def api(base: str, path: str, *, method: str = "GET", body: dict | None = None) -> tuple[int, dict]:
    res = httpx.request(method, f"{base}{path}", json=body, timeout=600)
    return res.status_code, res.json()


def main() -> None:
    _, tenant = api(GATE, "/tenants/by-slug/gate-demo")
    tenant_id = tenant["tenant"]["id"]

    _, before = api(GATE, f"/tenants/{tenant_id}/orders")
    print(f"orders before: real={len(before['real'])} synthetic={len(before['synthetic'])}")

    print("\n1) rebuilding the site with the booking form (Sol + checks + Terra review)...")
    api(WORKERS, f"/runs/{RUN_ID}/rebuild-website", method="POST", body={"tenantId": tenant_id})
    tasks: dict = {}
    for _ in range(120):
        time.sleep(5)
        _, state = api(WORKERS, f"/runs/{RUN_ID}")
        tasks = {t["task_type"]: t["status"] for t in state["tasks"]}
        print(f"   WEBSITE={tasks.get('WEBSITE')}                 ", end="\r")
        if tasks.get("WEBSITE") in ("AWAITING_DEPLOY_APPROVAL", "FAILED"):
            break
    print()
    if tasks.get("WEBSITE") == "FAILED":
        raise RuntimeError("rebuild failed - check workers logs")

    print("2) approving the exact reviewed site; deploy now includes the synthetic purchase...")
    website_task = next(t for t in state["tasks"] if t["task_type"] == "WEBSITE")
    site = json.loads(website_task["output_ref"])
    payload = {"projectName": site["projectName"], "files": site["files"]}
    _, approvals = api(GATE, "/approvals")
    pending = [p for p in approvals["pending"] if p["action_type"] == "deploy"]
    if not pending:
        raise RuntimeError("no pending deploy approval")
    action = pending[0]
    status, result = api(
        GATE,
        f"/approvals/{action['id']}/approve",
        method="POST",
        body={"approvedBy": "saurabh (demo)", "payloadHash": action["payload_hash"], "payload": payload},
    )
    if status != 200:
        raise RuntimeError(f"approve failed: {result}")
    url = result["action"]["provider_reference"]
    print(f"   deployed + verified (200 AND synthetic purchase): {url}")

    print("\n3) the live page now sells:")
    live = httpx.get(url, timeout=30).text
    print(f"   booking form present: {'id=\"booking-form\"' in live}")
    print(f"   posts to /api/checkout: {'/api/checkout' in live}")
    print(f"   qty inputs for all items: {live.count('data-item-id=')} data-item-id attributes")

    print("\n4) go-live evidence (synthetic, flagged, separate from real orders):")
    _, after = api(GATE, f"/tenants/{tenant_id}/orders")
    print(f"   real orders: {len(after['real'])} (unchanged: {len(after['real']) == len(before['real'])})")
    print(f"   synthetic orders: {len(after['synthetic'])}")
    if after["synthetic"]:
        s = after["synthetic"][-1]
        print(f"   latest synthetic: {s['amount']}p {s['currency']} status={s['status']}")

    _, state = api(WORKERS, f"/runs/{RUN_ID}")
    print(f"\n5) run spend: {state['run']['spent_microdollars']} of {state['run']['approved_budget_microdollars']} microdollars")


if __name__ == "__main__":
    main()
