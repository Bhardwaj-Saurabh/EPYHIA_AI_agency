"""Marketer milestone demo (README.md sec. 6, item 6): the content pack -
landing copy, 4 social posts, launch email, video storyboard - generated on
Terra, grounded in the brand doc + catalog, self-reviewed, then approved by
the administrator bound to the complete pack hash. Requires gate AND workers
running, with the Web Builder demo already completed."""

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
    _, tenant = api(GATE, "/tenants/by-slug/gate-demo")
    tenant_id = tenant["tenant"]["id"]
    _, submit = api(
        WORKERS,
        "/runs",
        method="POST",
        body={
            "tenantId": tenant_id,
            "brief": BRIEF,
            "budgetMicrodollars": 2_000_000,
            "approvedBy": "saurabh (demo)",
            "idempotencyKey": f"demo-run-{tenant_id[:6]}",
        },
    )
    run_id = submit["runId"]
    print(f"run: {run_id}")

    _, state = api(WORKERS, f"/runs/{run_id}")
    brand = state["brandDocument"]

    print("\n1) kicking the downstream pipeline (marketing is the remaining task)...")
    api(
        WORKERS,
        f"/runs/{run_id}/approve-brand",
        method="POST",
        body={"approvedBy": "saurabh (demo)", "contentHash": brand["content_hash"]},
    )

    print("2) Marketer working on Terra (generate -> grounding checks -> self-review)...")
    for _ in range(60):
        time.sleep(5)
        _, state = api(WORKERS, f"/runs/{run_id}")
        tasks = {t["task_type"]: t["status"] for t in state["tasks"]}
        print(f"   MARKETING_PACK={tasks.get('MARKETING_PACK')}          ", end="\r")
        if tasks.get("MARKETING_PACK") in ("AWAITING_PACK_APPROVAL", "AWAITING_VIDEO_RENDER", "FAILED"):
            break
    print()
    if tasks.get("MARKETING_PACK") == "FAILED":
        raise RuntimeError("marketer failed - check workers logs")

    print("\n3) the pack, as the administrator reviews it:")
    _, pack = api(GATE, f"/marketing-pack/{run_id}")
    for a in pack["artifacts"]:
        header = f"{a['artifact_type']}#{a['sequence_number']}"
        if a["channel"]:
            header += f" ({a['channel']})"
        first_line = a["text_content"].strip().split("\n")[0][:90]
        print(
            f"   {header}: self-review={a['self_review_status']} "
            f"grounding={a['grounding_check_status']}"
        )
        print(f"      | {first_line}...")
    print(f"   approval eligible: {pack['approvalEligible']}")
    print(f"   pack hash: {pack['packHash'][:16]}...")

    if pack["artifacts"][0]["approval_status"] != "APPROVED":
        print("\n4) tamper test: approving with a WRONG hash must fail...")
        status, result = api(
            GATE,
            f"/marketing-pack/{run_id}/approve",
            method="POST",
            body={"approvedBy": "saurabh (demo)", "packHash": "0" * 64},
        )
        print(f"   HTTP {status}: {result.get('error', result)}")

        print("\n5) approving the EXACT reviewed pack...")
        status, result = api(
            GATE,
            f"/marketing-pack/{run_id}/approve",
            method="POST",
            body={"approvedBy": "saurabh (demo)", "packHash": pack["packHash"]},
        )
        print(f"   HTTP {status}: {result}")

    print("\n6) final state & money:")
    _, state = api(WORKERS, f"/runs/{run_id}")
    for t in state["tasks"]:
        print(f"   {t['task_type']}: {t['status']}")
    print(
        f"   spent {state['run']['spent_microdollars']} of "
        f"{state['run']['approved_budget_microdollars']} microdollars"
    )

    print("\n7) idempotency - re-kicking the pipeline...")
    api(
        WORKERS,
        f"/runs/{run_id}/approve-brand",
        method="POST",
        body={"approvedBy": "saurabh (demo)", "contentHash": brand["content_hash"]},
    )
    time.sleep(8)
    _, pack2 = api(GATE, f"/marketing-pack/{run_id}")
    print(f"   pack unchanged: {pack2['packHash'] == pack['packHash']}")
    print(f"   artifacts still: {len(pack2['artifacts'])}")


if __name__ == "__main__":
    main()
