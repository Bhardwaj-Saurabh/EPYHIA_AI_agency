---
description: Scan application code for EPYHIA invariant violations (gate bypass, secrets, float money, unverified webhooks)
allowed-tools: Bash(.claude/scripts/invariants.sh), Read, Grep
---

Run `.claude/scripts/invariants.sh` and interpret its report.

For each finding, read the file at the flagged line and judge whether it is a
real violation of DESIGN.md's invariants (gate is sole credential holder;
integer money; verified webhooks; no secrets in code) or a false positive from
the grep. Report real violations with severity and the concrete fix; dismiss
false positives with one line of reasoning each. If the scan finds nothing and
code exists, say so briefly - do not invent findings.
