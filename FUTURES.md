# Futures feed investigation — 9 October 2026

The existing Massive key successfully authenticated and returned three hourly OHLC bars for each of **CLX6**, **GCZ6**, and **ESZ6**. These are dated contracts, not continuous CL1!, GC1! or ES1! histories. No subscription or account setting was changed.

Massive currently lists its continuous-contract data as **Coming soon**: https://www.massive.com/futures . Its documented aggregates endpoint accepts a dated contract ticker: https://massive.com/docs/rest/futures/aggregates . Contract reference queries need an explicit date to select the current contract definitions: https://massive.com/docs/rest/futures/contracts .

CL, GC and ES remain disabled futures entries in the permanent portfolio. The remaining choice is whether to build a continuous series with an explicit rollover and price-adjustment convention, or inspect dated contracts separately and reset the indicator baseline at each rollover. This matters because rollover gaps change Bollinger Bands, RSI and divergence signals. A continuous series assembled locally is not automatically identical to TradingView's continuous charts.

Before enabling scanning, validate the actual feed delay, sufficient closed history for each timeframe, contract rollover, and exchange-session/holiday boundaries. Successful sample OHLC requests establish candle access; they do not prove real-time entitlement or sufficient multi-year history.
