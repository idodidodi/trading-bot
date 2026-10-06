"""Upload existing dashboard data without starting scanner or Telegram delivery."""
import json
import os
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from platform_app import Store, load_env
from cloud_sync import apply, batch, meta, snapshot


def main():
    load_env()
    url = os.environ.get("CLOUD_SYNC_URL", "")
    token = os.environ.get("CLOUD_SYNC_TOKEN", "")
    if not url.startswith("https://") or not token:
        raise SystemExit("Configure the private cloud sync connection in local .env first.")
    store = Store(Path(os.environ.get("DATA_DIR", str(ROOT / "data"))) / "signals.sqlite3")
    total = 0
    snapshot(store)
    while True:
        outgoing = batch(store)
        if not outgoing["records"]:
            with store.connect() as db:
                latest = db.execute("SELECT coalesce(max(id),0) FROM activity_log").fetchone()[0]
                captured = int(meta(db, "log_snapshot"))
            if latest > captured:
                snapshot(store)
                continue
            break
        request = urllib.request.Request(url, data=json.dumps(outgoing).encode(), headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.load(response)
        apply(store, result)
        if result.get("conflicts"):
            raise SystemExit("Sync conflict retained locally; review before continuing.")
        acknowledged = len(result.get("ack", []))
        if not acknowledged:
            raise SystemExit("No records acknowledged; pending data retained.")
        total += acknowledged
        if total % 2000 < acknowledged:
            print(f"Acknowledged {total} records", flush=True)
    print(f"Initial sync complete: {total} records acknowledged; queue drained.", flush=True)


if __name__ == "__main__":
    main()
