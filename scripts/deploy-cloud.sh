#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v supabase >/dev/null || { echo 'Install the official Supabase CLI first.' >&2; exit 1; }
PROJECT_REF=bwejkkboophbfeiwizbu
python3 scripts/configure-cloud.py
# Install the function into the CLI project layout.
mkdir -p supabase/functions
cp -R cloud/functions/backtests supabase/functions/
# The handler verifies current ECC user JWTs with Supabase Auth and checks ownership.
supabase functions deploy backtests --project-ref "$PROJECT_REF" --no-verify-jwt
node cloud/build.mjs
printf '%s\n' 'Backtests function deployed. Publish dist/ through the existing Cloudflare build.'
