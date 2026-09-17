# تم تعديل دالة معالجة البيع التجريبي ودورة التداول لحل المشاكل المنطقية

def run_cycle(client, strategy, state):
    if state.get("wallet_reconciliation_pending"):
        try:
            reconcile_state_with_wallet(client, state)
            state.pop("wallet_reconciliation_pending", None)
        except Exception as exc:
            logger.error("Wallet reconciliation retry failed: %s", exc)

    # 1. جلب البيانات
    raw = client.ohlcv(timeframe=TIMEFRAME, limit=100)
    df = build_dataframe(raw)

    if df.empty:
        logger.info("No closed candle data available.")
        return

    # 2. حساب المؤشرات
    df = strategy.calculate_indicators(df)

    required_columns = ["rsi", "bb_percent_b", "atr", "ema", "ema_slope", "bb_upper", "bb_lower"]
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        raise RuntimeError("Strategy indicators are missing: " + ", ".join(missing_columns))

    if len(df) < max(30, RSI_PERIOD + 5):
        logger.info("Waiting for enough closed candles: %s", len(df))
        return

    # 3. جلب السعر الحالي الحي
    ticker = client.ticker()
    current_price = ticker.get("last") or ticker.get("close")
    if current_price is None or float(current_price) <= 0:
        raise RuntimeError(f"Invalid MEXC current price: {current_price}")
    current_price = float(current_price)

    positions = active_positions(state)

    # 4. معالجة صفقات الخروج أولاً
    for position in positions:
        update_trailing_position(position, current_price)
        
        highest_price = float(position.get("highest_price", position["entry_price"]))
        drawdown_pct = ((highest_price - current_price) / highest_price * 100.0) if highest_price > 0 else 0.0
        take_profit = float(position.get("take_profit", 0.0))
        entry_price = float(position["entry_price"])
        profit_pct = ((current_price - entry_price) / entry_price * 100.0) if entry_price > 0 else 0.0

        logger.info(
            "PRICE=%.8f | SYMBOL=%s | STATE=%s | PROFIT=%.3f%% | DRAWDOWN=%.3f%%",
            current_price, SYMBOL, "TRAILING" if position.get("trailing_active") else "TRACKING", profit_pct, drawdown_pct
        )
        save_state(state)

        exit_now, reason = should_exit_position(position, current_price)
        if exit_now:
            if LIVE_TRADING:
                execute_sell(client, state, position, reason, current_price)
            else:
                logger.warning("DRY RUN SELL: symbol=%s slot=%s reason=%s", SYMBOL, position["slot_id"], reason)
                # إصلاح خطأ التعليق في الـ Dry Run: إغلاق الصفقة وهمياً في الذاكرة لمنع التكرار اللانهائي
                position["active"] = False
                position["closed_at"] = utc_now()
                position["exit_reason"] = f"DRY_RUN: {reason}"
                save_state(state)

    positions = active_positions(state)

    # 5. الفحص لفتح صفقات جديدة
    available_slot_id = next_slot_id(state) if len(positions) < MAX_POSITIONS else None

    # دمج السعر الحي كآخر سطر مؤقت في الـ DataFrame لضمان دقة المؤشرات الحالية مع السعر الفعلي للشركات
    # (خطوة اختيارية إذا كنت ترغب في مطابقة السعر اللحظي بدقة)
    signal = strategy.evaluate_entry_signal(SYMBOL, df, positions, available_slot_id)

    logger.info(
        "PRICE=%.8f | SYMBOL=%s | SIGNAL=%s | RSI=%.2f | %%B=%.3f",
        current_price, SYMBOL, signal.action, signal.rsi_value, signal.percent_b
    )

    if signal.action != "BUY":
        save_state(state)
        return

    if available_slot_id is None:
        logger.info("No available trading slot.")
        return

    # 6. الـ Cooldown للـ Buy
    cooldown = time.time() - float(state.get("last_buy_time", 0.0))
    if cooldown < max(0, BUY_COOLDOWN_SEC):
        logger.info("Buy cooldown active: %.1fs remaining", max(0, BUY_COOLDOWN_SEC) - cooldown)
        return

    # 7. تنفيذ الشراء
    if LIVE_TRADING:
        execute_buy(client, strategy, state, signal)
    else:
        logger.warning("DRY RUN BUY signal. No real order was submitted.")
        # لمنع تكرار الشراء الوهمي في نفس اللحظة بشكل مستمر
        state["last_buy_time"] = time.time()
        save_state(state)
