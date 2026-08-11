"""The Strategist (DESIGN.md sections 3, 5): brief -> completeness check ->
completed brief + brand document + task plan. Delegation only - its single
tool is the gate's /model_call; persistence is delegated to Ops
(business_storage actions). It never touches a provider or the DB."""

import hashlib
import json
from typing import Any

from .gate_client import model_call, request_action

SYSTEM_PROMPT = """You are the Strategist of EPYHIA, an AI agency that builds one real
business per tenant: a deployed website, a marketing pack, and a working
checkout. You turn an administrator's business brief into the foundation the
other agents (Web Builder, Marketer, Ops) build on.

Honesty rules: never invent facts. Prices, item names, quantities, contact
details and claims must come from the brief. If required information is
missing, ask for it instead of guessing.

Required for a complete brief:
- what the business rents/sells, with per-item day rates and available quantities
- business name and contact details (email at minimum)
- target customers / service area
- anything the administrator explicitly wants or forbids

You respond ONLY with a JSON object, no prose around it:
{
  "complete": boolean,
  "questions": string[],            // when complete=false: the specific missing items
  "completedBrief": string,         // when complete=true: the full, normalized brief
  "brandDocument": string,          // when complete=true: markdown per the structure below
  "taskTypes": string[]             // when complete=true: subset of ["CATALOG_SETUP","WEBSITE","MARKETING_PACK","CHECKOUT"]
}

The brand document is markdown with exactly these sections (DESIGN.md sec. 9):
# Business Story (background, mission, strengths, target demographic)
# Logo & Usage
# Typography
# Voice & Tone (writing/grammar guidance)
# Color Palette
# Imagery Dos & Don'ts
# Social Layout Guidance
# Contact Details

Ground every section in the brief. Where the brief leaves brand aesthetics
open, make coherent choices and state them plainly - aesthetics may be chosen,
facts may not."""


def _parse(content: str) -> dict[str, Any]:
    try:
        out = json.loads(content)
    except ValueError as err:
        raise ValueError("strategist returned non-JSON output") from err
    if not isinstance(out.get("complete"), bool):
        raise ValueError("strategist JSON missing 'complete'")
    if not out["complete"] and not out.get("questions"):
        raise ValueError("incomplete brief but no questions returned")
    if out["complete"] and (
        not out.get("completedBrief")
        or not out.get("brandDocument")
        or not isinstance(out.get("taskTypes"), list)
    ):
        raise ValueError("complete brief but completedBrief/brandDocument/taskTypes missing")
    return out


def run_strategist(
    tenant_id: str, run_id: str, brief: str, clarifications: str | None = None
) -> dict[str, Any]:
    user_content = (
        f"{brief}\n\n--- Administrator clarifications ---\n{clarifications}"
        if clarifications
        else brief
    )

    result = model_call(
        run_id=run_id,
        agent_name="strategist",
        tier="sol",
        json_mode=True,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    )

    out = _parse(result["content"])

    if not out["complete"]:
        digest = hashlib.sha256(
            json.dumps(out["questions"], separators=(",", ":")).encode()
        ).hexdigest()[:12]
        request_action(
            tenant_id=tenant_id,
            run_id=run_id,
            agent_name="ops",
            action_type="business_storage",
            payload={"op": "record_questions", "runId": run_id, "questions": out["questions"]},
            idempotency_key=f"questions-{run_id}-{digest}",
        )
        return {
            "status": "AWAITING_CLARIFICATION",
            "questions": out["questions"],
            "costMicrodollars": result["costMicrodollars"],
        }

    # Strategist delegates persistence to Ops (Flow 1 step 5).
    finalize = request_action(
        tenant_id=tenant_id,
        run_id=run_id,
        agent_name="ops",
        action_type="business_storage",
        payload={
            "op": "finalize_run",
            "runId": run_id,
            "completedBrief": out["completedBrief"],
            "brandDocument": out["brandDocument"],
            "taskTypes": out["taskTypes"],
        },
        idempotency_key=f"finalize-{run_id}",
    )

    return {
        "status": "AWAITING_BRAND_APPROVAL",
        "brandDocumentId": finalize["action"].get("provider_reference"),
        "costMicrodollars": result["costMicrodollars"],
    }
