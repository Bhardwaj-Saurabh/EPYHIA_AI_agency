"""Ops agent - directed business actions on the cheap tier (DESIGN.md sec. 3).
First responsibility: CATALOG_SETUP - extract the catalog from the completed
brief into structured rows the checkout and the grounding checks depend on."""

import json
import logging
from typing import Any

from .gate_client import get_run, model_call, request_action

log = logging.getLogger("workers.ops")

CATALOG_PROMPT = """Extract the rental catalog and business contact from this
business brief. Convert prices to integer cents/pence (GBP 1.50 -> 150).
Use ONLY facts stated in the brief - do not invent items, rates, or contact
details.

Respond ONLY with JSON:
{
  "items": [{"name": str, "description": str|null, "dayRateCents": int, "availableQty": int}],
  "businessContact": {"email": str|null, "phone": str|null, "address": str|null}
}"""


def setup_catalog(tenant_id: str, run_id: str) -> dict[str, Any]:
    state = get_run(run_id)
    brief = state["run"]["completed_brief"] or state["run"]["original_brief"]

    result = model_call(
        run_id=run_id,
        agent_name="ops",
        tier="luna",
        json_mode=True,
        messages=[
            {"role": "system", "content": CATALOG_PROMPT},
            {"role": "user", "content": brief},
        ],
    )
    extracted = json.loads(result["content"])
    items = extracted.get("items") or []
    if not items:
        raise RuntimeError("catalog extraction found no items in the brief")

    stored = request_action(
        tenant_id=tenant_id,
        run_id=run_id,
        agent_name="ops",
        action_type="business_storage",
        payload={
            "op": "set_catalog",
            "runId": run_id,
            "items": items,
            "businessContact": extracted.get("businessContact") or {},
        },
        idempotency_key=f"catalog-{run_id}",
    )
    log.info("catalog stored: %s", stored["action"]["provider_reference"])
    return {"items": len(items), "costMicrodollars": result["costMicrodollars"]}
