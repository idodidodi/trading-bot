"""Preview/apply NEAR RSI settings; sends no messages and starts no scanner.

Apply only after the matching worker and SQL prerequisites are deployed.
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FRAMES = ['5m', '15m', '30m', '1h', '4h', 'daily', 'weekly']
CHAT = '-5412729128'
ASSET = 'NEARUSD'


class Database:
    """Use the authoritative runtime DB; no Supabase management dependency."""
    def __init__(self):
        self.dsn = os.environ.get('TRADING_CONFIG_DATABASE_URL') or os.environ.get('TRADING_DATABASE_URL')
        if not self.dsn:
            raise RuntimeError('Configure TRADING_CONFIG_DATABASE_URL for the runtime database')
        self.installation = self.readonly('SELECT installation FROM bot_runtime.runtime_control WHERE id=1')[0]['installation']

    @staticmethod
    def literal(value):
        return "'" + str(value).replace("'", "''") + "'"

    def connect(self):
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def query(self, sql):
        with self.connect() as db:
            db.execute(sql, prepare=False)

    def readonly(self, sql):
        with self.connect() as db:
            db.execute("SET TRANSACTION READ ONLY")
            db.execute("SET LOCAL statement_timeout='15s'")
            return db.execute(sql).fetchall()


def configure(config):
    from managed_assets import validate_asset, SCAN_STRATEGIES
    config = json.loads(json.dumps(config))
    matches = [a for a in config['assets'] if a['id'] == ASSET]
    if len(matches) != 1 or matches[0].get('symbol') != 'NEAR/USD':
        raise ValueError('Expected exactly one existing NEARUSD asset with exact NEAR/USD symbol')
    asset = matches[0]
    old_frames = asset.get('timeframes', config['timeframes'])
    asset.update(enabled=True, timeframes=list(dict.fromkeys([*old_frames, *FRAMES])))
    asset['scan_strategies'] = list(dict.fromkeys([*asset.get('scan_strategies', SCAN_STRATEGIES), 'rsi']))
    limits = asset.setdefault('strategy_timeframes', {})
    for strategy in asset['scan_strategies']:
        limits.setdefault(strategy, ['daily'] if strategy == 'ma-cross' else old_frames.copy())
    limits['rsi'] = list(dict.fromkeys([*limits.get('rsi', old_frames), *FRAMES]))
    # The existing asset already selects Kraken for higher intervals. Require
    # that confirmed venue before extending it to the short native intervals.
    overrides = asset.setdefault('timeframe_providers', {})
    if not (asset.get('provider') == 'kraken' and asset.get('exchange') == 'Kraken') and overrides.get('4h') != 'kraken':
        raise ValueError('NEAR/USD must already have an explicitly selected Kraken feed')
    for frame in FRAMES:
        overrides[frame] = 'kraken'
    validate_asset(asset)
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--replace-personal-route', action='store_true',
                        help='Remove existing personal route references to this exact group during enrollment')
    args = parser.parse_args()
    from platform_app import load_env
    load_env()
    api = Database()
    installation = api.literal(api.installation) + '::uuid'
    row = api.readonly('SELECT revision,payload::jsonb AS config FROM bot_runtime.managed_config WHERE id=1')[0]
    config = configure(row['config'])
    print(json.dumps(dict(chat=CHAT, strategy='rsi', divergences=['regular','hidden'],
                         asset=next(a for a in config['assets'] if a['id']==ASSET),
                         expected_revision=row['revision']), indent=2))
    if not args.apply:
        print('Preview only; production settings unchanged.')
        return
    # Recheck the revision while holding the same row lock used for config writes.
    # Shared routes always fail closed. Personal references to only this group
    # can be removed with the explicit replacement flag; other groups are preserved.
    sql = f"""BEGIN;
      SELECT 1 FROM public.dashboard_installations WHERE id={installation} FOR UPDATE;
      SELECT 1 FROM bot_runtime.managed_config WHERE id=1 FOR UPDATE;
      DO $$ BEGIN
        IF (SELECT revision FROM bot_runtime.managed_config WHERE id=1) IS DISTINCT FROM {int(row['revision'])}
          THEN RAISE EXCEPTION 'Configuration changed; rerun preview'; END IF;
        IF EXISTS(SELECT 1 FROM public.telegram_routes r, jsonb_each_text(r.routes) v WHERE r.installation={installation} AND v.value='{CHAT}')
          OR ({'false' if args.replace_personal_route else 'true'} AND EXISTS(SELECT 1 FROM public.telegram_recipients r, jsonb_each_text(r.routes) v WHERE r.installation={installation} AND v.value='{CHAT}'))
          THEN RAISE EXCEPTION 'Chat already has broad routes; reconcile before NEAR-only enrollment'; END IF;
      END $$;
      UPDATE public.telegram_recipients r SET routes=(
        SELECT coalesce(jsonb_object_agg(v.key,v.value),'{{}}'::jsonb) FROM jsonb_each_text(r.routes) v WHERE v.value<>'{CHAT}')
      WHERE r.installation={installation} AND {'true' if args.replace_personal_route else 'false'}
        AND EXISTS(SELECT 1 FROM jsonb_each_text(r.routes) v WHERE v.value='{CHAT}');
      UPDATE bot_runtime.managed_config SET payload={api.literal(json.dumps(config))},revision=revision+1 WHERE id=1;
      INSERT INTO bot_runtime.telegram_asset_subscriptions(chat,asset,timeframes)
      VALUES('{CHAT}','{ASSET}',ARRAY['5m','15m','30m','1h','4h','daily','weekly'])
      ON CONFLICT(chat,asset) DO UPDATE SET timeframes=excluded.timeframes,enabled=true,
        created_at=CASE WHEN telegram_asset_subscriptions.enabled THEN telegram_asset_subscriptions.created_at ELSE now() END;
      COMMIT;"""
    api.query(sql)
    saved = api.readonly(f"SELECT chat,asset,timeframes,enabled FROM bot_runtime.telegram_asset_subscriptions WHERE chat='{CHAT}' AND asset='{ASSET}'")[0]
    latest = api.readonly('SELECT revision,payload::jsonb AS config FROM bot_runtime.managed_config WHERE id=1')[0]
    if saved != dict(chat=CHAT,asset=ASSET,timeframes=FRAMES,enabled=True) or latest['config'] != config:
        raise RuntimeError('Saved configuration verification failed')
    print('Saved and read back at revision',latest['revision'],
          '— worker application and feed readiness still require verification.')


if __name__ == '__main__':
    main()
