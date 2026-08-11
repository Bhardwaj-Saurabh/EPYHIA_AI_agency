#!/usr/bin/env bash
# PreToolUse (Bash): enforce this repo's git discipline.
# - No bulk staging: protects the git-ignored-but-present internal file and
#   keeps every commit's contents deliberate.
# - No force pushes / amends: the root commit is graded evidence.

input=$(cat)
command=$(echo "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)
[ -z "$command" ] && exit 0

if echo "$command" | grep -Eq 'git\s+add\s+(-A|--all|\.($|\s))'; then
  echo "Blocked: stage files by explicit name (git add <file> ...), never -A/--all/. in this repo." >&2
  exit 2
fi

if echo "$command" | grep -Eq 'git\s+(add|commit|mv).*(ai-framework|delaware|di-ai)'; then
  echo "Blocked: that file is internal employer material and must never enter git." >&2
  exit 2
fi

if echo "$command" | grep -Eq 'git\s+push.*(--force|-f\b)|git\s+commit.*--amend'; then
  echo "Blocked: no amend/force-push in this repo - the commit history is graded evidence." >&2
  exit 2
fi

exit 0
