"""The Web Builder (DESIGN.md sections 3, 5.7, 11): approved brand document +
catalog -> a deployable site, through the anti-slop loop:

  generate (Sol) -> deterministic grounding checks -> independent design
  review (Terra, a different model judging the output) -> up to 3 rounds
  -> site stored -> deploy REQUESTED through the gate (hash-bound admin
  approval; nothing goes live from here).

It never deploys directly and never touches payment code.
"""

import hashlib
import json
import logging
import uuid
from typing import Any

from .checks import check_site, format_rate_gbp
from .gate_client import get_run, model_call, request_action

log = logging.getLogger("workers.web_builder")

MAX_ROUNDS = 3

GENERATE_PROMPT = """You are the Web Builder of EPYHIA. Build a single-page,
high-converting landing page for a local booking-based business - NOT a
generic corporate template. The page must look like a real design studio made
it: a customer landing on it should immediately trust this business.

Structure (in this order): a headline that says what the business does for
whom; one supporting sentence; the full catalog with exact prices as a clear,
scannable list or cards; the BOOKING FORM (contract below); how booking works,
stated exactly as the brief states it (payment terms, what happens after
booking); an accordion-style FAQ answering the practical customer questions
this specific business raises (availability, what to bring/expect, payment,
location and area - grounded in the brief); contact details.

ART DIRECTION (all of it, still in one inline <style> block):
- Design system first: define CSS custom properties on :root derived from the
  brand palette - --primary, --accent, plus 2-3 computed tints/shades of them
  (color-mix() is fine), --ink for text, --surface levels, a consistent
  spacing scale and a fluid type scale using clamp() so headlines feel large
  on desktop and right on mobile.
- Hero with real presence: a layered background built from the brand colours
  (e.g. a soft diagonal or radial gradient between --primary and --accent
  tints, optionally with one or two large decorative inline-SVG blobs/shapes
  at low opacity), a display-size headline, the supporting sentence, and a
  high-contrast CTA button that smooth-scrolls to the booking form.
- Sticky top nav: business name as a wordmark, 3-4 anchor links, background
  with backdrop-filter blur once scrolled.
- Catalog as cards: rounded corners, soft layered box-shadow, a clear price
  tag styled as the strongest element of the card, and a hover state that
  lifts the card (transform + shadow transition). Give each card a thin
  accent-coloured top border or icon-sized inline SVG so the grid has rhythm.
- Section rhythm: alternate plain and softly brand-tinted section backgrounds
  (very low-saturation tints of the palette), generous vertical padding, and
  section headings with a small accent detail (gradient text, underline bar,
  or eyebrow label) so the page never reads as one flat column.
- Motion, tastefully: reveal sections on scroll with a tiny translate+fade via
  IntersectionObserver; animate the accordion open/close; give buttons and
  links transition on hover/focus. Wrap ALL motion in
  @media (prefers-reduced-motion: no-preference).
- Accessibility is part of the polish: WCAG AA contrast on all text over
  gradients/tints, visible :focus-visible rings, semantic landmarks.

BOOKING FORM CONTRACT (follow exactly - the backend depends on it):
- <form id="booking-form"> containing: a quantity input per catalog item
  (type="number", min="0", value="0", data-item-id="<the item's id from the
  catalog data>"), start and end date inputs (type="date", required), customer
  name and email inputs (required), and a submit button styled per the brand.
- On submit (vanilla JS, preventDefault): collect items with qty > 0 as
  [{rentalItemId: el.dataset.itemId, qty: Number(el.value)}]; POST JSON to
  API_BASE + "/api/checkout" with body {businessSlug: BUSINESS_SLUG, items,
  startDate, endDate, customer: {name, email}, siteUrl: window.location.origin};
  on success redirect with window.location = response.checkoutUrl; on error
  show the server's error message near the form (no alert()).
- Do NOT display or compute any total in the browser - the price is
  authoritative on the server and the form says so in one honest sentence
  ("You'll see the exact total on the secure payment page.").
- Define const API_BASE and const BUSINESS_SLUG once at the top of the script,
  using the exact values given in the context.

Rules:
- Mobile-first, single file: inline CSS in one index.html, no external
  resources, no JavaScript frameworks (vanilla JS for the accordion and the
  booking form is fine).
- Include <meta name="viewport" content="width=device-width, initial-scale=1">.
- Follow the brand document exactly: palette, typography guidance (system font
  stacks approximating the named fonts are fine), voice and tone.
- Every catalog item must appear with its price formatted EXACTLY as given in
  the catalog data (e.g. £12/day, £1.50/day).
- Honesty: no invented reviews, testimonials, star ratings, discounts,
  guarantees, or services the brief does not state. Facts come from the brief
  and catalog only.

Respond ONLY with JSON: {"files": {"index.html": "<the complete html>"}}"""

