# MEXC Spot 24/7 Multi-Slot Scalper

Railway worker for MEXC Spot using CCXT. The repository is intentionally Python-only.

## Defaults
- Symbols: `SOL/USDT,DOGE/USDT`
- Timeframe: `1m`
- Scan interval: `10s`
- Slot size: `4.00 USDT`
- Initial slots: `2`
- Cash reserve: `2.00 USDT`
- Entry: `RSI <= 38` and Bollinger `%B <= 0.15`
- Trailing activation: `+0.8%`
- Trailing distance: `0.3%`
- Hard stop: `-2.0%`
- Additional slots unlock automatically at each additional `4 USDT` of equity above the protected reserve.

## Railway variables
Required for live mode: `MEXC_API_KEY`, `MEXC_API_SECRET`.

Optional: `TRADE_SYMBOLS`, `SLOT_SIZE_USDT`, `INITIAL_MAX_SLOTS`, `CASH_RESERVE_USDT`, `TIMEFRAME`, `CHECK_INTERVAL_SECONDS`, `RSI_OVERSOLD`, `TRAILING_STOP_ACTIVATION_PCT`, `TRAILING_STOP_OFFSET_PCT`, `STOP_LOSS_PCT`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SIMULATION_MODE`.

Keep MEXC API permissions limited to Spot read/trade; do not enable withdrawals.

## Exact slot protection
The bot does not silently raise the `$4.00` slot to satisfy an exchange minimum. If MEXC reports a minimum order value above `$4.00`, that entry is skipped and logged. This avoids accidental oversizing.

`bot_state.json` is intentionally ignored by Git. Railway local storage is ephemeral unless a persistent volume is configured, so configure a persistent volume if local restart/deploy state must survive container replacement.
