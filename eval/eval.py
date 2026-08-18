"""EPYHIA eval suite (docs/ASSIGNMENT.md sec. 7, DESIGN.md sec. 14).

Runs every rubric check against the RUNNING agency and its real database, and
writes PRODUCT_EVAL.md - the submission artifact. Evidence over claims: checks
query HTTP responses, Neon rows and git history, never an agent's status field.

Usage (from the repo root, services running):
    uv run python eval/eval.py                 # all deterministic checks
    uv run python eval/eval.py --judge         # + the LLM brand-voice judge
    uv run python eval/eval.py --only no-duplicates
    uv run python eval/eval.py --slug biscuit-barn

The two decisive checks (order-persists, no-duplicates) run first.
"""

import argparse
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")

sys.path.insert(0, str(REPO_ROOT / "apps" / "workers"))
from workers.checks import check_marketing_text, check_site  # noqa: E402

GATE = os.environ.get("GATE_URL", "http://localhost:8082").rstrip("/")
WORKERS = f"http://localhost:{os.environ.get('WORKERS_PORT', '8081')}"
GATEWAY = f"http://localhost:{os.environ.get('GATEWAY_PORT', '8080')}"
AGENCY_URL = os.environ.get("EVAL_AGENCY_URL", "").rstrip("/")

JUDGE_CACHE = REPO_ROOT / "eval" / ".judge_cache.json"
PASS_SCORE = 7  # judge threshold out of 10

TIER_ORDER = {"luna": 0, "terra": 1, "sol": 2}
ENTITLED = {
    "strategist": "sol",
    "web_builder": "sol",
    "marketer": "terra",
    "ops": "luna",
    "evaluator": "luna",
    "system": "luna",
}
APPROVAL_REQUIRED = ("deploy", "video_render", "publish")


def db():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def fetch_live(url: str) -> tuple[int, str]:
    res = httpx.get(
        f"{url}?v={int(time.time())}",
        headers={"cache-control": "no-cache"},
        timeout=30,
        follow_redirects=True,
    )
    return res.status_code, res.text


class Ctx:
    """Everything the checks need about the business under evaluation."""

    def __init__(self, slug: str):
        self.slug = slug
        with db() as conn:
            self.tenant = conn.execute(
                "SELECT * FROM tenants WHERE business_slug = %s", (slug,)
            ).fetchone()
            if not self.tenant:
                raise SystemExit(f"no tenant with slug '{slug}' - run the pipeline first")
            tid = self.tenant["id"]
            self.run = conn.execute(
                "SELECT * FROM runs WHERE tenant_id = %s ORDER BY created_at DESC LIMIT 1",
                (tid,),
            ).fetchone()
            self.catalog = conn.execute(
                "SELECT * FROM rental_items WHERE tenant_id = %s ORDER BY name", (tid,)
            ).fetchall()
            self.deployment = conn.execute(
                "SELECT * FROM deployments WHERE tenant_id = %s", (tid,)
            ).fetchone()
            self.brand = conn.execute(
                "SELECT * FROM brand_document WHERE tenant_id = %s "
                "ORDER BY version_number DESC LIMIT 1",
                (tid,),
            ).fetchone()
        self.tenant_id = str(self.tenant["id"])
        self.run_id = str(self.run["id"]) if self.run else None
        self.live_url = (self.deployment or {}).get("live_url")
        self.services_up = self._services_up()

    def _services_up(self) -> bool:
        try:
            for base in (GATE, WORKERS, GATEWAY):
                if httpx.get(f"{base}/health/ready", timeout=5).status_code != 200:
                    return False
            return True
        except Exception:
            return False


# ---------------------------------------------------------------- deliverables


