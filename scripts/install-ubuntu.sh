#!/usr/bin/env bash
set -euo pipefail
# Run as the ordinary account that will own the worker, from this checkout.
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [[ $(id -u) == 0 ]]; then echo 'Run as a regular user with sudo access.' >&2; exit 1; fi
command -v python3 >/dev/null || { echo 'Install Python 3.10+ first: sudo apt install python3' >&2; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ required"'
if [[ ! -f "$APP_DIR/.env" ]]; then cp "$APP_DIR/.env.example" "$APP_DIR/.env"; chmod 600 "$APP_DIR/.env"; echo "Created .env. Configure credentials and DRY_RUN, then run this installer again."; exit 0; fi
chmod 600 "$APP_DIR/.env"
mkdir -p "$APP_DIR/data"
# Quote systemd paths (including spaces); prevent specifier expansion.
if [[ "$APP_DIR" == *'%'* || "$APP_DIR" == *'"'* || "$APP_DIR" == *$'\n'* ]]; then echo 'Choose a checkout path without %, quotes or newlines.' >&2; exit 1; fi
SERVICE_USER="$(id -un)"
TASK_UNIT="$(mktemp)"
trap 'rm -f "$TASK_UNIT"' EXIT
cat > "$TASK_UNIT" <<EOF
[Unit]
Description=Trading signal scanner and local dashboard
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory="$APP_DIR"
ExecStart=/usr/bin/python3 "$APP_DIR/platform_app.py"
Restart=on-failure
RestartSec=15
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=HOST=127.0.0.1
NoNewPrivileges=true
PrivateTmp=true
CPUWeight=20
MemoryHigh=192M
MemoryMax=384M
TasksMax=32
TimeoutStopSec=60
[Install]
WantedBy=multi-user.target
EOF
sudo install -m 644 "$TASK_UNIT" /etc/systemd/system/trading-bot.service
sudo systemctl daemon-reload
sudo systemctl enable trading-bot.service
sudo systemctl restart trading-bot.service
sudo systemctl --no-pager status trading-bot.service
