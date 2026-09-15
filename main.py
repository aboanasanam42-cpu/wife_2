import json
import logging
import os
import time
import traceback
from datetime import datetime, timezone

import pandas as pd

from config import (
    BUY_COOLDOWN_SEC,
    CASH_RESERVE_USDT,
    LIVE_TRADING,
    LOOP_INTERVAL_SECONDS,
    MAX_HOLD_TIME_SEC,
    MAX_POSITIONS,
    MIN_SLOT_PRICE_DIFF_PCT,
    RSI_PERIOD,
    STATE_FILE,
    STOP_LOSS_PCT,
    SYMBOL,
    TIMEFRAME,
    TRADE_AMOUNT_USDT,
    TRAILING_STOP_ACTIVATION_PCT,
    TRAILING_STOP_OFFSET_PCT,
)

from exchange_client import MEXCClient
from strategy import SpotStrategy


logger = logging.getLogger("wife_2")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def ensure_state_directory():
    directory = os.path.dirname(STATE_FILE)

    if directory:
        os.makedirs(directory, exist_ok=True)


def load_state():
    ensure_state_directory()

    if not os.path.exists(STATE_FILE):
        state = {
            "positions": [],
            "last_buy_time": 0.0,
        }
        save_state(state)
        return state

    try:
        with open(
            STATE_FILE,
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

        if not isinstance(data, dict):
            raise ValueError("Invalid state format")

        data.setdefault("positions", [])
        data.setdefault("last_buy_time", 0.0)

        return data

    except Exception as exc:
        logger.error(
            "Could not load state file: %s",
            exc,
        )

        return {
            "positions": [],
            "last_buy_time": 0.0,
        }


def save_state(state):
    ensure_state_directory()

    temporary = STATE_FILE + ".tmp"

    with open(
        temporary,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            state,
            file,
            ensure_ascii=False,
            indent=2,
        )

    os.replace(
        temporary,
        STATE_FILE,
    )


def build_dataframe(raw_ohlcv):
    if not raw_ohlcv:
        return pd.DataFrame()

    df = pd.DataFrame(
        raw_ohlcv,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ],
    )

    for column in [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]:
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        )

    df = df.dropna()

    if len(df) > 1:
        # The last candle can still be forming.
        # The strategy must use closed candles only.
        df = df.iloc[:-1].copy()

    return df.reset_index(drop=True)


def create_strategy():
    return SpotStrategy(
        rsi_period=RSI_PERIOD,
        stop_loss_pct=STOP_LOSS_PCT / 100.0,
        trailing_stop_activation_pct=(
            TRAILING_STOP_ACTIVATION_PCT / 100.0
        ),
        trailing_stop_offset_pct=(
            TRAILING_STOP_OFFSET_PCT / 100.0
        ),
        min_slot_price_diff_pct=(
            MIN_SLOT_PRICE_DIFF_PCT / 100.0
        ),
    )


def active_positions(state):
    return [
        position
        for position in state["positions"]
        if position.get("active") is True
    ]


def next_slot_id(state):
    existing = {
        str(position.get("slot_id"))
        for position in state["positions"]
    }

    index = 1

    while f"slot-{index}" in existing:
        index += 1

    return f"slot-{index}"


def position_amount_from_order(order):
    filled = order.get("filled")

    if filled is not None:
        return float(filled)

    amount = order.get("amount")

    if amount is not None:
        return float(amount)

    info = order.get("info") or {}

    for key in (
        "executedQty",
        "dealQuantity",
        "quantity",
    ):
        value = info.get(key)

        if value is not None:
            return float(value)

    raise RuntimeError(
        "Could not determine filled base amount "
        "from MEXC order response"
    )


def position_average_price(order, fallback_price):
    average = order.get("average")

    if average is not None:
        return float(average)

    price = order.get("price")

    if price is not None and float(price) > 0:
        return float(price)

    info = order.get("info") or {}

    for key in (
        "avgPrice",
        "price",
    ):
        value = info.get(key)

        if value is not None and float(value) > 0:
            return float(value)

    return float(fallback_price)


def update_trailing_position(position, current_price):
    entry = float(position["entry_price"])

    highest = max(
        float(
            position.get(
                "highest_price",
                entry,
            )
        ),
        current_price,
    )

    position["highest_price"] = highest

    activation = (
        entry
        * (
            1.0
            + TRAILING_STOP_ACTIVATION_PCT / 100.0
        )
    )

    if highest >= activation:
        trailing_stop = (
            highest
            * (
                1.0
                - TRAILING_STOP_OFFSET_PCT / 100.0
            )
        )

        current_stop = float(
            position.get(
                "stop_loss",
                entry
                * (
                    1.0
                    - STOP_LOSS_PCT / 100.0
                ),
            )
        )

        position["stop_loss"] = max(
            current_stop,
            trailing_stop,
        )

        position["trailing_active"] = True

    return position


