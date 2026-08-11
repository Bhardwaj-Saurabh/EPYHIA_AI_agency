"""The Marketer (DESIGN.md sections 3, 5.8): brand document + brief + catalog
-> the content pack (landing copy, 3-5 social posts, launch email, video
storyboard), on the mid tier (Terra). Every artifact passes a self-review AND
a deterministic grounding check before it is stored as approval-eligible;
approval binds to the complete pack hash at the gate.

The Marketer never publishes directly (sandbox/mail-catcher only, via the
gate) and never triggers a paid video render - that is a separate
exact-payload-and-cost approval after pack approval."""

import json
import logging
import uuid
from typing import Any

from .checks import check_marketing_text, format_rate_gbp
from .gate_client import get_run, model_call, request_action

log = logging.getLogger("workers.marketer")

MAX_ROUNDS = 3

GENERATE_PROMPT = """You are the Marketer of EPYHIA, writing the launch content
pack for a local rentals business. Everything must be grounded in the brand
document, brief, and catalog - never invent reviews, testimonials, discounts,
star ratings, delivery, or services. Prices may ONLY be exact catalog rates.

Produce:
- landingCopy: launch announcement copy for the website/blog (150-250 words)
- socialPosts: exactly 4 posts, each {"channel": one of "instagram"|"facebook"|
  "linkedin"|"x", "text": ...} - channel-appropriate length and tone, at most
  ONE tasteful emoji per post, no hashtag walls (max 3 hashtags)
- launchEmail: {"subject": ..., "body": ...} - a neutral launch announcement;
  assume NO prior relationship with the recipient (no "you asked", "as
  requested", or invented urgency)
- videoStoryboard: a 6-8 shot storyboard for a 30-second launch video, each
  shot with duration, visual description, and on-screen text - visuals must
  only show things the business actually offers

Voice, palette references, and tone come from the brand document. Mention the
live website URL where natural.

Respond ONLY with JSON:
{"landingCopy": str, "socialPosts": [{"channel": str, "text": str}],
 "launchEmail": {"subject": str, "body": str}, "videoStoryboard": str}"""

REVIEW_PROMPT = """You are the Marketer's self-review pass. Check each artifact
against the brand document and catalog: on-brand voice, no fabricated claims
(reviews, discounts, delivery, urgency, guarantees), prices exactly matching
the catalog, channel-appropriate social posts. Approve unless you can name a
concrete violation; if you reject, say exactly which artifact and what to fix.

Respond ONLY with JSON: {"approved": boolean, "feedback": string}"""


