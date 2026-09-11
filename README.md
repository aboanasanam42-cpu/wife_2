# MEXC Spot 24/7 Multi-Slot Scalper

Railway worker for MEXC Spot using CCXT. The repository is intentionally Python-only.

## Defaults
- **Automatic pair selection:** enabled by default; selects the top 4 active MEXC Spot `*/USDT` pairs by current 24h quote volume.
- Stablecoin-vs-stablecoin pairs are excluded.
- Timeframe: `1m`
- Scan interval: `10s`
- Slot size: `2.00 USDT`
- Initial slots: `4`
- Cash reserve: `2.00 USDT`
- Entry: `RSI <= 38` and Bollinger `%B <= 0.15`
- Trailing activation: `+0.8%`
- Trailing distance: `0.3%`
- Hard stop: `-2.0%`
- Additional slots unlock automatically at each additional `2 USDT` of equity above the protected reserve.

## Automatic pair selection
When `AUTO_SELECT_SYMBOLS=true`, the bot loads active MEXC Spot markets and ranks eligible USDT pairs by 24h quote volume. The highest-volume pairs are selected for scanning. The count is controlled by `AUTO_SELECT_COUNT` and defaults to `4`.

If an already-open position belongs to a pair that is no longer in the newly selected top list, that position is still monitored until it closes; automatic selection therefore does not abandon existing positions.

To force specific pairs instead, set `AUTO_SELECT_SYMBOLS=false` and provide `TRADE_SYMBOLS`, for example `BTC/USDT,ETH/USDT,SOL/USDT,DOGE/USDT`.

## Railway variables
Required for live mode: `MEXC_API_KEY`, `MEXC_API_SECRET`.

Optional: `AUTO_SELECT_SYMBOLS`, `AUTO_SELECT_COUNT`, `TRADE_SYMBOLS`, `SLOT_SIZE_USDT`, `INITIAL_MAX_SLOTS`, `CASH_RESERVE_USDT`, `TIMEFRAME`, `CHECK_INTERVAL_SECONDS`, `RSI_OVERSOLD`, `TRAILING_STOP_ACTIVATION_PCT`, `TRAILING_STOP_OFFSET_PCT`, `STOP_LOSS_PCT`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SIMULATION_MODE`.

Keep MEXC API permissions limited to Spot read/trade; do not enable withdrawals.

## Exact slot protection
The bot does not silently raise the `$2.00` slot to satisfy an exchange minimum. If MEXC reports a minimum order value above `$2.00`, that entry is skipped and logged. This avoids accidental oversizing.

`bot_state.json` is intentionally ignored by Git. Railway local storage is ephemeral unless a persistent volume is configured, so configure a persistent volume if local restart/deploy state must survive container replacement.
