# Progress

One-page state of the build. Sessions resume from here instead of re-reading
the specs. Update when a milestone lands or a decision is made; keep it short.

## Where we are

- **Phase:** customer loop CLOSED — the live site sells (booking form → 
  /api/checkout → Stripe → order row), and go-live verification requires the
  synthetic end-to-end purchase (failure catalogue #8 control, live).
  Remaining: admin dashboard (React SPA on Tier 1), Veo video render
  (GOOGLE_AI_API_KEY still empty), Fly deploy of the agency (+ real Stripe
  webhook endpoint + GATEWAY_PUBLIC_URL update + site redeploy), eval/ +
  PRODUCT_EVAL.md, demo recording.
- **How to run:** `uv sync --all-packages` + `npm install` (wrangler), then
  `uv run python -m gate.main` / `-m workers.main` / `-m gateway.main`.
  Health: `GET :8082|:8081|:8080/health/live` and `/health/ready`.
  Migrations: `uv run python -m gate.migrate`. Tests: `uv run pytest` (hits
  the real Neon dev DB). Lint: `uv run ruff check apps/`. Demos:
  `uv run python -m gate.demo` / `-m workers.demo` (services must be running).

## Done

- 2026-08-10 — DESIGN.md committed as root commit (party rentals, three-tier
  architecture, Action Gate as sole credential holder). Pushed to
  github.com/Bhardwaj-Saurabh/EPYHIA_AI_agency.
- 2026-08-10 — Review pass applied: explicit agent prohibitions, synthetic
  go-live test purchase, sourced failure catalogue, "Proving it" section.
- 2026-08-11 — Provider set to Azure OpenAI / Microsoft Foundry (GPT-5.6
  Sol/Terra/Luna, same prices). Decision: no agent framework — thin loops
  against the gate's OpenAI-compatible /model_call.
- 2026-08-11 — Frontend decision: React 18 + Vite + Tailwind + shadcn/ui SPA,
  served as static assets by the Tier 1 Node gateway on Fly. Not Next.js.
- 2026-08-11 — Claude Code harness in place: agents (react-frontend-expert,
  node-backend-expert, code-reviewer, eval-engineer), commands (/track, /eval,
  /invariants), guard hooks, formatter.
- 2026-08-11 — Monorepo scaffold: npm workspaces (apps/gate, apps/workers,
  apps/gateway), Node 22 + TS strict + Express, health endpoints boot-tested,
  .env.example documents every variable by owning tier, db/migrations/001_init.sql
  translates DESIGN.md §8 (all unique constraints in place). Typecheck green.
- 2026-08-11 — **Action Gate milestone**: pipeline (capability → approval →
  budget → idempotency → audit + cost log → execute) live against Neon; deploy
  executor pushes to Cloudflare Pages via wrangler and independently verifies
  HTTP 200 before marking executed; hash-bound approvals; 7 vitest invariant
  tests green; end-to-end demo proved for real (pending → approve → live URL
  https://epyhia-gate-check-35951d.pages.dev → 200 → replay: same row, no
  duplicate deploy). Credentials exist only in gate env.
- 2026-08-11 — **Strategist milestone**: gate /model_call (Azure OpenAI v1
  chat completions, tier-per-agent enforcement, per-run budget cap, every call
  cost-logged to agent_calls in microdollars); run_shell + business_storage
  executors (Tier 2 has no DB — all persistence is gated); workers app runs
  the Strategist (Sol, JSON contract, honesty rules, clarification loop for
  incomplete briefs, Ops delegated for persistence). Real end-to-end demo:
  BrightSide Party Rentals brief → run 202 → real Sol call → brand doc v1
  (5.8k chars, grounded, no invented facts) + 4 tasks BLOCKED_ON_BRAND_APPROVAL
  → spent $0.056 of $2.00 budget → replay returned same run, no second call.
- 2026-08-11 — **Backend ported TypeScript → Python** (user decision; DESIGN.md
  amended first). uv workspace, FastAPI + psycopg + httpx; same pipeline, same
  DB, same endpoints. Canonical payload hashing is byte-compatible: the Python
  gate replayed TS-era audit rows (same idempotency keys recognized, 409 on
  changed payloads), all 7 invariant tests re-pass via pytest, ruff clean, and
  a fresh Luna call through the Python gate hit Azure for real (11 µ$ logged).
  Wrangler remains the one Node dependency (gate shells out for Pages deploys).
- 2026-08-11 — **Web Builder milestone**: brand approval endpoint (bound to
  brand_document content_hash, migration 002; unblocks downstream tasks);
  Ops/Luna catalog extraction into rental_items (idempotent wholesale replace,
  guarded once reservations exist); Web Builder loop per DESIGN §11 — Sol
  generates, deterministic grounding checks (prices/items/contact/fabrication
  tripwires, unit-tested), independent Terra review, revision rounds revise
  the previous HTML (max 3); site stored on the task so the admin approves the
  exact reviewed payload; deploy hash-approved at the gate → REAL site:
  https://epyhia-gate-demo.pages.dev (verified 200, all 5 real prices on
  page). Re-approval + replay changed nothing. Run spend: $0.95 of $2.00.
  Lesson logged: first attempt burned 3 Sol rounds because the reviewer had no
  calibration and regeneration started from scratch — fixed by revise-not-
  regenerate + reject-only-for-nameable-violations prompt (1 round after fix).
