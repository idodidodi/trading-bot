#!/usr/bin/env bash
# Prepare an isolated Postgres installation; never start a worker implicitly.
set -euo pipefail
source_dir=$(cd -- "$(dirname -- "$0")/.." && pwd)
if [[ $# != 2 ]]; then
  echo 'Usage: scripts/install-ubuntu.sh /absolute/new-install /absolute/worker.env' >&2
  echo 'Guide: https://github.com/idodidodi/trading-bot/blob/main/POSTGRES_MIGRATION.md' >&2
  exit 2
fi
exec bash "$source_dir/scripts/prepare-postgres-install.sh" "$@"
