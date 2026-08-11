---
description: Run the eval suite, compare with the last run, and pick the highest-value next fix (eval-driven development loop)
argument-hint: [optional: --only <check-name>]
---

Drive one turn of the eval-driven development loop
(docs/eval-driven-development.png).

1. If `eval/` does not exist yet, delegate to the **eval-engineer** agent to
   scaffold it: `rubric.json` mirroring README.md section 7's rubric rows and
   points, and `eval.py` with the two decisive checks implemented first
   (one order persists per purchase; re-run creates no duplicates) and the
   rest as explicit TODOs. Then stop and report what was scaffolded.
2. If it exists: run `python eval/eval.py $ARGUMENTS` (or the project's
   documented invocation). If the agency isn't running/deployed, report that
   instead of faking results.
3. Compare the new PRODUCT_EVAL.md against the previous one (git diff is
   enough). Report: checks that flipped pass/fail, current point total, and
   the trend.
4. Recommend exactly ONE next action: the failing check worth the most points
   that is cheapest to fix. Explain in two sentences why it is the best
   value. Do not start fixing it without the user's go-ahead.
5. If a new failure mode was observed anywhere this session that no check
   covers, note it as a candidate new check (refine-evals loop) - suggest it,
   don't silently add it.