- 2026-08-11 — **Marketer milestone**: content pack on Terra (landing copy,
  4 channel-appropriate posts, launch email, 6-8 shot video storyboard), each
  artifact passing a deterministic grounding check (only catalog prices, only
  the real email, fabrication tripwires incl. delivery) AND a self-review
  before storage; pack approval bound to the complete pack hash at the gate
  (tamper test: wrong hash → 409). 7 artifacts approved; MARKETING_PACK →
  AWAITING_VIDEO_RENDER (honest: Veo is a separate paid approval, key not yet
  provided). Run spend: $1.12 of $2.00. Lessons: the self-review caught a real
  fabrication my own prompt had planted ("announcing to people who asked to be
  told" → invented subscriber relationship), and two false-positive rounds
  came from context asymmetry — the reviewer must receive the same ground
  truth (brief, live URL) as the generator. Also: task-status transitions now
  use attempt-scoped idempotency keys so retries re-mark status.
- 2026-08-11 — **Checkout milestone** (Flows 2+3, full three-tier chain):
  browser sends items/dates/customer only — totals computed server-side in
  pence from rental_items; availability via SELECT FOR UPDATE + date-overlap
  (double-booking = DB-impossible); reservation id deterministic from action
  id (crash-retry can't double-hold stock); REAL Stripe test session created
  (gate refuses non-sk_test_ keys — caught the publishable key mix-up);
  webhook raw-body passthrough gateway→workers→gate, signature verified at
  the gate, deduped by event id + already-confirmed guard, amount/currency
  compared to the persisted reservation, order + confirm in one transaction.
  Live demo: £132.00 order row PAID in Neon; redelivery → duplicate no-op;
  double-click → same reservation; tampered signature → 400. 7 pytest
  invariants cover it (24 tests total). STRIPE_WEBHOOK_SECRET uses a local
  dev value — replace when the real Stripe webhook endpoint is created on
  the deployed gateway.
- 2026-08-11 — **Site v2 + synthetic go-live check**: the generated site now
  carries a booking form under a strict contract (qty inputs with
  data-item-id per catalog item, date/name/email fields, vanilla-JS POST to
  GATEWAY_PUBLIC_URL/api/checkout, NO client-side totals) enforced by
  deterministic checks before review; gateway gained CORS for *.pages.dev.
  Deploy verification is now two-step: HTTP 200 AND a synthetic end-to-end
  purchase (real Stripe test session + signed completed event through the
  full webhook path) persisting a synthetic-flagged PAID order — verified
  live: 1 synthetic order (150p), real orders untouched. Deploy/site
  idempotency keys are version-scoped (new site version = new audited
  action; same version still replays). Run spend: $1.40 of $2.00.
  Note for eval: pages.dev serves the previous version for ~30-60s after
  deploy — content checks must cache-bust or retry (the gate's own checks
  are API-path based and unaffected).

## Blocked / owed decisions

- Accounts + keys needed before gate work is testable end-to-end: Fly.io,
  Neon, Cloudflare (Pages), Stripe (test), Auth0, Azure OpenAI. Veo can wait
  until week 2.

## Standing constraints (do not relitigate)

- Root commit is never amended; no force pushes.
- ai-framework-security-blog_6.md never enters git.
- Architecture decisions originate with the user; DESIGN.md is the spec.
