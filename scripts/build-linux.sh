#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
  echo 'Build on Linux x86_64; this is not a cross-compiler.' >&2
  exit 1
fi
python3 -m pip install -r windows-build-requirements.txt
python3 scripts/build-version.py
python3 -m PyInstaller --noconfirm --clean --onefile --name FamilyTradingBot-linux-x86_64 \
  --add-data 'cloud/browser-config.json:cloud' --collect-all psycopg --collect-all psycopg_binary --collect-all psycopg_pool --collect-all tzdata --collect-data certifi \
  --add-data 'web:web' --add-data 'skills:skills' --add-data 'scanner.json:.' \
  --add-data 'stock_universe.json:.' --add-data 'tradingview_template.pine:.' \
  --add-data '.env.example:.' linux_launcher.py
python3 scripts/linux-manifest.py