def check_order_persists(ctx: Ctx):
    """DECISIVE (a): a scripted purchase through the full public chain persists
    exactly one PAID order row; a redelivered webhook is a no-op."""
    if not ctx.services_up:
        return "SKIPPED", "services not running - start gate/workers/gateway"
    item = max(ctx.catalog, key=lambda c: c["available_qty"])
    # Future dates that drift with time so repeated eval runs don't exhaust stock.
    start = date.today() + timedelta(days=45 + (int(time.time()) // 60) % 300)
    end = start + timedelta(days=2)
    days = (end - start).days + 1  # billing is inclusive: drop-off through collection day
    body = {
        "businessSlug": ctx.slug,
        "items": [{"rentalItemId": str(item["id"]), "qty": 1}],
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "customer": {"name": "EPYHIA Eval", "email": "eval@epyhia.example"},
        "siteUrl": ctx.live_url,
        "checkoutKey": uuid.uuid4().hex,
    }
    res = httpx.post(f"{GATEWAY}/api/checkout", json=body, timeout=120)
    if res.status_code != 200:
        return "FAIL", f"public checkout returned {res.status_code}: {res.text[:200]}"
    out = res.json()
    reservation_id, total = out["reservationId"], out["totalPence"]
    expected = days * item["day_rate"]
    if total != expected:
        return "FAIL", (
            f"server total {total}p != expected {expected}p ({days}d x {item['day_rate']}p)"
        )

    with db() as conn:
        resv = conn.execute(
            "SELECT stripe_checkout_session_id FROM reservations WHERE id = %s",
            (reservation_id,),
        ).fetchone()
    event = {
        "id": f"evt_eval_{uuid.uuid4().hex[:16]}",
        "object": "event",
        "type": "checkout.session.completed",
        "created": int(time.time()),
        "data": {
            "object": {
                "id": resv["stripe_checkout_session_id"],
                "object": "checkout.session",
                "payment_status": "paid",
                "amount_total": total,
                "currency": "gbp",
                "metadata": {"reservation_id": reservation_id},
            }
        },
    }
    raw = json.dumps(event).encode()
    ts = int(time.time())
    sig = hmac.new(
        os.environ["STRIPE_WEBHOOK_SECRET"].encode(), f"{ts}.".encode() + raw, hashlib.sha256
    ).hexdigest()
    headers = {"stripe-signature": f"t={ts},v1={sig}", "content-type": "application/json"}
    first = httpx.post(
        f"{GATEWAY}/webhooks/stripe", content=raw, headers=headers, timeout=60
    ).json()

    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE reservation_id = %s", (reservation_id,)
        ).fetchall()
    if len(rows) != 1 or rows[0]["status"] != "PAID" or rows[0]["amount"] != total:
        got = [(r["status"], r["amount"]) for r in rows]
        return "FAIL", f"expected exactly one PAID order of {total}p, got {got}"

    replay = httpx.post(
        f"{GATEWAY}/webhooks/stripe", content=raw, headers=headers, timeout=60
    ).json()
    with db() as conn:
        after = conn.execute(
            "SELECT count(*) AS n FROM orders WHERE reservation_id = %s", (reservation_id,)
        ).fetchone()["n"]
    if not replay.get("duplicate") or after != 1:
        return "FAIL", f"webhook redelivery not a no-op: {replay}, rows={after}"
    return "PASS", (
        f"purchase {reservation_id}: {total}p PAID persisted "
        f"(webhook received={first.get('received')}); "
        f"redelivery duplicate=true, still exactly 1 row"
    )


def check_no_duplicates(ctx: Ctx):
    """DECISIVE (b): re-submitting the exact original brief with the original
    idempotency key yields the same run and creates nothing new."""
    if not ctx.services_up:
        return "SKIPPED", "services not running"
    counts_sql = """
        SELECT (SELECT count(*) FROM actions
                 WHERE tenant_id = %(t)s AND action_type = 'deploy') AS deploys,
               (SELECT count(*) FROM orders WHERE tenant_id = %(t)s) AS orders
    """
    with db() as conn:
        shell = conn.execute(
            "SELECT idempotency_key FROM actions "
            "WHERE action_type = 'run_shell' AND provider_reference = %s",
            (ctx.run_id,),
        ).fetchone()
        before = conn.execute(counts_sql, {"t": ctx.tenant_id}).fetchone()
    res = httpx.post(
        f"{WORKERS}/runs",
        json={
            "tenantId": ctx.tenant_id,
            "brief": ctx.run["original_brief"],
            "budgetMicrodollars": int(ctx.run["approved_budget_microdollars"]),
            "approvedBy": ctx.run["budget_approved_by"],
            "idempotencyKey": shell["idempotency_key"],
        },
        timeout=120,
    )
    out = res.json()
    if not out.get("replayed") or out.get("runId") != ctx.run_id:
        return "FAIL", f"replay was not recognized: HTTP {res.status_code} {out}"
    time.sleep(2)  # nothing async should have started; give it a beat anyway
    with db() as conn:
        after = conn.execute(counts_sql, {"t": ctx.tenant_id}).fetchone()
    if after != before:
        return "FAIL", f"replay created new rows: before {before}, after {after}"
    return "PASS", (
        f"same run {ctx.run_id} returned (replayed=true); deploy actions "
        f"{before['deploys']} and orders {before['orders']} unchanged"
    )


def check_live_site(ctx: Ctx):
    if not ctx.live_url:
        return "FAIL", "no deployment row / live_url for this tenant"
    status, html = fetch_live(ctx.live_url)
    if status != 200:
        return "FAIL", f"{ctx.live_url} returned HTTP {status}"
    problems = check_site(
        html, ctx.catalog, ctx.tenant.get("business_email"), require_booking_form=True
    )
    if problems:
        return "FAIL", f"{ctx.live_url} live but fails grounding: {problems[:5]}"
    verified = (ctx.deployment or {}).get("verified_at")
    if not verified:
        return "FAIL", "deployment never passed the gate's independent verification"
    return "PASS", (
        f"{ctx.live_url} HTTP 200; all {len(ctx.catalog)} items with exact prices; "
        f"booking form contract intact; gate-verified at {verified:%Y-%m-%d %H:%M} UTC"
    )


def check_marketing_pack(ctx: Ctx):
    with db() as conn:
        arts = conn.execute(
            "SELECT * FROM marketing_artifacts WHERE run_id = %s", (ctx.run_id,)
        ).fetchall()
    by_type: dict[str, list] = {}
    for a in arts:
        by_type.setdefault(a["artifact_type"], []).append(a)
    needed = (("LANDING_COPY", 1), ("SOCIAL_POST", 3), ("LAUNCH_EMAIL", 1), ("VIDEO_STORYBOARD", 1))
    missing = [t for t, need in needed if len(by_type.get(t, [])) < need]
    if missing:
        return "FAIL", f"pack incomplete - missing/short: {missing}"
    problems, unapproved = [], []
    for a in arts:
        if a["text_content"]:
            problems += check_marketing_text(
                a["text_content"], ctx.catalog, ctx.tenant.get("business_email")
            )
        if not a["approved_by"]:
            unapproved.append(a["artifact_type"])
    if problems:
        return "FAIL", f"grounding violations in stored artifacts: {problems[:5]}"
    if unapproved:
        return "FAIL", f"artifacts never hash-approved: {unapproved}"
    n_posts = len(by_type["SOCIAL_POST"])
    return "PASS", (
        f"{len(arts)} artifacts (landing copy, {n_posts} posts, launch email, storyboard), "
        f"all grounded against catalog+contact, all approved by '{arts[0]['approved_by']}'"
    )


def check_launch_video(ctx: Ctx):
    with db() as conn:
        vids = conn.execute(
            "SELECT artifact_type, r2_object_key FROM marketing_artifacts "
            "WHERE run_id = %s AND artifact_type IN ('VIDEO_LANDSCAPE','VIDEO_VERTICAL')",
            (ctx.run_id,),
        ).fetchall()
    have = {v["artifact_type"] for v in vids if v["r2_object_key"]}
    if {"VIDEO_LANDSCAPE", "VIDEO_VERTICAL"} <= have:
        return "PASS", f"rendered videos stored: {sorted(have)}"
    return "FAIL", (
        "no rendered video artifacts - MARKETING_PACK honestly stops at "
        "AWAITING_VIDEO_RENDER (paid render is an approval-gated action; key not provided)"
    )


# -------------------------------------------------------------------- not slop


def check_not_slop(ctx: Ctx):
    if not ctx.live_url:
        return "FAIL", "no live site to inspect"
    status, html = fetch_live(ctx.live_url)
    if status != 200:
        return "FAIL", f"live site returned {status}"
    lower = html.lower()
    problems = []
    for marker in ("lorem ipsum", "placeholder", "your text here", "[insert"):
        if marker in lower:
            problems.append(f"filler marker '{marker}'")
    tripwires = (
        (r"★|⭐|5[- ]star", "star ratings"),
        (r"testimonial", "testimonials"),
        (r"\b\d+%\s*off\b", "invented discounts"),
        (r"money[- ]back guarantee", "invented guarantees"),
    )
    for pattern, label in tripwires:
        if re.search(pattern, lower):
            problems.append(f"fabricated {label}")
    style = re.search(r"<style[^>]*>(.*?)</style>", html, re.S)
    design = {
        "design system (:root custom properties)": ":root" in lower and "--" in html,
        "fluid type (clamp)": "clamp(" in lower,
        "colour work (gradient/color-mix)": "gradient(" in lower or "color-mix" in lower,
        "motion behind prefers-reduced-motion": "prefers-reduced-motion" in lower,
        "substantial styling (>3KB inline CSS)": style is not None and len(style.group(1)) > 3000,
    }
    absent = [k for k, v in design.items() if not v]
    if problems:
        return "FAIL", "; ".join(problems)
    if absent:
        return "FAIL", f"page lacks design intent: {absent}"
    return "PASS", f"no filler, no fabrications; design markers all present: {list(design)}"


def check_brand_voice_judge(ctx: Ctx, enabled: bool):
    if not enabled:
        return "SKIPPED", "run with --judge to score (LLM call, costs money, cached by hash)"
    if not ctx.services_up or not ctx.live_url or not ctx.brand:
        return "SKIPPED", "needs running services, a live site and a brand document"
    _, html = fetch_live(ctx.live_url)
    text = re.sub(r"<style.*?</style>|<script.*?</script>", "", html, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()[:6000]
    key = hashlib.sha256((ctx.brand["content_hash"] + text).encode()).hexdigest()
    cache = json.loads(JUDGE_CACHE.read_text()) if JUDGE_CACHE.exists() else {}
    if key in cache:
        v = cache[key]
        status = "PASS" if v["score"] >= PASS_SCORE else "FAIL"
        return status, f"(cached) score {v['score']}/10 - {v['rationale']}"
    system = (
        "You are a strict brand reviewer. Score 1-10 how well this page's visible text "
        "matches the brand document's voice, tone and positioning, and how far it is from "
        "a generic AI template. 7+ means a real brand plausibly paid for this. "
        'Respond ONLY with JSON: {"score": int, "rationale": "one sentence"}'
    )
    res = httpx.post(
        f"{GATE}/model_call",
        json={
            "agentName": "evaluator",
            "runId": ctx.run_id,
            "tier": "luna",
            "json": True,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": f"BRAND DOCUMENT:\n{ctx.brand['full_text']}\n\nPAGE TEXT:\n{text}",
                },
            ],
        },
        timeout=120,
    )
    if res.status_code != 200:
        return "FAIL", f"judge call failed: {res.status_code} {res.text[:200]}"
    out = res.json()
    verdict = json.loads(out["content"])
    cache[key] = verdict
    JUDGE_CACHE.write_text(json.dumps(cache, indent=2))
    status = "PASS" if verdict["score"] >= PASS_SCORE else "FAIL"
    return status, (
        f"score {verdict['score']}/10 ({out.get('tier')} tier, {out.get('costMicrodollars', 0)} "
        f"microdollars, cost-logged through the gate) - {verdict['rationale']}"
    )


