# Telegram signal alert logic

Last reviewed: 2026-11-06 (automatic asset setup).

This reference describes local source behavior, not verified production settings.

## Automatic asset setup

Local UI behavior (not verified in production): `web/app.js` (`openAssetSetup`, `automaticAsset`, `assetFrameAvailable`) configures one data connection after the user identifies the instrument. Common currency pairs, BTC/ETH/XBT pairs with explicit quote currencies, and exact supported index/commodity/futures symbols can preselect a category; unknown symbols require a category selection. US-listed stocks/ETFs default to Alpaca, crypto pairs to Kraken, and forex pairs to OANDA. Other instruments require an exact selection from the supported Yahoo/Tiingo mappings. No provider is chosen by timeframe in the normal editor.

New crypto assets default to weekly/daily/4h; monthly is unavailable on Kraken. Other new assets use supported monthly/weekly/daily/4h frames. This branch exposes monthly, weekly, daily, 4h and 1h, matching its existing backend. The separate lower-interval feature is not included. Unsupported intervals are disabled for new selections. Existing asset mappings, timeframe overrides, TradingView mappings, strategy choices and enabled state are preserved on ordinary edits. Existing CSV drafts can enter automatic setup explicitly; no bulk migration changes their venues.

Saving records the chosen instrument mapping (`feed_confirmed=true` is configuration confirmation, not evidence of successful data access). New assets default to scan enabled, with a visible toggle; existing drafts retain their enabled state during setup. Feed access, exact response identity and candle eligibility are checked by the normal scanner/provider code. The UI shows awaiting scanner data check until coverage is available and the configuration revision has been applied; it never treats a save alone as a successful connection test. The add flow uses revision-checked draft creation followed by save, so a failed final save leaves a disabled draft. No test alerts or extra scanner are started by setup. Existing first-scan baselines and alert rules still apply: new assets do not bypass candle validation, strategy qualification, deduplication, routing, or delivery controls.

Sources: `web/app.js` (`openAssetSetup`, `automaticAsset`, `assetDataStatus`, `loadAssets`), `managed_assets.py` (`change`, `validate_asset`, `for_timeframe`), `yahoo_feed.py` (`INSTRUMENTS`), `tiingo_feed.py` (`validate_feed`), provider fetch functions, `scanner.py`.

