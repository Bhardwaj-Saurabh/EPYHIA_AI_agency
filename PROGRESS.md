# Progress

One-page state of the build. Sessions resume from here instead of re-reading
the specs. Update when a milestone lands or a decision is made; keep it short.

## Where we are

- **Phase:** scaffold in place, gate implementation next.
- **Next milestone:** the Action Gate pipeline (capability check → approval →
  budget → idempotency → audit + cost log) with one trivial gated action
  (a Cloudflare Pages test deploy) — README.md §6, item 2.
- **How to run:** `npm install`, then `npm run dev:gate` / `dev:workers` /
  `dev:gateway`. Health: `GET :8082|:8081|:8080/health/live` and `/health/ready`.
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

## Blocked / owed decisions

- Accounts + keys needed before gate work is testable end-to-end: Fly.io,
  Neon, Cloudflare (Pages), Stripe (test), Auth0, Azure OpenAI. Veo can wait
  until week 2.

## Standing constraints (do not relitigate)

- Root commit is never amended; no force pushes.
- ai-framework-security-blog_6.md never enters git.
- Architecture decisions originate with the user; DESIGN.md is the spec.