# ------------------------------------------------------------------------ crew


def check_tier_split_cost(ctx: Ctx):
    with db() as conn:
        calls = conn.execute(
            "SELECT agent_name, model_tier, cost_microdollars, input_tokens, output_tokens "
            "FROM agent_calls WHERE run_id = %s AND status = 'completed'",
            (ctx.run_id,),
        ).fetchall()
    if not calls:
        return "FAIL", "no completed agent_calls for the run"
    uncosted = [
        c
        for c in calls
        if c["cost_microdollars"] <= 0 or (c["input_tokens"] + c["output_tokens"]) <= 0
    ]
    if uncosted:
        return "FAIL", f"{len(uncosted)} completed calls have no cost/tokens logged"
    over = [
        c
        for c in calls
        if TIER_ORDER[c["model_tier"]] > TIER_ORDER[ENTITLED.get(c["agent_name"], "luna")]
    ]
    if over:
        pairs = [(c["agent_name"], c["model_tier"]) for c in over]
        return "FAIL", f"calls above entitled tier: {pairs}"
    tiers = sorted({c["model_tier"] for c in calls})
    if len(tiers) < 2:
        return "FAIL", f"only one tier used ({tiers}) - cheaper models never drafted"
    total = sum(c["cost_microdollars"] for c in calls)
    per_tier = {
        t: sum(c["cost_microdollars"] for c in calls if c["model_tier"] == t) for t in tiers
    }
    return "PASS", (
        f"{len(calls)} calls, every one cost-logged; tiers used {tiers}; "
        f"spend by tier (microdollars) {per_tier}; total {total}"
    )


