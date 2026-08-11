"""End-to-end demo of the gate milestone (README.md sec. 6, item 2):
request a deploy -> pending approval -> approve (hash-bound) -> REAL
Cloudflare Pages deploy -> independent 200 verification -> audit row ->
idempotent replay. Run the gate first: uv run python -m gate.main"""

import os

import httpx

from .db import pool

GATE = f"http://localhost:{os.environ.get('GATE_PORT', '8082')}"


def api(path: str, *, method: str = "GET", body: dict | None = None) -> tuple[int, dict]:
    res = httpx.request(method, f"{GATE}{path}", json=body, timeout=300)
    return res.status_code, res.json()


def main() -> None:
    with pool.connection() as conn:
        tenant = conn.execute(
            """INSERT INTO tenants (name, email, business_name, business_slug)
               VALUES ('Gate Demo', 'demo@example.com', 'Gate Demo Biz', 'gate-demo')
               ON CONFLICT (business_slug) DO UPDATE SET name = EXCLUDED.name
               RETURNING id"""
        ).fetchone()
    tenant_id = str(tenant["id"])
    print(f"tenant: {tenant_id}")

    suffix = tenant_id[:6]
    payload = {
        "projectName": f"epyhia-gate-check-{suffix}",
        "files": {
            "/index.html": """<!doctype html><html><head><title>EPYHIA gate check</title></head>
<body><h1>EPYHIA Action Gate - first gated deploy</h1>
<p>This page exists to prove the gate pipeline end to end: capability check,
hash-bound human approval, idempotent execution, audit row, and independent
verification. It is a test artifact, not a business site.</p></body></html>"""
        },
    }
    request = {
        "tenantId": tenant_id,
        "agentName": "web_builder",
        "actionType": "deploy",
        "payload": payload,
        "idempotencyKey": f"demo-deploy-{suffix}",
    }

    print("\n1) web_builder requests the deploy...")
    status, first = api("/actions", method="POST", body=request)
    print(f"   HTTP {status} - status={first['action']['status']}")
    if first["action"]["status"] == "executed":
        print("   (already executed on a previous demo run - idempotency at work)")

    action_id = first["action"]["id"]
    digest = first["action"]["payload_hash"]

    if first["action"]["status"] == "pending_approval":
        print("\n2) pending approvals visible to the admin:")
        _, approvals = api("/approvals")
        for a in approvals["pending"]:
            print(f"   {a['id']}  {a['agent_name']} wants '{a['action_type']}'  hash={a['payload_hash'][:16]}...")

        print("\n3) admin approves THIS exact payload (hash-bound). Deploying for real...")
        status, approved = api(
            f"/approvals/{action_id}/approve",
            method="POST",
            body={"approvedBy": "saurabh (demo)", "payloadHash": digest, "payload": payload},
        )
        if status != 200:
            raise RuntimeError(f"approve failed: {approved}")
        print(f"   status={approved['action']['status']}  url={approved['action']['provider_reference']}")

    print("\n4) independent reality check from this client:")
    _, action = api(f"/actions/{action_id}")
    url = action["action"]["provider_reference"]
    live = httpx.get(url, timeout=30)
    print(f"   GET {url} -> HTTP {live.status_code}")

    print("\n5) replaying the SAME request (crash/retry simulation)...")
    _, replay = api("/actions", method="POST", body=request)
    print(f"   replayed={replay['replayed']}, same action id: {replay['action']['id'] == action_id}")

    print("\n6) the audit trail:")
    with pool.connection() as conn:
        audit = conn.execute(
            """SELECT action_type, agent_name, mode, approval_status, approved_by, status,
                      provider_reference, created_at, executed_at
                 FROM actions WHERE id = %s""",
            (action_id,),
        ).fetchone()
        dep = conn.execute(
            "SELECT cloudflare_project_name, live_url, verified_at FROM deployments WHERE tenant_id = %s",
            (tenant_id,),
        ).fetchone()
        count = conn.execute(
            "SELECT count(*) AS n FROM actions WHERE tenant_id = %s AND action_type = 'deploy'",
            (tenant_id,),
        ).fetchone()
    for k, v in audit.items():
        print(f"   {k}: {v}")
    print(f"   deployments: {dep}")
    print(f"   deploy audit rows for this tenant: {count['n']} (re-run created no duplicate)")


if __name__ == "__main__":
    main()