def _flatten_artifacts(pack: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts = [{"type": "LANDING_COPY", "sequenceNumber": 1, "text": pack["landingCopy"]}]
    for i, post in enumerate(pack["socialPosts"], start=1):
        artifacts.append(
            {
                "type": "SOCIAL_POST",
                "sequenceNumber": i,
                "channel": post["channel"],
                "text": post["text"],
            }
        )
    email = pack["launchEmail"]
    artifacts.append(
        {
            "type": "LAUNCH_EMAIL",
            "sequenceNumber": 1,
            "text": f"Subject: {email['subject']}\n\n{email['body']}",
        }
    )
    artifacts.append(
        {"type": "VIDEO_STORYBOARD", "sequenceNumber": 1, "text": pack["videoStoryboard"]}
    )
    return artifacts


def build_marketing_pack(tenant_id: str, run_id: str) -> dict[str, Any]:
    state = get_run(run_id)
    brand = state["brandDocument"]
    tenant = state["tenant"]
    catalog = state["catalog"]
    brief = state["run"]["completed_brief"] or state["run"]["original_brief"]
    live_url = (state.get("deployment") or {}).get("live_url")

    if not brand or not brand.get("approved_by"):
        raise RuntimeError("brand document is not approved - Marketer must not start")

    _update_task(tenant_id, run_id, "MARKETING_PACK", "IN_PROGRESS")

    catalog_lines = "\n".join(
        f"- {item['name']}: {format_rate_gbp(item['day_rate'])}/day" for item in catalog
    )
    context = (
        f"BRAND DOCUMENT:\n{brand['full_text']}\n\n"
        f"BUSINESS BRIEF:\n{brief}\n\n"
        f"CATALOG (only these prices may appear):\n{catalog_lines}\n\n"
        f"LIVE WEBSITE: {live_url}\n"
        f"CONTACT EMAIL: {tenant.get('business_email')}"
    )

    feedback: str | None = None
    artifacts: list[dict[str, Any]] = []
    rounds = 0
    for rounds in range(1, MAX_ROUNDS + 1):
        user = context if not feedback else f"{context}\n\nREVISION FEEDBACK (fix all of it):\n{feedback}"
        result = model_call(
            run_id=run_id,
            agent_name="marketer",
            tier="terra",
            json_mode=True,
            max_tokens=8000,
            messages=[
                {"role": "system", "content": GENERATE_PROMPT},
                {"role": "user", "content": user},
            ],
        )
        pack = json.loads(result["content"])
        if not 3 <= len(pack.get("socialPosts", [])) <= 5:
            feedback = "socialPosts must contain 3-5 posts"
            continue
        artifacts = _flatten_artifacts(pack)

        # Deterministic grounding check per artifact (DESIGN.md sec. 5.8).
        grounding_problems: list[str] = []
        for a in artifacts:
            for p in check_marketing_text(a["text"], catalog, tenant.get("business_email")):
                grounding_problems.append(f"[{a['type']}#{a['sequenceNumber']}] {p}")
        if grounding_problems:
            feedback = "Grounding checks failed:\n" + "\n".join(f"- {p}" for p in grounding_problems)
            log.info("round %d: grounding problems: %s", rounds, grounding_problems)
            continue

        # Self-review pass (same agent role, fresh judgment).
        review = model_call(
            run_id=run_id,
            agent_name="marketer",
            tier="terra",
            json_mode=True,
            messages=[
                {"role": "system", "content": REVIEW_PROMPT},
                {
                    "role": "user",
                    # The reviewer gets the SAME ground truth as the generator -
                    # anything less produces false "fabrication" rejections.
                    "content": f"BRAND DOCUMENT:\n{brand['full_text']}\n\n"
                    f"BUSINESS BRIEF (the source of truth for facts and specs):\n{brief}\n\n"
                    f"CATALOG PRICES:\n{catalog_lines}\n\n"
                    f"LIVE WEBSITE URL (legitimate to mention): {live_url}\n\n"
                    f"ARTIFACTS:\n{json.dumps(pack, indent=2)}",
                },
            ],
        )
        verdict = json.loads(review["content"])
        if verdict.get("approved"):
            log.info("round %d: self-review passed", rounds)
            break
        feedback = f"Self-review rejected the pack:\n{verdict.get('feedback', '')}"
        log.info("round %d: self-review rejected: %.400s", rounds, verdict.get("feedback", ""))
    else:
        _update_task(tenant_id, run_id, "MARKETING_PACK", "FAILED")
        raise RuntimeError(f"marketing pack failed checks after {MAX_ROUNDS} rounds: {feedback}")

    for a in artifacts:
        a["selfReviewStatus"] = "PASSED"
        a["groundingCheckStatus"] = "PASSED"

    request_action(
        tenant_id=tenant_id,
        run_id=run_id,
        agent_name="marketer",
        action_type="artifact_storage",
        payload={
            "runId": run_id,
            "brandDocumentId": brand["id"],
            "artifacts": artifacts,
        },
        idempotency_key=f"pack-{run_id}-r{rounds}",
    )
    _update_task(tenant_id, run_id, "MARKETING_PACK", "AWAITING_PACK_APPROVAL")
    return {"artifacts": len(artifacts), "reviewRounds": rounds}


def _update_task(tenant_id: str, run_id: str, task_type: str, status: str) -> None:
    # Attempt-scoped key: task transitions are internal bookkeeping and each
    # attempt (including retries after failure) should land and be audited.
    request_action(
        tenant_id=tenant_id,
        run_id=run_id,
        agent_name="system",
        action_type="task_storage",
        payload={"op": "update", "runId": run_id, "taskType": task_type, "status": status},
        idempotency_key=f"task-{run_id}-{task_type}-{status}-{uuid.uuid4().hex[:8]}",
    )
