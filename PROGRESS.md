# Progress

One-page state of the build. Sessions resume from here instead of re-reading
the specs. Update when a milestone lands or a decision is made; keep it short.

## Where we are

- **Phase:** backend ported to Python (user decision 2026-08-11); Strategist
  milestone re-verified in Python. Next: the Web Builder (generate the site
  from the approved brand doc, deploy through the gate) — item 4. Brand
  approval flow (dashboard) becomes needed soon: runs stop at
  AWAITING_BRAND_APPROVAL by design.
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

## Blocked / owed decisions

- Accounts + keys needed before gate work is testable end-to-end: Fly.io,
  Neon, Cloudflare (Pages), Stripe (test), Auth0, Azure OpenAI. Veo can wait
  until week 2.

## Standing constraints (do not relitigate)

- Root commit is never amended; no force pushes.
- ai-framework-security-blog_6.md never enters git.
- Architecture decisions originate with the user; DESIGN.md is the spec.