def check_strategist_delegates(ctx: Ctx):
    with db() as conn:
        n = conn.execute(
            "SELECT count(*) AS n FROM actions WHERE agent_name = 'strategist'"
        ).fetchone()["n"]
        calls = conn.execute(
            "SELECT count(*) AS n FROM agent_calls WHERE run_id = %s AND agent_name = 'strategist'",
            (ctx.run_id,),
        ).fetchone()["n"]
    if n:
        return "FAIL", f"strategist requested {n} gate actions - it must delegate side effects"
    return "PASS", (
        f"zero action rows by 'strategist' (its only footprint is {calls} model calls); "
        f"all side effects requested by specialists/system"
    )


def check_brand_doc(ctx: Ctx):
    if not ctx.brand:
        return "FAIL", "no brand document"
    problems = []
    if not ctx.brand.get("content_hash"):
        problems.append("no content hash")
    if not ctx.brand.get("approved_by"):
        problems.append("never approved")
    with db() as conn:
        linked = conn.execute(
            "SELECT count(DISTINCT brand_document_id) AS n "
            "FROM marketing_artifacts WHERE run_id = %s",
            (ctx.run_id,),
        ).fetchone()["n"]
    if problems:
        return "FAIL", "; ".join(problems)
    return "PASS", (
        f"v{ctx.brand['version_number']} hash-approved by '{ctx.brand['approved_by']}'; "
        f"all pack artifacts reference the same brand document ({linked} version). "
        f"Edit-changes-behavior is shown live in the demo recording"
    )


