-- Apply after postgres-worker-role.sql. Operator-managed, additional RSI copies.
BEGIN;
CREATE TABLE IF NOT EXISTS bot_runtime.telegram_asset_subscriptions (
 chat text NOT NULL CHECK (chat ~ '^-?[1-9][0-9]{0,19}$'),
 asset text NOT NULL,
 timeframes text[] NOT NULL CHECK (cardinality(timeframes)>0 AND
   timeframes <@ ARRAY['5m','15m','30m','1h','4h','daily','weekly','monthly']::text[]),
 enabled boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(chat,asset)
);
REVOKE ALL ON bot_runtime.telegram_asset_subscriptions FROM PUBLIC,anon,authenticated,trading_worker;
GRANT SELECT ON bot_runtime.telegram_asset_subscriptions TO trading_worker;
COMMIT;
