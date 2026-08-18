# EPYHIA — a one-person AI agency

[![CI/CD](https://github.com/Bhardwaj-Saurabh/EPYHIA_AI_agency/actions/workflows/deploy.yml/badge.svg)](https://github.com/Bhardwaj-Saurabh/EPYHIA_AI_agency/actions/workflows/deploy.yml)
[![Eval](https://img.shields.io/badge/product_eval-100%2F100-brightgreen)](PRODUCT_EVAL.md)
[![Deployed on Fly.io](https://img.shields.io/badge/deployed-Fly.io-8b5cf6)](https://epyhia-gateway.fly.dev)
[![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![React 18](https://img.shields.io/badge/react-18-61DAFB?logo=react&logoColor=black)](apps/dashboard)
[![Stripe test mode](https://img.shields.io/badge/stripe-test_mode_only-635BFF?logo=stripe&logoColor=white)](DESIGN.md)
[![Design-first](https://img.shields.io/badge/root_commit-DESIGN.md-blueviolet)](DESIGN.md)

![EPYHIA architecture overview](docs/EPYHIA.png)

**A business brief goes in. A real business comes out:** a live website on a real
URL, a grounded marketing pack with a launch video, and a working Stripe
checkout where a completed test purchase writes an order row to a real
database. Built, marketed, and ready to sell — by a crew of four AI agents that
can spend money and publish to the world, but only through one audited door.

**Scores [100/100](PRODUCT_EVAL.md) on its own evidence-based evaluation suite.**

| See it live | |
|---|---|
| 🐕 **A business it built** — The Biscuit Barn (pet boarding) | [epyhia-biscuit-barn.pages.dev](https://epyhia-biscuit-barn.pages.dev) — book a stay with Stripe test card `4242 4242 4242 4242`; the order really persists |
| 🚲 **Another one, same system, new brief** — Dales Wheels (bike hire) | [epyhia-harrogate-bike-hire.pages.dev](https://epyhia-harrogate-bike-hire.pages.dev) |
| 🎛️ **The agency itself** — Action Gate console | [epyhia-gateway.fly.dev](https://epyhia-gateway.fly.dev) — live runs, per-agent model costs, approvals, and the full audit log |

---

## Why

This is the capstone of a Forward Deployed Engineer track ([full assignment](docs/ASSIGNMENT.md)),
modeled on a real product category: autonomous "AI business builders." The
reference product's most-reported failures are instructive — tasks marked
"complete" that never deployed, outreach with wrong prices, duplicate charges,
and launched businesses that turned out to be cosmetic landing pages with no
way to buy anything.

**The moment an agent can ship and charge, "it looked fine in the demo" stops
being good enough.** EPYHIA is an answer to that: an agent system designed
around the assumption that agents lie, crash, and hallucinate — where every
claim is verified against reality and every irreversible action passes a human.
The full failure catalogue and its controls are in [DESIGN.md §12](DESIGN.md#12-failure-catalogue).

## What

Four agents behind a single **Action Gate** (three-tier isolation, all on Fly.io):

| Agent | Model tier | Job | May never |
|---|---|---|---|
| **Strategist** (orchestrator) | GPT-5.6 Sol | Brief → brand document + task plan, then delegates | Make any external call, hold any credential |
| **Web Builder** | Sol (drafts) + Terra (reviews) | Generates the site, requests deploys | Touch payments; deploy without a hash-bound human approval |
| **Marketer** | Terra | Landing copy, social posts, launch email, video storyboard | Publish outside sandbox; make a claim not grounded in the brief |
| **Ops** | Luna (cheapest) | Catalog extraction, checkout, order verification | Request live-mode actions; mark its own work verified |

The **Action Gate (Tier 3)** is the only process holding credentials — Stripe,
Cloudflare, Azure OpenAI, the database. Agents get capability handles, never
keys. Every side effect passes one pipeline:

```
capability check → human approval (if irreversible) → run budget
→ idempotency → audit row + cost log → execute → verify against reality
```

Network topology enforces the design: only Tier 1 has a public address —
Tier 2 (agents) and Tier 3 (gate) have **no public inbound at all** (Fly
private networking).

### Architecture (from [DESIGN.md](DESIGN.md), the repo's root commit)

```mermaid
flowchart TD
    Admin["Administrator<br/>(brief, reviews, approvals)"]
    Customer["Customer<br/>(generated site on Cloudflare Pages)"]
    StripeWH["Stripe webhooks"]

    subgraph T1["Tier 1 - Public web app (Fly.io, public ingress, no credentials)"]
        API["API Gateway<br/>admin dashboard · checkout API · webhook intake"]
    end

    subgraph T2["Tier 2 - Agent workers (private, scoped capabilities, no credentials)"]
        Runtime["Orchestration Runtime"]
        Strategist["Strategist<br/>(Sol 5.6)"]
        WebBuilder["Web Builder<br/>(Sol 5.6)"]
        Marketer["Marketer<br/>(Terra 5.6)"]
        Ops["Ops<br/>(Luna 5.6)"]
    end

    subgraph T3["Tier 3 - Action Gate (private, no public inbound, sole credential holder)"]
        Gate["capability check → approval check → run budget<br/>→ idempotency → audit + cost log<br/>/model_call · /deploy · /checkout-session · /video-render · /publish"]
    end

    subgraph EXT["External providers"]
        OpenAI["Azure OpenAI"]
        CF["Cloudflare Pages"]
        Stripe["Stripe (test mode)"]
        Neon["Neon DB"]
        R2["R2"]
    end

    Admin -->|brief + approvals| API
    Customer -->|checkout request| API
    StripeWH -->|"signed event (raw body forwarded unchanged)"| API

    API --> Runtime
    Strategist -->|delegates| WebBuilder
    Strategist -->|delegates| Marketer
    Strategist -->|delegates| Ops
    Strategist --- Runtime
    WebBuilder --- Runtime
    Marketer --- Runtime
    Ops --- Runtime
    Runtime -->|"/model_call for scoped agent"| Gate

    WebBuilder -->|"/deploy (admin-approved)"| Gate
    Marketer -->|"/video-render (admin-approved)"| Gate
    Ops -->|"/checkout-session · storage"| Gate

    CF -.->|"evidence: verified URL + HTTP 200"| Gate
    Neon -.->|"evidence: persisted order row"| Gate

    Gate --> OpenAI
    Gate --> CF
    Gate --> Stripe
    Gate --> Neon
    Gate --> R2
```

The dashed edges are the point: the gate collects **evidence from reality**
(a URL answering 200, an order row actually persisted) instead of trusting an
agent's self-report. The full data model, flows, idempotency scheme, and
failure catalogue are in [DESIGN.md](DESIGN.md) — written and committed
**before any code**, as the root commit proves (`git log --reverse`).

## How it works (one run)

1. **Brief in** — the administrator submits a plain-language brief with a spend
   budget (e.g. $2.00). A deterministic run-shell is created; replaying the
   same brief returns the same run forever.
2. **Strategist** (Sol) writes a versioned brand document — the crew's shared
   memory. The admin approves it, bound to its content hash.
3. **Ops** (Luna) extracts the bookable catalog into the database — integer
   pence, never floats.
4. **Web Builder** (Sol) generates a single-file site with a strict
   booking-form contract; deterministic grounding checks (exact prices, real
   contact, no invented testimonials) and an independent Terra design review
   gate it. The admin approves the deploy against the exact reviewed payload's
   hash.
5. **Go-live requires proof, not self-report:** the gate deploys via wrangler,
   checks the URL returns 200, then runs a **synthetic end-to-end purchase**
   through the real checkout, webhook, and database path. Only a persisted
   synthetic order row makes the deployment "verified."
6. **Marketer** (Terra) produces the content pack; every artifact passes a
   deterministic grounding check *and* an LLM self-review before it can be
   hash-approved. The launch video (landscape + vertical) is rendered
   deterministically inside the gate from the approved storyboard — brand
   palette in, MP4s in R2 out, no external video API.
7. **A customer pays:** the live site's booking form posts to the public
   gateway; totals are computed server-side; availability is locked with
   `SELECT FOR UPDATE`; Stripe (test mode, enforced) hosts the checkout; the
   webhook is signature-verified on the raw body, deduped by event id, amount-
   checked against the persisted reservation, and writes the order + confirmation
   in one transaction.
8. **Re-run everything — nothing duplicates.** Same run id, no second site, no
   second charge. Crash-and-retry is safe by construction (deterministic
   reservation ids, version-scoped deploy keys, unique constraints).

A full run costs about **$0.80–$1.40** in model spend, itemized per call by
agent, tier, and tokens — visible on the [dashboard](https://epyhia-gateway.fly.dev).

## Proof over promises

`eval/` is part of the deliverable: [rubric.json](eval/rubric.json) mirrors the
assignment's 100-point rubric; [eval.py](eval/eval.py) runs 17 evidence-based
checks against the *running* agency — HTTP responses, database rows, git
history, never an agent's status field — and writes [PRODUCT_EVAL.md](PRODUCT_EVAL.md).
The two decisive checks run first: a scripted purchase must persist exactly one
PAID order (webhook redelivery = no-op), and a replayed brief must create
nothing new. An LLM brand-voice judge (cheapest tier, cost-logged through the
gate like any other call, cached by content hash) scores the live page against
the brand document.

Current score: **100/100.**

## Build and run it yourself

Prerequisites: Python 3.12 + [uv](https://docs.astral.sh/uv/), Node 22+, and
free-tier accounts for Neon (Postgres), Cloudflare (Pages + R2), Stripe (test
mode), Azure OpenAI, and Fly.io (deploy only).

```bash
git clone https://github.com/Bhardwaj-Saurabh/EPYHIA_AI_agency.git && cd EPYHIA_AI_agency
uv sync --all-packages && npm install          # backend + wrangler + dashboard
cp .env.example .env                           # fill in your keys (documented per tier)
uv run python -m gate.migrate                  # apply db/migrations/*.sql

# three tiers, three terminals (gate 8082, workers 8081, gateway 8080)
uv run python -m gate.main
uv run python -m workers.main
uv run python -m gateway.main

# the whole story in one command: brief → brand → site → pack → video → paid order → replay
uv run python -m workers.demo_full --interactive

# measure it
uv run python eval/eval.py --judge

uv run pytest                                   # 30 integration tests (real DB)
```

Bring your own business: pass `--slug`, `--business`, and `--brief-file` to
`demo_full` (see [apps/workers/workers/briefs/](apps/workers/workers/briefs/)
for the shape). Same system every time — you're choosing the customer.

### Deploying (CI/CD)

Push to `main` and [GitHub Actions](.github/workflows/deploy.yml) lints, tests,
and deploys all three Fly apps (`fly/*.toml`). One-time setup: create the apps,
allocate private IPs for gate/workers, and import tier-scoped secrets — the
exact commands are in [PROGRESS.md](PROGRESS.md). Secrets live only in Fly
secrets and GitHub repo secrets; `.env` never enters git or a Docker image.

## Repository map

```
DESIGN.md            the human-written system design (the repo's root commit)
docs/ASSIGNMENT.md   the course assignment + grading rubric
apps/gate/           Tier 3 — Action Gate: pipeline, executors, /model_call, video renderer
apps/workers/        Tier 2 — Strategist, Web Builder, Marketer, Ops + demo scripts
apps/gateway/        Tier 1 — public API gateway + serves the admin dashboard
apps/dashboard/      React 18 + Vite + Tailwind admin console
db/migrations/       raw SQL schema (psycopg 3, no ORM)
eval/                rubric.json + eval.py → PRODUCT_EVAL.md
fly/                 per-app Fly.io configs; .github/workflows/ is the CI/CD pipeline
```

## What this project demonstrates

- **Agent systems that survive contact with money**: single credential holder,
  human-approved irreversibles, per-run budgets, integer money, test-mode
  enforced at the key level
- **Idempotency as a design principle** (crash + re-run → one site, one order),
  proven by automated checks, not asserted
- **Verification against reality**: deploys confirmed by HTTP + a synthetic
  purchase through the real payment path; the eval reads databases and live
  URLs, never agent self-reports
- **Cost-engineered LLM usage**: top-tier model only where reasoning pays,
  cheap tiers for drafting/review/judging, every call metered
- **Eval-driven development**: the rubric became an executable test suite;
  observed failures (reviewer context asymmetry, inclusive billing, webhook
  replay) became permanent regression checks

Built by **Saurabh Bhardwaj** as a design-first solo project: the architecture
in [DESIGN.md](DESIGN.md) came before any code, and every architectural
decision since originated with its author.
