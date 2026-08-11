# PRODUCT_EVAL.md

Produced by `eval/eval.py` on 2026-08-11 15:24 UTC against the running agency.
Every result below is measured evidence (HTTP responses, database rows, git
history) - never an agent's self-report.

- **Business under evaluation:** The Biscuit Barn (`biscuit-barn`)
- **Run:** `ad9da5f1-dc28-4db5-8a38-32342b0eb063`
- **Live site:** https://epyhia-biscuit-barn.pages.dev
- **Agency:** local (gate :8082 / workers :8081 / gateway :8080)

| Check | Area | Result | Points | Evidence |
|---|---|---|---|---|
| `order-persists` | The three deliverables are real | ✅ PASS | 10/10 | purchase 06141982-23c0-580c-9961-f3a93e5bb3cd: 5400p PAID persisted (webhook received=True); redelivery duplicate=true, still exactly 1 row |
| `no-duplicates` | The Action Gate | ✅ PASS | 5/5 | same run ad9da5f1-dc28-4db5-8a38-32342b0eb063 returned (replayed=true); deploy actions 2 and orders 4 unchanged |
| `live-site` | The three deliverables are real | ✅ PASS | 10/10 | https://epyhia-biscuit-barn.pages.dev HTTP 200; all 4 items with exact prices; booking form contract intact; gate-verified at 2026-08-11 14:56 UTC |
| `marketing-pack` | The three deliverables are real | ✅ PASS | 6/6 | 7 artifacts (landing copy, 4 posts, launch email, storyboard), all grounded against catalog+contact, all approved by 'saurabh' |
| `launch-video` | The three deliverables are real | ❌ FAIL | 0/4 | no rendered video artifacts - MARKETING_PACK honestly stops at AWAITING_VIDEO_RENDER (paid render is an approval-gated action; key not provided) |
| `not-slop` | Not slop | ✅ PASS | 8/8 | no filler, no fabrications; design markers all present: ['design system (:root custom properties)', 'fluid type (clamp)', 'colour work (gradient/color-mix)', 'motion behind prefers-reduced-motion', 'substantial styling (>3KB inline CSS)'] |
| `brand-voice-judge` | Not slop | ✅ PASS | 7/7 | score 9/10 (luna tier, 638 microdollars, cost-logged through the gate) - The page closely reflects the brand’s warm, reassuring, practical positioning with accurate local, service, pricing, booking, food, payment, and transport details, while its specific offerings and pet-focused language make it feel substantially more tailored than a generic AI template. |
| `tier-split-cost` | The crew & orchestration | ✅ PASS | 5/5 | 18 calls, every one cost-logged; tiers used ['luna', 'sol', 'terra']; spend by tier (microdollars) {'luna': 1016, 'sol': 1783997, 'terra': 167646}; total 1952659 |
| `strategist-delegates` | The crew & orchestration | ✅ PASS | 5/5 | zero action rows by 'strategist' (its only footprint is 1 model calls); all side effects requested by specialists/system |
| `brand-doc` | The crew & orchestration | ✅ PASS | 5/5 | v1 hash-approved by 'saurabh'; all pack artifacts reference the same brand document (1 version). Edit-changes-behavior is shown live in the demo recording |
| `sole-credential-holder` | The Action Gate | ✅ PASS | 5/5 | Tier 1/2 source reads no provider secrets from the environment; .env untracked; no key-shaped strings in any tracked file |
| `approval-before-irreversible` | The Action Gate | ✅ PASS | 5/5 | 2 executed irreversible actions (['deploy']), every one carries approved_by + approved_at <= executed_at |
| `audit-cost` | The Action Gate | ✅ PASS | 5/5 | 19 audit rows, all mode=TEST with payload hash + idempotency key; run spend 1952659 of 2000000 microdollars ($1.95 of $2.00) |
| `design-first` | Design & failure catalogue | ✅ PASS | 5/5 | root commit 41a55159 contains exactly one file: DESIGN.md (no code) |
| `failure-catalogue` | Design & failure catalogue | ✅ PASS | 5/5 | 8 failure modes, each stated with its control: ['Tenant paid for business creation but got no deployed website.', "Marketing copy doesn't match the tenant's wishes — off-brand or inaccurate claims.", 'Business customer gets double charged on crash or retry.']... |
| `env-example` | Ships & runs from clean clone | ✅ PASS | 4/4 | all 14 environment variables read anywhere in apps/ are documented in .env.example |
| `agency-deployed` | Ships & runs from clean clone | 🟡 PARTIAL | 2/6 | all three tiers healthy locally (gate/workers/gateway); Fly deploy pending - set EVAL_AGENCY_URL once deployed for full credit |

## Score by rubric area

| Area | Points |
|---|---|
| The three deliverables are real | 26/30 |
| The Action Gate | 20/20 |
| Not slop | 15/15 |
| The crew & orchestration | 15/15 |
| Design & failure catalogue | 10/10 |
| Ships & runs from clean clone | 6/10 |
| **Total (automated)** | **92/100** |

## Not automated (grader / demo evidence)

- The 60-90 s demo recording (README sec. 8) shows the full flow live, including
  the brand-doc edit visibly changing regenerated output.
- `brand-voice-judge` ran via the gate's cost-logged /model_call.
- Aesthetic 'not slop' judgment beyond the deterministic + judge checks is the
  grader's to make at the live URL.
