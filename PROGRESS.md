# Progress

One-page state of the build. Sessions resume from here instead of re-reading
the specs. Update when a milestone lands or a decision is made; keep it short.

## Where we are

- **Phase:** design complete, implementation not started.
- **Next milestone:** the Action Gate (Tier 3) with one trivial gated action
  (a test deploy): capability check, approval, idempotency, audit row, cost
  log — README.md §6, item 2.

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

## Blocked / owed decisions

- Accounts + keys needed before gate work is testable end-to-end: Fly.io,
  Neon, Cloudflare (Pages), Stripe (test), Auth0, Azure OpenAI. Veo can wait
  until week 2.

## Standing constraints (do not relitigate)

- Root commit is never amended; no force pushes.
- ai-framework-security-blog_6.md never enters git.
- Architecture decisions originate with the user; DESIGN.md is the spec.
