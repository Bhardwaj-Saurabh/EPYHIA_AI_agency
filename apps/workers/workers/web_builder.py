"""The Web Builder (DESIGN.md sections 3, 5.7, 11): approved brand document +
catalog -> a deployable site, through the anti-slop loop:

  generate (Sol) -> deterministic grounding checks -> independent design
  review (Terra, a different model judging the output) -> up to 3 rounds
  -> site stored -> deploy REQUESTED through the gate (hash-bound admin
  approval; nothing goes live from here).

It never deploys directly and never touches payment code.
"""

import json
import logging
from typing import Any

from .checks import check_site, format_rate_gbp
from .gate_client import get_run, model_call, request_action

log = logging.getLogger("workers.web_builder")

MAX_ROUNDS = 3

GENERATE_PROMPT = """You are the Web Builder of EPYHIA. Build a single-page,
high-converting landing page for a local rentals business - NOT a generic
corporate template.

Structure (in this order): a headline that says what the business does for
whom; one supporting sentence; the full catalog with exact prices as a clear,
scannable list or cards; how booking works (pay in full at booking, collection
only - no delivery); an accordion-style FAQ answering practical customer
questions (availability, collection, payment, service area); contact details.

Rules:
- Mobile-first, single file: inline CSS in one index.html, no external
  resources, no JavaScript frameworks (vanilla JS for the accordion is fine).
- Include <meta name="viewport" content="width=device-width, initial-scale=1">.
- Follow the brand document exactly: palette, typography guidance (system font
  stacks approximating the named fonts are fine), voice and tone.
- Every catalog item must appear with its price formatted EXACTLY as given in
  the catalog data (e.g. £12/day, £1.50/day).
- Honesty: no invented reviews, testimonials, star ratings, discounts,
  guarantees, or services (no delivery, no setup). Facts come from the brief
  and catalog only.

Respond ONLY with JSON: {"files": {"index.html": "<the complete html>"}}"""

REVIEW_PROMPT = """You are an independent design reviewer judging a small local
business's landing page against its brand document. Think about how the HTML
actually renders on mobile and desktop: hierarchy, spacing, readability.

Approve when the page is clearly good enough to represent a real local
business: on-brand palette and voice, a scannable catalog with prices, warm
practical copy, and a sensible mobile layout. Reject ONLY for concrete
violations you can name - a specific brand-document conflict, unreadable
hierarchy, a generic corporate/AI-template feel, or broken layout. Do not
reject for taste-level nitpicks or hypothetical improvements a reasonable
customer would never notice. If you reject, list at most 5 specific fixes.

Respond ONLY with JSON: {"approved": boolean, "feedback": string}"""


def _update_task(tenant_id: str, run_id: str, task_type: str, status: str) -> None:
    request_action(
        tenant_id=tenant_id,
        run_id=run_id,
        agent_name="system",
        action_type="task_storage",
        payload={"op": "update", "runId": run_id, "taskType": task_type, "status": status},
        idempotency_key=f"task-{run_id}-{task_type}-{status}",
    )


def build_website(tenant_id: str, run_id: str) -> dict[str, Any]:
    state = get_run(run_id)
    brand = state["brandDocument"]
    tenant = state["tenant"]
    catalog = state["catalog"]
    brief = state["run"]["completed_brief"] or state["run"]["original_brief"]

    if not brand or not brand.get("approved_by"):
        raise RuntimeError("brand document is not approved - Web Builder must not start")
    if not catalog:
        raise RuntimeError("catalog is empty - CATALOG_SETUP must run first")

    _update_task(tenant_id, run_id, "WEBSITE", "IN_PROGRESS")

    catalog_lines = "\n".join(
        f"- {item['name']}: {format_rate_gbp(item['day_rate'])}/day, "
        f"{item['available_qty']} available"
        + (f" - {item['description']}" if item.get("description") else "")
        for item in catalog
    )
    context = (
        f"BRAND DOCUMENT:\n{brand['full_text']}\n\n"
        f"BUSINESS BRIEF:\n{brief}\n\n"
        f"CATALOG (render prices exactly as shown):\n{catalog_lines}\n\n"
        f"CONTACT: email {tenant.get('business_email')}, phone {tenant.get('business_phone')}, "
        f"address {tenant.get('business_address')}"
    )

    feedback: str | None = None
    html = ""
    rounds = 0
    for rounds in range(1, MAX_ROUNDS + 1):
        if not feedback:
            user = context
        else:
            # Revision rounds REVISE the previous page - they apply the named
            # fixes rather than regenerating from scratch (cheaper, converges).
            user = (
                f"{context}\n\nPREVIOUS HTML (revise this - keep what works):\n{html}"
                f"\n\nREVISION FEEDBACK (apply every fix):\n{feedback}"
            )
        result = model_call(
            run_id=run_id,
            agent_name="web_builder",
            tier="sol",
            json_mode=True,
            max_tokens=20000,
            messages=[
                {"role": "system", "content": GENERATE_PROMPT},
                {"role": "user", "content": user},
            ],
        )
        files = json.loads(result["content"])["files"]
        html = files["index.html"]

        problems = check_site(html, catalog, tenant.get("business_email"))
        if problems:
            feedback = "Deterministic checks failed:\n" + "\n".join(f"- {p}" for p in problems)
            log.info("round %d: %d deterministic problems: %s", rounds, len(problems), problems)
            continue

        review = model_call(
            run_id=run_id,
            agent_name="web_builder",
            tier="terra",  # independent, cheaper reviewer per DESIGN.md sec. 11
            json_mode=True,
            messages=[
                {"role": "system", "content": REVIEW_PROMPT},
                {"role": "user", "content": f"BRAND DOCUMENT:\n{brand['full_text']}\n\nHTML:\n{html}"},
            ],
        )
        verdict = json.loads(review["content"])
        if verdict.get("approved"):
            log.info("round %d: review approved", rounds)
            break
        feedback = f"Design review rejected the page:\n{verdict.get('feedback', '')}"
        log.info("round %d: review rejected: %.400s", rounds, verdict.get("feedback", ""))
    else:
        _update_task(tenant_id, run_id, "WEBSITE", "FAILED")
        raise RuntimeError(f"site failed checks/review after {MAX_ROUNDS} rounds: {feedback}")

    project_name = f"epyhia-{tenant['business_slug']}"
    files = {"index.html": html}

    # Store the exact reviewed site so the admin approves what they can see.
    request_action(
        tenant_id=tenant_id,
        run_id=run_id,
        agent_name="web_builder",
        action_type="site_storage",
        payload={"runId": run_id, "files": files, "reviewRounds": rounds, "projectName": project_name},
        idempotency_key=f"site-{run_id}-r{rounds}",
    )

    # Request the deploy - hash-bound admin approval; execution happens only
    # after a human approves at the gate.
    deploy = request_action(
        tenant_id=tenant_id,
        run_id=run_id,
        agent_name="web_builder",
        action_type="deploy",
        payload={"projectName": project_name, "files": files},
        idempotency_key=f"deploy-{run_id}",
    )
    _update_task(tenant_id, run_id, "WEBSITE", "AWAITING_DEPLOY_APPROVAL")

    return {
        "reviewRounds": rounds,
        "deployActionId": deploy["action"]["id"],
        "deployStatus": deploy["action"]["status"],
        "payloadHash": deploy["action"]["payload_hash"],
    }
