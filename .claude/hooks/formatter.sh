#!/usr/bin/env bash
# PostToolUse formatter: formats the file Claude just wrote/edited.
# Silently no-ops if the formatter isn't installed yet.

input_json=$(cat)
file_path=$(echo "$input_json" | jq -r '.tool_input.file_path // empty' 2>/dev/null)

[ -z "$file_path" ] && exit 0
[ -f "$file_path" ] || exit 0

case "$file_path" in
  *.ts|*.tsx|*.js|*.jsx|*.css|*.json|*.html)
    npx --no-install prettier --write "$file_path" >/dev/null 2>&1
    ;;
  *.py)
    if command -v ruff >/dev/null 2>&1; then
      ruff check --fix "$file_path" >/dev/null 2>&1
      ruff format "$file_path" >/dev/null 2>&1
    fi
    ;;
esac

exit 0
