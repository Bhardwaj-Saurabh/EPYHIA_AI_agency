# Progress

One-page state of the build. Sessions resume from here instead of re-reading
the specs. Update when a milestone lands or a decision is made; keep it short.

## Where we are

- **Phase:** Action Gate milestone DONE (README.md §6, item 2). Next: the
  Strategist (brief → brand doc + task list, via gate /model_call) — item 3.
- **How to run:** `npm install`, then `npm run dev:gate` / `dev:workers` /
  `dev:gateway`. Health: `GET :8082|:8081|:8080/health/live` and `/health/ready`.
  Migrations: `npx tsx src/migrate.ts` from apps/gate. Tests: `npx vitest run`
  from root (pipeline tests hit the real Neon dev DB). Gate demo:
  `npx tsx src/demo.ts` from apps/gate (gate must be running).
  `npm run typecheck` must stay green.

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

## Blocked / owed decisions

- Accounts + keys needed before gate work is testable end-to-end: Fly.io,
  Neon, Cloudflare (Pages), Stripe (test), Auth0, Azure OpenAI. Veo can wait
  until week 2.

## Standing constraints (do not relitigate)

- Root commit is never amended; no force pushes.
- ai-framework-security-blog_6.md never enters git.
- Architecture decisions originate with the user; DESIGN.md is the spec.