REVIEW_PROMPT = """You are an independent design reviewer judging a small local
business's landing page against its brand document. Think about how the HTML
actually renders on mobile and desktop: hierarchy, spacing, readability.

Approve when the page is clearly good enough to represent a real local
business: on-brand palette and voice, a scannable catalog with prices, warm
practical copy, and a sensible mobile layout with visible design intent
(deliberate colour system, hierarchy, spacing - not default browser styling).
Reject ONLY for concrete violations you can name - a specific brand-document
conflict, unreadable hierarchy, a visually flat or default-looking page, a
generic corporate/AI-template feel, or broken layout. Do not
reject for taste-level nitpicks or hypothetical improvements a reasonable
customer would never notice. If you reject, list at most 5 specific fixes.

Respond ONLY with JSON: {"approved": boolean, "feedback": string}"""


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

    import os

    api_base = os.environ.get("GATEWAY_PUBLIC_URL", "http://localhost:8080").rstrip("/")
    catalog_lines = "\n".join(
        f"- {item['name']} (id: {item['id']}): {format_rate_gbp(item['day_rate'])}/day, "
        f"{item['available_qty']} available"
        + (f" - {item['description']}" if item.get("description") else "")
        for item in catalog
    )
    context = (
        f"BRAND DOCUMENT:\n{brand['full_text']}\n\n"
        f"BUSINESS BRIEF:\n{brief}\n\n"
        f"CATALOG (render prices exactly as shown; use each id in data-item-id):\n{catalog_lines}\n\n"
        f"CONTACT: email {tenant.get('business_email')}, phone {tenant.get('business_phone')}, "
        f"address {tenant.get('business_address')}\n"
        f"API_BASE: {api_base}\n"
        f"BUSINESS_SLUG: {tenant['business_slug']}"
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
            # Reasoning tokens share this cap with the HTML itself; the art
            # direction makes pages longer, and a truncated page loses its
            # booking form (= a wasted Sol round).
            max_tokens=28000,
            messages=[
                {"role": "system", "content": GENERATE_PROMPT},
                {"role": "user", "content": user},
            ],
        )
        files = json.loads(result["content"])["files"]
        html = files["index.html"]

        problems = check_site(
            html, catalog, tenant.get("business_email"), require_booking_form=True
        )
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
                {
                    "role": "user",
                    # The reviewer gets the SAME ground truth as the generator -
                    # anything less produces false "fabrication" rejections.
                    "content": f"BRAND DOCUMENT:\n{brand['full_text']}\n\n"
                    f"BUSINESS BRIEF (source of truth for facts):\n{brief}\n\n"
                    f"CATALOG (verified prices and availability - legitimate to show):\n{catalog_lines}\n\n"
                    f"CONTACT (verified): email {tenant.get('business_email')}, "
                    f"phone {tenant.get('business_phone')}, address {tenant.get('business_address')}\n\n"
                    f"HTML:\n{html}",
                },
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
    # Version-scoped keys: each distinct site version is its own audited
    # action; replays of the SAME version still replay.
    version = hashlib.sha256(html.encode("utf-8")).hexdigest()[:8]

    # Store the exact reviewed site so the admin approves what they can see.
    request_action(
        tenant_id=tenant_id,
        run_id=run_id,
        agent_name="web_builder",
        action_type="site_storage",
        payload={
            "runId": run_id,
            "files": files,
            "reviewRounds": rounds,
            "projectName": project_name,
        },
        idempotency_key=f"site-{run_id}-{version}",
    )

    # Request the deploy - hash-bound admin approval; execution happens only
    # after a human approves at the gate.
    deploy = request_action(
        tenant_id=tenant_id,
        run_id=run_id,
        agent_name="web_builder",
        action_type="deploy",
        payload={"projectName": project_name, "files": files},
        idempotency_key=f"deploy-{run_id}-{version}",
    )
    _update_task(tenant_id, run_id, "WEBSITE", "AWAITING_DEPLOY_APPROVAL")

    return {
        "reviewRounds": rounds,
        "deployActionId": deploy["action"]["id"],
        "deployStatus": deploy["action"]["status"],
        "payloadHash": deploy["action"]["payload_hash"],
    }
