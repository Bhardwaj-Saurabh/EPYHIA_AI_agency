#!/usr/bin/env bash
# PreToolUse (Write|Edit): protect course-provided files and block key-shaped
# secrets from landing in tracked files.

input=$(cat)
file_path=$(echo "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
[ -z "$file_path" ] && exit 0

base=$(basename "$file_path")

case "$base" in
  README.md|README-sample.md|AGENTS.md)
    # Only protect the course files at the repo root, not app-level readmes.
    dir=$(cd "$(dirname "$file_path")" 2>/dev/null && pwd)
    if [ "$dir" = "$CLAUDE_PROJECT_DIR" ]; then
      echo "Blocked: $base is course-provided and must not be modified." >&2
      exit 2
    fi
    ;;
  ai-framework-security-blog_6.md)
    echo "Blocked: internal employer material - do not modify or copy it." >&2
    exit 2
    ;;
esac

# Secret scan on content being written (skip .env files, which are git-ignored).
case "$base" in
  .env|.env.*) exit 0 ;;
esac

content=$(echo "$input" | jq -r '(.tool_input.content // "") + " " + (.tool_input.new_string // "")' 2>/dev/null)
if echo "$content" | grep -Eq 'sk_live_[A-Za-z0-9]|whsec_[A-Za-z0-9]{10,}|sk_test_[A-Za-z0-9]{10,}'; then
  echo "Blocked: that content contains a key-shaped string. Keys live only in .env / Fly secrets; use placeholders in code and .env.example." >&2
  exit 2
fi

exit 0
