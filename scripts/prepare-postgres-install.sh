#!/usr/bin/env bash
# PREPARE ONLY: never modifies an existing installation or starts a service.
set -euo pipefail
source_dir=$(cd -- "$(dirname -- "$0")/.." && pwd)
new_install=${1:?Usage: prepare-postgres-install.sh /absolute/new-install /absolute/new-env-file}
new_env=${2:?Supply a separate existing environment file}
[[ "$new_install" == /* && "$new_env" == /* ]] || { echo 'Use absolute paths' >&2; exit 1; }
[[ ! -e "$new_install" && -f "$new_env" ]] || { echo 'Destination must be NEW and environment file must exist' >&2; exit 1; }
[[ "$new_env" != "$source_dir/.env" ]] || { echo 'Use a separate configuration file' >&2; exit 1; }
mkdir -m 700 -- "$new_install"
# Explicit source contents; exclude private state, checkouts, tests and snapshots.
python3 - "$source_dir" "$new_install" <<'PY'
from pathlib import Path
import shutil,sys
src,dest=map(Path,sys.argv[1:])
for path in src.glob('*.py'):
    if not path.name.startswith(('test_','postgres_test_')):shutil.copy2(path,dest/path.name)
for name in ('web','skills','cloud'):
    if name=='cloud':
        (dest/name).mkdir();shutil.copy2(src/name/'browser-config.json',dest/name/'browser-config.json')
    else:shutil.copytree(src/name,dest/name)
for name in ('scanner.json','stock_universe.json','tradingview_template.pine','requirements-runtime.txt','.env.postgres.example','POSTGRES_MIGRATION.md'):
    shutil.copy2(src/name,dest/name)
PY
python3 -m venv "$new_install/.venv"
"$new_install/.venv/bin/pip" install -r "$new_install/requirements-runtime.txt"
chmod 600 -- "$new_env"
echo "Prepared separate installation at $new_install. No service was installed or started."
echo 'Use the manual runbook for schema/import, activation, and service cutover.'