def should_exit_position(
    position,
    current_price,
):
    entry = float(position["entry_price"])

    stop = float(
        position.get(
            "stop_loss",
            entry
            * (
                1.0
                - STOP_LOSS_PCT / 100.0
            ),
        )
    )

    take_profit = float(
        position.get(
            "take_profit",
            entry
            * (
                1.0
                + 3.0 / 100.0
            ),
        )
    )

    if current_price <= stop:
        return True, "stop-loss/trailing-stop"

    if current_price >= take_profit:
        return True, "take-profit"

    opened_at = float(
        position.get(
            "opened_at_epoch",
            time.time(),
        )
    )

    if (
        MAX_HOLD_TIME_SEC > 0
        and time.time() - opened_at
        >= MAX_HOLD_TIME_SEC
    ):
        return True, "maximum-hold-time"

    return False, ""


def execute_buy(
    client,
    state,
    signal,
):
    slot_id = signal.target_slot_id

    if slot_id is None:
        logger.warning(
            "BUY signal has no slot ID"
        )
        return

    if TRADE_AMOUNT_USDT <= 0:
        logger.error(
            "TRADE_AMOUNT_USDT must be greater than zero"
        )
        return

    available_usdt = client.free_balance("USDT")

    spendable = (
        available_usdt
        - CASH_RESERVE_USDT
    )

    if spendable <= 0:
        logger.warning(
            "No spendable USDT. "
            "available=%.8f reserve=%.8f",
            available_usdt,
            CASH_RESERVE_USDT,
        )
        return

    amount_usdt = min(
        TRADE_AMOUNT_USDT,
        spendable,
    )

    logger.warning(
        "REAL BUY: %.8f USDT of %s",
        amount_usdt,
        SYMBOL,
    )

    order = client.create_market_buy(
        amount_usdt
    )

    filled_amount = position_amount_from_order(
        order
    )

    average_price = position_average_price(
        order,
        signal.price,
    )

    if filled_amount <= 0:
        raise RuntimeError(
            "MEXC returned a buy order "
            "without a positive filled amount"
        )

    entry = average_price

    stop = (
        signal.suggested_sl
        if signal.suggested_sl is not None
        else entry
        * (
            1.0
            - STOP_LOSS_PCT / 100.0
        )
    )

    take_profit = (
        signal.suggested_tp
        if signal.suggested_tp is not None
        else entry
        * (
            1.0
            + 3.0 / 100.0
        )
    )

    position = {
        "slot_id": slot_id,
        "symbol": SYMBOL,
        "active": True,
        "amount": filled_amount,
        "entry_price": entry,
        "highest_price": entry,
        "stop_loss": stop,
        "take_profit": take_profit,
        "trailing_active": False,
        "opened_at": utc_now(),
        "opened_at_epoch": time.time(),
        "buy_order_id": order.get("id"),
        "buy_cost_usdt": amount_usdt,
    }

    state["positions"].append(position)

    state["last_buy_time"] = time.time()

    save_state(state)

    logger.warning(
        "BUY FILLED: slot=%s amount=%s "
        "entry=%s order=%s",
        slot_id,
        filled_amount,
        entry,
        order.get("id"),
    )


def execute_sell(
    client,
    state,
    position,
    reason,
    current_price,
):
    amount = float(position["amount"])

    logger.warning(
        "REAL SELL: %s %s amount=%s reason=%s",
        SYMBOL,
        position["slot_id"],
        amount,
        reason,
    )

    order = client.create_market_sell(
        amount
    )

    sold_amount = position_amount_from_order(
        order
    )

    if sold_amount <= 0:
        raise RuntimeError(
            "MEXC returned a sell order "
            "without a positive filled amount"
        )

    position["active"] = False
    position["closed_at"] = utc_now()
    position["closed_at_epoch"] = time.time()
    position["sell_order_id"] = order.get("id")
    position["exit_price_observed"] = current_price
    position["exit_reason"] = reason
    position["sold_amount"] = sold_amount

    entry = float(position["entry_price"])

    position["pnl_pct_estimate"] = (
        (current_price - entry)
        / entry
        * 100.0
    )

    save_state(state)

    logger.warning(
        "SELL FILLED: slot=%s amount=%s "
        "order=%s estimated_pnl=%+.3f%%",
        position["slot_id"],
        sold_amount,
        order.get("id"),
        position["pnl_pct_estimate"],
    )


