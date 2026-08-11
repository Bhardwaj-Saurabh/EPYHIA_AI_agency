#!/usr/bin/env bash
# Deterministic scan for EPYHIA invariant violations in application code.
# Exits 0 with a report; findings are advisory (the code reviewer judges them).
cd "$(dirname "$0")/../.." || exit 1

APP_DIRS=""
for d in apps services src tier1 tier2 tier3 gate; do
  [ -d "$d" ] && APP_DIRS="$APP_DIRS $d"
done

if [ -z "$APP_DIRS" ]; then
  echo "No application code directories found yet (looked for: apps/ services/ src/ tier1/ tier2/ tier3/ gate/)."
  exit 0
fi

echo "Scanning:$APP_DIRS"
echo

echo "== Provider usage outside the gate (should ONLY appear under the Tier 3 / gate app) =="
grep -rn --include='*.ts' --include='*.js' -E "from ['\"]stripe['\"]|require\(['\"]stripe['\"]\)|openai\.azure\.com|api\.openai\.com|api\.cloudflare\.com|generativelanguage|veo" $APP_DIRS | grep -viv 'gate\|tier3' || echo "  none found"
echo

echo "== Key-shaped strings in code (should be none; keys live in Fly secrets/.env) =="
grep -rn --include='*.ts' --include='*.js' --include='*.json' --include='*.toml' -E 'sk_(test|live)_[A-Za-z0-9]{8,}|whsec_[A-Za-z0-9]{8,}' $APP_DIRS || echo "  none found"
echo

echo "== Float money math (amounts must be integer cents/microdollars) =="
grep -rn --include='*.ts' --include='*.js' -E '(amount|price|total|cost)[A-Za-z_]*\s*[*/]\s*[0-9]*\.[0-9]|parseFloat\((amount|price|total|cost)' $APP_DIRS || echo "  none found"
echo

echo "== Stripe webhook handlers missing signature verification =="
files=$(grep -rl --include='*.ts' --include='*.js' 'checkout.session.completed\|checkout.session.expired' $APP_DIRS 2>/dev/null)
if [ -n "$files" ]; then
  for f in $files; do
    grep -q 'constructEvent\|verifySignature\|stripe-signature' "$f" || echo "  $f handles webhook events but shows no signature verification"
  done
  [ -n "$files" ] && echo "  (checked: $files)"
else
  echo "  no webhook handlers found yet"
fi
echo

echo "== git hygiene =="
git status --short | grep -E 'ai-framework|delaware' && echo "  WARNING: internal file appears in git status above" || echo "  internal file not staged - ok"