# ------------------------------------------------------------------------ gate


def check_sole_credential_holder(ctx: Ctx):
    forbidden = ("STRIPE_", "AZURE_", "DATABASE_URL", "CLOUDFLARE_", "R2_", "OPENAI")
    offenders = []
    for tier_dir in ("apps/workers", "apps/gateway"):
        for py in (REPO_ROOT / tier_dir).rglob("*.py"):
            # Demo/eval scripts play the role of Stripe (they sign synthetic
            # webhook events), so they legitimately hold the signing secret;
            # they are not part of the tier's runtime.
            if py.name.startswith("demo"):
                continue
            src = py.read_text()
            for line in src.splitlines():
                if "environ" not in line:
                    continue
                for name in forbidden:
                    if name in line:
                        offenders.append(f"{py.relative_to(REPO_ROOT)}: {line.strip()[:80]}")
    env_tracked = subprocess.run(
        ["git", "ls-files", ".env"], capture_output=True, text=True, cwd=REPO_ROOT
    ).stdout.strip()
    keys = subprocess.run(
        [
            "git",
            "grep",
            "-lE",
            r"(sk_(test|live)|whsec|npg)_[A-Za-z0-9]{20,}",
            "--",
            ".",
            ":!eval/eval.py",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    ).stdout.strip()
    problems = offenders
    if env_tracked:
        problems = problems + [f".env is tracked by git: {env_tracked}"]
    if keys:
        problems = problems + [f"key-shaped strings in tracked files: {keys}"]
    if problems:
        return "FAIL", "; ".join(problems[:5])
    return "PASS", (
        "Tier 1/2 source reads no provider secrets from the environment; .env untracked; "
        "no key-shaped strings in any tracked file"
    )


def check_approval_before_irreversible(ctx: Ctx):
    with db() as conn:
        rows = conn.execute(
            "SELECT action_type, approved_by, approved_at, executed_at FROM actions "
            "WHERE tenant_id = %s AND action_type = ANY(%s) AND status = 'executed'",
            (ctx.tenant_id, list(APPROVAL_REQUIRED)),
        ).fetchall()
    if not rows:
        return "FAIL", "no executed approval-gated actions to inspect"
    bad = [
        r
        for r in rows
        if not r["approved_by"]
        or not r["approved_at"]
        or (r["executed_at"] and r["approved_at"] > r["executed_at"])
    ]
    if bad:
        return "FAIL", f"{len(bad)} irreversible actions executed without prior human approval"
    kinds = sorted({r["action_type"] for r in rows})
    return "PASS", (
        f"{len(rows)} executed irreversible actions ({kinds}), every one carries "
        f"approved_by + approved_at <= executed_at"
    )


def check_audit_cost(ctx: Ctx):
    with db() as conn:
        acts = conn.execute(
            "SELECT * FROM actions WHERE tenant_id = %s", (ctx.tenant_id,)
        ).fetchall()
        spend = conn.execute(
            "SELECT COALESCE((SELECT SUM(cost_microdollars) FROM agent_calls "
            "                  WHERE run_id = %(r)s), 0)"
            "     + COALESCE((SELECT SUM(provider_cost_microdollars) FROM actions "
            "                  WHERE run_id = %(r)s), 0) AS s",
            {"r": ctx.run_id},
        ).fetchone()["s"]
    spend = int(spend)
    problems = []
    for a in acts:
        if not a["payload_hash"] or not a["idempotency_key"]:
            problems.append(f"{a['action_type']} missing hash/key")
        if a["mode"] != "TEST":
            problems.append(f"{a['action_type']} ran in mode={a['mode']}")
        if (
            a["status"] == "executed"
            and a["action_type"] in ("deploy", "checkout_session")
            and not a["provider_reference"]
        ):
            problems.append(f"executed {a['action_type']} has no provider reference")
    budget = int(ctx.run["approved_budget_microdollars"])
    if spend > budget:
        problems.append(f"spend {spend} exceeds approved budget {budget}")
    if problems:
        return "FAIL", "; ".join(problems[:5])
    return "PASS", (
        f"{len(acts)} audit rows, all mode=TEST with payload hash + idempotency key; "
        f"run spend {int(spend)} of {budget} microdollars "
        f"(${spend / 1e6:.2f} of ${budget / 1e6:.2f})"
    )


# ---------------------------------------------------------------------- design


def check_design_first(ctx: Ctx):
    root = subprocess.run(
        ["git", "log", "--reverse", "--format=%H"], capture_output=True, text=True, cwd=REPO_ROOT
    ).stdout.split()[0]
    files = subprocess.run(
        ["git", "show", "--name-only", "--format=", root],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    ).stdout.split()
    if files != ["DESIGN.md"]:
        return "FAIL", f"root commit {root[:8]} touches {files}, not DESIGN.md alone"
    return "PASS", f"root commit {root[:8]} contains exactly one file: DESIGN.md (no code)"


def check_failure_catalogue(ctx: Ctx):
    text = (REPO_ROOT / "DESIGN.md").read_text()
    m = re.search(r"^## 12\..*?$(.*?)(?=^## )", text, re.S | re.M)
    if not m:
        return "FAIL", "DESIGN.md has no section 12 failure catalogue"
    entries = re.findall(r"^\d+\.\s+\*\*(.+?)\*\*", m.group(1), re.M)
    if len(entries) < 5:
        return "FAIL", f"only {len(entries)} catalogued failures; rubric requires 5"
    return "PASS", f"{len(entries)} failure modes, each stated with its control: {entries[:3]}..."


# ----------------------------------------------------------------- clean clone


def check_env_example(ctx: Ctx):
    example = (REPO_ROOT / ".env.example").read_text()
    used: set[str] = set()
    pattern = r"""os\.environ(?:\.get)?[\(\[]\s*["']([A-Z][A-Z0-9_]+)["']"""
    for py in (REPO_ROOT / "apps").rglob("*.py"):
        used |= set(re.findall(pattern, py.read_text()))
    missing = sorted(v for v in used if v not in example)
    if missing:
        return "FAIL", f"variables read in apps/ but absent from .env.example: {missing}"
    return "PASS", (
        f"all {len(used)} environment variables read anywhere in apps/ "
        f"are documented in .env.example"
    )


def check_agency_deployed(ctx: Ctx):
    if AGENCY_URL:
        try:
            res = httpx.get(f"{AGENCY_URL}/health/ready", timeout=15)
            if res.status_code == 200:
                return "PASS", f"deployed agency healthy at {AGENCY_URL}"
            return "FAIL", f"{AGENCY_URL}/health/ready returned {res.status_code}"
        except Exception as err:
            return "FAIL", f"deployed agency unreachable: {err}"
    if ctx.services_up:
        return "PARTIAL", (
            "all three tiers healthy locally (gate/workers/gateway); Fly deploy pending - "
            "set EVAL_AGENCY_URL once deployed for full credit"
        )
    return "FAIL", "agency not deployed and not running locally"


# --------------------------------------------------------------------- harness

CHECKS = {
    "order-persists": check_order_persists,
    "no-duplicates": check_no_duplicates,
    "live-site": check_live_site,
    "marketing-pack": check_marketing_pack,
    "launch-video": check_launch_video,
    "not-slop": check_not_slop,
    "brand-voice-judge": check_brand_voice_judge,
    "tier-split-cost": check_tier_split_cost,
    "strategist-delegates": check_strategist_delegates,
    "brand-doc": check_brand_doc,
    "sole-credential-holder": check_sole_credential_holder,
    "approval-before-irreversible": check_approval_before_irreversible,
    "audit-cost": check_audit_cost,
    "design-first": check_design_first,
    "failure-catalogue": check_failure_catalogue,
    "env-example": check_env_example,
    "agency-deployed": check_agency_deployed,
}
PARTIAL_CREDIT = {"agency-deployed": 2}  # points awarded on PARTIAL


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", default="biscuit-barn")
    ap.add_argument("--only", help="run a single check by id")
    ap.add_argument("--judge", action="store_true", help="include the LLM brand-voice judge")
    args = ap.parse_args()

    rubric = json.loads((REPO_ROOT / "eval" / "rubric.json").read_text())
    rows = {c["id"]: c for c in rubric["criteria"]}
    ctx = Ctx(args.slug)

    results = []
    for cid, fn in CHECKS.items():
        if args.only and cid != args.only:
            continue
        try:
            if cid == "brand-voice-judge":
                status, evidence = fn(ctx, args.judge)
            else:
                status, evidence = fn(ctx)
        except Exception as err:  # a crashed check is a failed check, with evidence
            status, evidence = "FAIL", f"check crashed: {err!r}"
        crit = rows[cid]
        if status == "PASS":
            earned = crit["points"]
        elif status == "PARTIAL":
            earned = PARTIAL_CREDIT.get(cid, 0)
        else:
            earned = 0
        results.append(
            {
                "id": cid,
                "area": crit["area"],
                "points": crit["points"],
                "earned": earned,
                "status": status,
                "evidence": evidence,
            }
        )
        print(f"  {status:8} {cid:30} {earned}/{crit['points']}  {evidence[:100]}")

    if not args.only:
        write_report(ctx, results, judged=args.judge)
        total = sum(r["earned"] for r in results)
        maxi = sum(r["points"] for r in results)
        note = "" if args.judge else " (brand-voice-judge skipped; run with --judge)"
        print(f"\nPRODUCT_EVAL.md written - {total}/{maxi} automated points{note}")
    return 1 if any(r["status"] == "FAIL" for r in results) else 0


def write_report(ctx: Ctx, results: list[dict], judged: bool) -> None:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    icon = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "PARTIAL": "🟡 PARTIAL", "SKIPPED": "⏭️ SKIPPED"}
    lines = [
        "# PRODUCT_EVAL.md",
        "",
        f"Produced by `eval/eval.py` on {now} against the running agency.",
        "Every result below is measured evidence (HTTP responses, database rows, git",
        "history) - never an agent's self-report.",
        "",
        f"- **Business under evaluation:** {ctx.tenant['business_name']} (`{ctx.slug}`)",
        f"- **Run:** `{ctx.run_id}`",
        f"- **Live site:** {ctx.live_url}",
        f"- **Agency:** {AGENCY_URL or 'local (gate :8082 / workers :8081 / gateway :8080)'}",
        "",
        "| Check | Area | Result | Points | Evidence |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        ev = r["evidence"].replace("|", "\\|")
        lines.append(
            f"| `{r['id']}` | {r['area']} | {icon[r['status']]} "
            f"| {r['earned']}/{r['points']} | {ev} |"
        )
    total = sum(r["earned"] for r in results)
    maxi = sum(r["points"] for r in results)
    lines += ["", "## Score by rubric area", "", "| Area | Points |", "|---|---|"]
    areas: dict[str, list[int]] = {}
    for r in results:
        areas.setdefault(r["area"], [0, 0])
        areas[r["area"]][0] += r["earned"]
        areas[r["area"]][1] += r["points"]
    for area, (e, m) in areas.items():
        lines.append(f"| {area} | {e}/{m} |")
    lines += [f"| **Total (automated)** | **{total}/{maxi}** |", ""]
    judge_note = (
        "ran via the gate's cost-logged /model_call."
        if judged
        else "was skipped this run (`--judge` to include)."
    )
    lines += [
        "## Not automated (grader / demo evidence)",
        "",
        "- The 60-90 s demo recording (README sec. 8) shows the full flow live, including",
        "  the brand-doc edit visibly changing regenerated output.",
        f"- `brand-voice-judge` {judge_note}",
        "- Aesthetic 'not slop' judgment beyond the deterministic + judge checks is the",
        "  grader's to make at the live URL.",
        "",
    ]
    (REPO_ROOT / "PRODUCT_EVAL.md").write_text("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