def run_cycle(
    client,
    strategy,
    state,
):
    raw = client.ohlcv(
        timeframe=TIMEFRAME,
        limit=100,
    )

    df = build_dataframe(raw)

    # ---------------------------------------------------------
    # IMPORTANT FIX:
    # Do not call evaluate_entry_signal() before calculating
    # RSI, Bollinger Bands, EMA and ATR.
    # ---------------------------------------------------------

    if df.empty:
        logger.info(
            "No closed candle data available."
        )
        return

    # Calculate all technical indicators required
    # by strategy.evaluate_entry_signal().
    df = strategy.calculate_indicators(df)

    # Verify the indicators required by the strategy exist.
    required_columns = [
        "rsi",
        "bb_percent_b",
        "atr",
        "ema",
        "ema_slope",
        "bb_upper",
        "bb_lower",
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in df.columns
    ]

    if missing_columns:
        raise RuntimeError(
            "Strategy indicators are missing: "
            + ", ".join(missing_columns)
        )

    if len(df) < max(
        30,
        RSI_PERIOD + 5,
    ):
        logger.info(
            "Waiting for enough closed candles: %s",
            len(df),
        )
        return

    ticker = client.ticker()

    current_price = ticker.get("last")

    if current_price is None:
        raise RuntimeError(
            "MEXC returned no current price"
        )

    current_price = float(current_price)

    positions = active_positions(state)

    # ---------------------------------------------------------
    # UPDATE POSITIONS AND PROCESS EXITS FIRST
    # ---------------------------------------------------------

    for position in positions:
        if position.get("symbol") != SYMBOL:
            continue

        update_trailing_position(
            position,
            current_price,
        )

        exit_now, reason = should_exit_position(
            position,
            current_price,
        )

        if exit_now:
            if LIVE_TRADING:
                execute_sell(
                    client,
                    state,
                    position,
                    reason,
                    current_price,
                )
            else:
                logger.warning(
                    "DRY RUN SELL: slot=%s reason=%s",
                    position["slot_id"],
                    reason,
                )

    positions = active_positions(state)

    # ---------------------------------------------------------
    # DETERMINE AVAILABLE SLOT
    # ---------------------------------------------------------

    available_slot_id = None

    if len(positions) < MAX_POSITIONS:
        available_slot_id = next_slot_id(
            state
        )

    # ---------------------------------------------------------
    # GENERATE ENTRY SIGNAL
    # ---------------------------------------------------------

    signal = strategy.evaluate_entry_signal(
        SYMBOL,
        df,
        positions,
        available_slot_id,
    )

    logger.info(
        "price=%s signal=%s RSI=%.2f %%B=%.3f reason=%s",
        current_price,
        signal.action,
        signal.rsi_value,
        signal.percent_b,
        signal.reason,
    )

    if signal.action != "BUY":
        save_state(state)
        return

    if available_slot_id is None:
        logger.info(
            "No available trading slot."
        )
        return

    # ---------------------------------------------------------
    # BUY COOLDOWN
    # ---------------------------------------------------------

    cooldown = time.time() - float(
        state.get(
            "last_buy_time",
            0.0,
        )
    )

    buy_cooldown_seconds = max(0, BUY_COOLDOWN_SEC)

    if cooldown < buy_cooldown_seconds:
        remaining = (
            buy_cooldown_seconds
            - cooldown
        )

        logger.info(
            "Buy cooldown active: %.1fs remaining",
            remaining,
        )
        return

    # ---------------------------------------------------------
    # EXECUTE BUY OR DRY RUN
    # ---------------------------------------------------------

    if LIVE_TRADING:
        execute_buy(
            client,
            state,
            signal,
        )
    else:
        logger.warning(
            "DRY RUN BUY signal. "
            "No real order was submitted."
        )


def run():
    logging.basicConfig(
        level=getattr(
            logging,
            os.getenv(
                "LOG_LEVEL",
                "INFO",
            ).upper(),
            logging.INFO,
        ),
        format=(
            "%(asctime)s | "
            "%(levelname)s | "
            "%(message)s"
        ),
    )

    logger.warning("=" * 70)

    logger.warning(
        "wife_2 - MEXC SPOT TRADING WORKER"
    )

    logger.warning("=" * 70)

    logger.warning(
        "SYMBOL=%s TIMEFRAME=%s",
        SYMBOL,
        TIMEFRAME,
    )

    logger.warning(
        "LIVE_TRADING=%s",
        LIVE_TRADING,
    )

    logger.warning(
        "TRADE_AMOUNT_USDT=%.4f",
        TRADE_AMOUNT_USDT,
    )

    logger.warning(
        "MAX_POSITIONS=%s",
        MAX_POSITIONS,
    )

    logger.warning(
        "CHECK_INTERVAL=%ss",
        LOOP_INTERVAL_SECONDS,
    )

    client = MEXCClient()

    logger.warning(
        "MEXC Spot connection: OK"
    )

    logger.warning(
        "MEXC market type: SPOT"
    )

    logger.warning(
        "Current price: %s",
        client.last_price(),
    )

    if LIVE_TRADING:
        logger.warning(
            "!!! REAL TRADING IS ENABLED !!!"
        )

        logger.warning(
            "The worker can place real BUY/SELL orders."
        )
    else:
        logger.warning(
            "DRY RUN MODE - NO REAL ORDERS"
        )

    strategy = create_strategy()

    state = load_state()

    logger.warning(
        "State file: %s",
        STATE_FILE,
    )

    logger.warning(
        "Active positions loaded: %s",
        len(active_positions(state)),
    )

    while True:
        started = time.time()

        try:
            run_cycle(
                client,
                strategy,
                state,
            )

        except KeyboardInterrupt:
            logger.warning(
                "Worker stopped."
            )
            break

        except Exception as exc:
            logger.error(
                "Worker cycle failed: %s",
                exc,
            )

            traceback.print_exc()

        elapsed = time.time() - started

        sleep_seconds = max(
            1,
            LOOP_INTERVAL_SECONDS - elapsed,
        )

        time.sleep(sleep_seconds)


if __name__ == "__main__":
    run()
