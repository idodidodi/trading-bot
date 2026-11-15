-- Apply only to the dedicated local working database, using its owner.
-- The scanner runtime role needs USAGE, SELECT/INSERT/UPDATE/DELETE, not DDL.
CREATE SCHEMA IF NOT EXISTS working;
CREATE TABLE IF NOT EXISTS working.responses (
 namespace text NOT NULL, key text NOT NULL, fetched double precision NOT NULL,
 payload jsonb NOT NULL, PRIMARY KEY(namespace,key)
);
CREATE TABLE IF NOT EXISTS working.candles (
 namespace text NOT NULL, kind text NOT NULL CHECK(kind IN ('candle_cache','backtest_candle_cache')),
 feed text NOT NULL, frame text NOT NULL, start bigint NOT NULL, payload jsonb NOT NULL,
 PRIMARY KEY(namespace,kind,feed,frame,start)
);
CREATE TABLE IF NOT EXISTS working.publication (
 namespace text NOT NULL, kind text NOT NULL, feed text NOT NULL, frame text NOT NULL,
 start bigint NOT NULL, payload jsonb NOT NULL, acknowledged double precision NOT NULL,
 PRIMARY KEY(namespace,kind,feed,frame,start)
);
CREATE TABLE IF NOT EXISTS working.counters (
 namespace text NOT NULL, name text NOT NULL, value bigint NOT NULL,
 PRIMARY KEY(namespace,name)
);
