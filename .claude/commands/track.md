---
description: Report where the build stands against the assignment's build order, and flag drift
allowed-tools: Bash(git log:*), Bash(git status:*), Bash(ls:*), Bash(find:*), Read, Grep
---

Establish where the project stands and what comes next. Do NOT re-read
README-sample.md or the full README.md for this - the build order is
reproduced below.

Build order (README.md section 6):
1. DESIGN.md committed first (done - root commit)
2. Action Gate with one trivial gated action: approval, idempotency, audit row, cost log
3. Strategist: brief -> brand doc + task list, persisted
4. Web Builder: generate a site and deploy it through the gate
5. Week-1 demo: brief in -> live URL -> audit row visible
6. Marketer: content pack + self-review, grounded in the brand doc
7. Ops: Stripe test checkout; completed purchase writes an order row
8. Approval on go-live + charge; idempotency on re-run and crash
9. Deploy the agency itself to Fly.io
10. Final demo + eval/ + PRODUCT_EVAL.md

Steps:
1. Check `git log --oneline` and the repo's directory layout ("PROGRESS.md" at
   the root, if present, is the fast path - trust it unless the repo state
   contradicts it).
2. Read PROGRESS.md if it exists; skim only what is needed to verify.
3. Report: what is DONE (with evidence - commits, files), what is IN PROGRESS,
   the single NEXT milestone, and anything BLOCKED (missing accounts, keys,
   decisions the user owes).
4. Flag drift: work present in the repo that is not on the build order, or
   build-order items being done out of sequence. Drift is not automatically
   wrong - name it and ask.
5. Keep the whole report under ~20 lines.

If PROGRESS.md is stale relative to reality, update it (short bullets, dates)
after reporting.
