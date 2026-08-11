#!/usr/bin/env bash
# Deterministic scan for EPYHIA invariant violations in application code.
# Exits 0 with a report; findings are advisory (the code reviewer judges them).
cd "$(dirname "$0")/../.." || exit 1

APP_DIRS=""
for d in apps services src; do
  [ -d "$d" ] && APP_DIRS="$APP_DIRS $d"
done

if [ -z "$APP_DIRS" ]; then
  echo "No application code directories found yet (looked for: apps/ services/ src/)."
  exit 0
fi

INC="--include=*.py --include=*.ts --include=*.tsx --include=*.js"

echo "Scanning:$APP_DIRS"
echo

echo "== Provider usage outside the gate (should ONLY appear under apps/gate) =="
grep -rn $INC -E "import stripe|from stripe|require\(['\"]stripe['\"]\)|openai\.azure\.com|api\.openai\.com|api\.cloudflare\.com|AZURE_OPENAI_API_KEY|CLOUDFLARE_API_TOKEN|STRIPE_SECRET_KEY" $APP_DIRS 2>/dev/null | grep -v 'apps/gate/' || echo "  none found"
echo

echo "== Key-shaped strings in code (should be none; keys live in Fly secrets/.env) =="
grep -rn $INC --include='*.json' --include='*.toml' -E 'sk_(test|live)_[A-Za-z0-9]{8,}|whsec_[A-Za-z0-9]{8,}|npg_[A-Za-z0-9]{8,}' $APP_DIRS 2>/dev/null || echo "  none found"
echo

echo "== Float money math (amounts must be integer cents/microdollars) =="
grep -rn $INC -E '(amount|price|total|cost)[A-Za-z_]*\s*[*/]\s*[0-9]*\.[0-9]|parseFloat\((amount|price|total|cost)|float\((amount|price|total|cost)' $APP_DIRS 2>/dev/null || echo "  none found"
echo

echo "== Stripe webhook handlers missing signature verification =="
files=$(grep -rl $INC 'checkout.session.completed\|checkout.session.expired' $APP_DIRS 2>/dev/null)
if [ -n "$files" ]; then
  for f in $files; do
    grep -q 'construct_event\|constructEvent\|verifySignature\|stripe-signature' "$f" || echo "  $f handles webhook events but shows no signature verification"
  done
  echo "  (checked: $files)"
else
  echo "  no webhook handlers found yet"
fi
echo

echo "== git hygiene =="
git status --short | grep -E 'ai-framework|delaware' && echo "  WARNING: internal file appears in git status above" || echo "  internal file not staged - ok"
