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
    MEXC_BUY_FEE_RATE,
    MEXC_SELL_FEE_RATE,
    MIN_SLOT_PRICE_DIFF_PCT,
    RSI_PERIOD,
    STATE_FILE,
    STOP_LOSS_PCT,
    SYMBOL,
    TARGET_NET_PROFIT_RATE,
    TIMEFRAME,
    TRADE_AMOUNT_USDT,
    TRAILING_CONFIRMATION_CANDLES,
    TRAILING_STOP_ACTIVATION_PCT,
    TRAILING_STOP_OFFSET_PCT,
)

from exchange_client import MEXCClient
from strategy import SpotStrategy


logger = logging.getLogger("wife_2")


# =========================================================
# SYMBOL HELPERS
# =========================================================

def get_symbol_currencies():
    """
    Extract BASE/QUOTE from SYMBOL.

    Example:
        MX/USDT -> ("MX", "USDT")
        BTC/USDT -> ("BTC", "USDT")
    """
    symbol = str(SYMBOL).strip().upper()

    if "/" not in symbol:
        raise RuntimeError(
            f"Invalid SYMBOL={symbol!r}. Expected BASE/QUOTE."
        )

    base_currency, quote_currency = symbol.split("/", 1)

    base_currency = base_currency.strip().upper()
    quote_currency = quote_currency.strip().upper()

    if not base_currency or not quote_currency:
        raise RuntimeError(
            f"Invalid SYMBOL={symbol!r}. Expected BASE/QUOTE."
        )

    return base_currency, quote_currency


BASE_CURRENCY, QUOTE_CURRENCY = get_symbol_currencies()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def ensure_state_directory():
    directory = os.path.dirname(STATE_FILE)

    if directory:
        os.makedirs(directory, exist_ok=True)


# =========================================================
# STATE
# =========================================================

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

        if not isinstance(data["positions"], list):
            data["positions"] = []

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


def active_positions(state):
    return [
        position
        for position in state.get("positions", [])
        if position.get("active") is True
        and position.get("symbol") == SYMBOL
    ]


def all_active_positions(state):
    return [
        position
        for position in state.get("positions", [])
        if position.get("active") is True
    ]


def next_slot_id(state):
    existing = {
        str(position.get("slot_id"))
        for position in state.get("positions", [])
    }

    index = 1

    while f"slot-{index}" in existing:
        index += 1

    return f"slot-{index}"


# =========================================================
# WALLET HELPERS
# =========================================================

def get_free_balance_from_wallet(
    balances,
    currency,
):
    """
    Read a free balance without assuming BTC or any other
    hard-coded currency.
    """
    free_balances = balances.get("free", {}) or {}

    value = free_balances.get(currency)

    if value is None:
        currency_data = balances.get(currency, {}) or {}
        value = currency_data.get("free", 0.0)

    return float(value or 0.0)


def reconcile_state_with_wallet(client, state):
    """
    Reconcile the bot's CURRENT SYMBOL position with the real
    Spot wallet.

    IMPORTANT:
    - Never assumes BTC.
    - Never converts a BTC balance into an MX position.
    - Never automatically creates a bot position merely because
      some base currency exists in the wallet.
    - Only reconciles an already persisted position belonging
      to the current SYMBOL.
    """

    balances = client.balance()

    base_currency = BASE_CURRENCY
    quote_currency = QUOTE_CURRENCY

    free_base = get_free_balance_from_wallet(
        balances,
        base_currency,
    )

    free_quote = get_free_balance_from_wallet(
        balances,
        quote_currency,
    )

    price = client.last_price()

    market = client.market()
    limits = market.get("limits", {}) or {}

    min_amount = float(
        (limits.get("amount", {}) or {}).get("min") or 0.0
    )

    min_cost = float(
        (limits.get("cost", {}) or {}).get("min") or 0.0
    )

    active = active_positions(state)

    # -----------------------------------------------------
    # IMPORTANT:
    # Ignore active positions belonging to another symbol.
    # This prevents an old BTC/USDT position from becoming
    # an MX/USDT position.
    # -----------------------------------------------------

    mismatched_active = [
        position
        for position in state.get("positions", [])
        if position.get("active") is True
        and position.get("symbol") != SYMBOL
    ]

    if mismatched_active:
        for position in mismatched_active:
            logger.warning(
                "Ignoring active position from different symbol: "
                "position_symbol=%s current_symbol=%s slot=%s",
                position.get("symbol"),
                SYMBOL,
                position.get("slot_id"),
            )

            # Do NOT sell it.
            # Do NOT convert it.
            # Do NOT treat it as the current pair.
            position["ignored_for_current_symbol"] = True

    # -----------------------------------------------------
    # CURRENT SYMBOL POSITION
    # -----------------------------------------------------

    if active:
        position = active[0]

        stored_amount = float(
            position.get(
                "amount",
                0.0,
            )
        )

        # If the wallet no longer has enough of the BASE
        # currency for this current-symbol position, close
        # the internal position record.
        if (
            free_base <= 0
            or (
                min_amount > 0
                and free_base < min_amount
            )
            or (
                min_cost > 0
                and free_base * price < min_cost
            )
        ):
            position["active"] = False
            position["reconciled_at"] = utc_now()
            position["reconciliation_reason"] = (
                f"No sellable {base_currency} remained "
                f"for {SYMBOL}."
            )

            logger.warning(
                "POSITION CLOSED BY WALLET RECONCILIATION: "
                "symbol=%s base=%s free_base=%.12f",
                SYMBOL,
                base_currency,
                free_base,
            )

        else:
            position["amount"] = min(
                stored_amount if stored_amount > 0 else free_base,
                free_base,
            )

            position["highest_price"] = max(
                float(
                    position.get(
                        "highest_price",
                        position.get(
                            "entry_price",
                            price,
                        ),
                    )
                ),
                price,
            )

            position["last_observed_price"] = price

            position["symbol"] = SYMBOL

            logger.info(
                "POSITION_RECONCILED symbol=%s "
                "base=%s amount=%.12f price=%.8f",
                SYMBOL,
                base_currency,
                position["amount"],
                price,
            )

    # -----------------------------------------------------
    # IMPORTANT SAFETY RULE:
    #
    # Do NOT automatically create a bot position from any
    # wallet balance.
    #
    # An existing MX balance could have been purchased
    # manually and must not automatically become a bot trade.
    # -----------------------------------------------------

    else:
        if free_base > 0:
            logger.info(
                "UNTRACKED_WALLET_BALANCE symbol=%s "
                "base=%s free_base=%.12f. "
                "Not adopting it as a bot position.",
                SYMBOL,
                base_currency,
                free_base,
            )

    save_state(state)

    logger.info(
        "WALLET_SYNC symbol=%s "
        "BASE=%s BASE_FREE=%.12f "
        "QUOTE=%s QUOTE_FREE=%.8f "
        "active_positions=%d",
        SYMBOL,
        base_currency,
        free_base,
        quote_currency,
        free_quote,
        len(active_positions(state)),
    )

    return state


# =========================================================
# DATAFRAME
# =========================================================

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
        # Last candle can still be forming.
        # Strategy works only on closed candles.
        df = df.iloc[:-1].copy()

    return df.reset_index(drop=True)


# =========================================================
# STRATEGY
# =========================================================

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
        buy_fee=MEXC_BUY_FEE_RATE,
        sell_fee=MEXC_SELL_FEE_RATE,
        net_profit_rate=TARGET_NET_PROFIT_RATE,
    )


# =========================================================
# ORDER HELPERS
# =========================================================

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


def position_average_price(
    order,
    fallback_price,
):
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


# =========================================================
# TRAILING STOP
# =========================================================

def update_trailing_position(
    position,
    current_price,
):
    entry = float(
        position["entry_price"]
    )

    highest = max(
        float(
            position.get(
                "highest_price",
                entry,
            )
        ),
        current_price,
    )

    previous_price = float(
        position.get(
            "last_observed_price",
            entry,
        )
    )

    position["highest_price"] = highest
    position["last_observed_price"] = current_price

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

        drawdown_pct = (
            (highest - current_price)
            / highest
            * 100.0
            if highest > 0
            else 0.0
        )

        if (
            current_price < previous_price
            and drawdown_pct
            >= TRAILING_STOP_OFFSET_PCT
        ):
            position[
                "trailing_decline_confirmations"
            ] = int(
                position.get(
                    "trailing_decline_confirmations",
                    0,
                )
            ) + 1

        elif current_price >= previous_price:
            position[
                "trailing_decline_confirmations"
            ] = 0

    return position


def should_exit_position(
    position,
    current_price,
):
    entry = float(
        position["entry_price"]
    )

    # -----------------------------------------------------
    # TAKE PROFIT
    # -----------------------------------------------------
    # The bot must sell as soon as the configured profit
    # target is reached.
    #
    # This check intentionally happens BEFORE trailing logic.
    # -----------------------------------------------------

    take_profit = float(
        position.get(
            "take_profit",
            0.0,
        )
    )

    if (
        take_profit > 0
        and current_price >= take_profit
    ):
        profit_pct = (
            (current_price - entry)
            / entry
            * 100.0
            if entry > 0
            else 0.0
        )

        return (
            True,
            "take-profit reached "
            f"({profit_pct:.3f}% from entry)",
        )

    # -----------------------------------------------------
    # STOP / TRAILING STOP
    # -----------------------------------------------------

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

    if (
        position.get("trailing_active")
        and int(
            position.get(
                "trailing_decline_confirmations",
                0,
            )
        )
        >= TRAILING_CONFIRMATION_CANDLES
    ):
        highest = float(
            position.get(
                "highest_price",
                entry,
            )
        )

        drawdown_pct = (
            (highest - current_price)
            / highest
            * 100.0
            if highest > 0
            else 0.0
        )

        return (
            True,
            "Trailing decline from highest price "
            f"({drawdown_pct:.3f}%)",
        )

    if current_price <= stop:
        return (
            True,
            "stop-loss/trailing-stop",
        )

    # -----------------------------------------------------
    # MAXIMUM HOLD TIME
    # -----------------------------------------------------

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
        return (
            True,
            "maximum-hold-time",
        )

    return False, ""


# =========================================================
# BUY
# =========================================================

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

    # This project uses USDT as the quote currency.
    if QUOTE_CURRENCY != "USDT":
        raise RuntimeError(
            f"TRADE_AMOUNT_USDT requires USDT quote currency, "
            f"but SYMBOL={SYMBOL} uses {QUOTE_CURRENCY}"
        )

    available_usdt = client.free_balance(
        QUOTE_CURRENCY
    )

    spendable = (
        available_usdt
        - CASH_RESERVE_USDT
    )

    if spendable <= 0:
        logger.warning(
            "No spendable %s. "
            "available=%.8f reserve=%.8f",
            QUOTE_CURRENCY,
            available_usdt,
            CASH_RESERVE_USDT,
        )
        return

    amount_usdt = min(
        TRADE_AMOUNT_USDT,
        spendable,
    )

    logger.warning(
        "SIGNAL=BUY "
        "SYMBOL=%s "
        "BASE=%s "
        "QUOTE=%s "
        "USDT_BALANCE=%.8f "
        "BUY_AMOUNT=%.8f",
        SYMBOL,
        BASE_CURRENCY,
        QUOTE_CURRENCY,
        available_usdt,
        amount_usdt,
    )

    order = client.create_market_buy(
        amount_usdt
    )

    order = client.confirm_order(
        order
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

    # -----------------------------------------------------
    # STOP LOSS
    # -----------------------------------------------------

    stop = (
        float(signal.suggested_sl)
        if signal.suggested_sl is not None
        else entry
        * (
            1.0
            - STOP_LOSS_PCT / 100.0
        )
    )

    # -----------------------------------------------------
    # TAKE PROFIT
    # -----------------------------------------------------
    # IMPORTANT:
    # Calculate the minimum target from the REAL filled
    # entry price rather than relying only on the signal price.
    # -----------------------------------------------------

    take_profit = client.normalize_price(
        strategy.calculate_take_profit_price(entry)
    )

    if take_profit <= entry:
        raise RuntimeError(
            "Calculated take-profit price is not above the entry price"
        )

    position = {
        "slot_id": slot_id,
        "symbol": SYMBOL,
        "active": True,
        "amount": filled_amount,
        "entry_price": entry,
        "highest_price": entry,
        "last_observed_price": entry,
        "trailing_decline_confirmations": 0,
        "stop_loss": stop,
        "take_profit": take_profit,
        "trailing_active": False,
        "opened_at": utc_now(),
        "opened_at_epoch": time.time(),
        "buy_order_id": order.get("id"),
        "buy_cost_usdt": amount_usdt,
    }

    state["positions"].append(
        position
    )

    state["last_buy_time"] = time.time()

    save_state(state)

    logger.warning(
        "BUY FILLED: "
        "symbol=%s "
        "base=%s "
        "slot=%s "
        "amount=%s "
        "entry=%s "
        "take_profit=%s "
        "stop_loss=%s "
        "order_id=%s "
        "status=%s",
        SYMBOL,
        BASE_CURRENCY,
        slot_id,
        filled_amount,
        entry,
        take_profit,
        stop,
        order.get("id"),
        order.get(
            "status",
            "unknown",
        ),
    )


# =========================================================
# SELL
# =========================================================

def execute_sell(
    client,
    state,
    position,
    reason,
    current_price,
):
    # Never sell a position belonging to another symbol.
    if position.get("symbol") != SYMBOL:
        logger.error(
            "SELL BLOCKED: position symbol=%s "
            "does not match current SYMBOL=%s",
            position.get("symbol"),
            SYMBOL,
        )
        return

    amount = float(
        position["amount"]
    )

    if amount <= 0:
        logger.error(
            "SELL BLOCKED: invalid amount=%s "
            "symbol=%s "
            "slot=%s",
            amount,
            SYMBOL,
            position.get("slot_id"),
        )
        return

    logger.warning(
        "SIGNAL=SELL "
        "SYMBOL=%s "
        "BASE=%s "
        "ENTRY_PRICE=%.8f "
        "CURRENT_PRICE=%.8f "
        "TAKE_PROFIT=%.8f "
        "SELL_AMOUNT=%.10f "
        "reason=%s",
        SYMBOL,
        BASE_CURRENCY,
        float(position["entry_price"]),
        current_price,
        float(
            position.get(
                "take_profit",
                0.0,
            )
        ),
        amount,
        reason,
    )

    order = client.create_market_sell(
        amount
    )

    order = client.confirm_order(
        order
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

    entry = float(
        position["entry_price"]
    )

    position["pnl_pct_estimate"] = (
        (current_price - entry)
        / entry
        * 100.0
    )

    save_state(state)

    logger.warning(
        "SELL FILLED: "
        "symbol=%s "
        "slot=%s "
        "amount=%s "
        "order_id=%s "
        "status=%s "
        "estimated_pnl=%+.3f%% "
        "POSITION=CLOSED "
        "STATE=SEARCHING_BOTTOM",
        SYMBOL,
        position["slot_id"],
        sold_amount,
        order.get("id"),
        order.get(
            "status",
            "unknown",
        ),
        position["pnl_pct_estimate"],
    )


# =========================================================
# MAIN TRADING CYCLE
# =========================================================

def run_cycle(
    client,
    strategy,
    state,
):
    if state.get(
        "wallet_reconciliation_pending"
    ):
        try:
            reconcile_state_with_wallet(
                client,
                state,
            )

            state.pop(
                "wallet_reconciliation_pending",
                None,
            )

        except Exception as exc:
            logger.error(
                "Wallet reconciliation retry failed: %s",
                exc,
            )

    # -----------------------------------------------------
    # OHLCV
    # -----------------------------------------------------

    raw = client.ohlcv(
        timeframe=TIMEFRAME,
        limit=100,
    )

    df = build_dataframe(raw)

    if df.empty:
        logger.info(
            "No closed candle data available."
        )
        return

    # -----------------------------------------------------
    # INDICATORS
    # -----------------------------------------------------

    df = strategy.calculate_indicators(
        df
    )

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
            + ", ".join(
                missing_columns
            )
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

    # -----------------------------------------------------
    # CURRENT PRICE
    # -----------------------------------------------------

    ticker = client.ticker()

    current_price = ticker.get(
        "last"
    )

    if current_price is None:
        raise RuntimeError(
            "MEXC returned no current price"
        )

    current_price = float(
        current_price
    )

    # -----------------------------------------------------
    # CURRENT SYMBOL POSITIONS ONLY
    # -----------------------------------------------------

    positions = active_positions(
        state
    )

    # -----------------------------------------------------
    # EXITS FIRST
    # -----------------------------------------------------

    for position in positions:
        update_trailing_position(
            position,
            current_price,
        )

        highest_price = float(
            position.get(
                "highest_price",
                position["entry_price"],
            )
        )

        drawdown_pct = (
            (highest_price - current_price)
            / highest_price
            * 100.0
            if highest_price > 0
            else 0.0
        )

        take_profit = float(
            position.get(
                "take_profit",
                0.0,
            )
        )

        entry_price = float(
            position["entry_price"]
        )

        profit_pct = (
            (current_price - entry_price)
            / entry_price
            * 100.0
            if entry_price > 0
            else 0.0
        )

        logger.info(
            "PRICE=%.8f "
            "SYMBOL=%s "
            "STATE=%s "
            "ENTRY_PRICE=%.8f "
            "TAKE_PROFIT=%.8f "
            "PROFIT=%.3f%% "
            "HIGHEST_PRICE=%.8f "
            "DRAWDOWN_FROM_HIGH=%.3f%%",
            current_price,
            SYMBOL,
            (
                "TRAILING"
                if position.get(
                    "trailing_active"
                )
                else "TRACKING"
            ),
            entry_price,
            take_profit,
            profit_pct,
            highest_price,
            drawdown_pct,
        )

        save_state(state)

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
                    "DRY RUN SELL: "
                    "symbol=%s slot=%s reason=%s",
                    SYMBOL,
                    position["slot_id"],
                    reason,
                )

    positions = active_positions(
        state
    )

    # -----------------------------------------------------
    # AVAILABLE SLOT
    # -----------------------------------------------------

    available_slot_id = None

    if len(positions) < MAX_POSITIONS:
        available_slot_id = next_slot_id(
            state
        )

    # -----------------------------------------------------
    # ENTRY SIGNAL
    # -----------------------------------------------------

    signal = strategy.evaluate_entry_signal(
        SYMBOL,
        df,
        positions,
        available_slot_id,
    )

    logger.info(
        "PRICE=%.8f "
        "SYMBOL=%s "
        "SIGNAL=%s "
        "RSI=%.2f "
        "%%B=%.3f "
        "REASON=%s",
        current_price,
        SYMBOL,
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

    # -----------------------------------------------------
    # BUY COOLDOWN
    # -----------------------------------------------------

    cooldown = (
        time.time()
        - float(
            state.get(
                "last_buy_time",
                0.0,
            )
        )
    )

    buy_cooldown_seconds = max(
        0,
        BUY_COOLDOWN_SEC,
    )

    if cooldown < buy_cooldown_seconds:
        remaining = (
            buy_cooldown_seconds
            - cooldown
        )

        logger.info(
            "Buy cooldown active: "
            "%.1fs remaining",
            remaining,
        )
        return

    # -----------------------------------------------------
    # BUY
    # -----------------------------------------------------

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


# =========================================================
# STARTUP
# =========================================================

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
        "SYMBOL=%s",
        SYMBOL,
    )

    logger.warning(
        "BASE_CURRENCY=%s",
        BASE_CURRENCY,
    )

    logger.warning(
        "QUOTE_CURRENCY=%s",
        QUOTE_CURRENCY,
    )

    logger.warning(
        "TIMEFRAME=%s",
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

    # -----------------------------------------------------
    # SAFETY CHECK
    # -----------------------------------------------------

    if SYMBOL != f"{BASE_CURRENCY}/{QUOTE_CURRENCY}":
        raise RuntimeError(
            "Internal SYMBOL parsing error"
        )

    if QUOTE_CURRENCY != "USDT":
        raise RuntimeError(
            f"This worker expects a USDT quote currency. "
            f"Current SYMBOL={SYMBOL}"
        )

    client = None

    while client is None:
        try:
            client = MEXCClient()

            logger.warning(
                "MEXC Spot connection: OK"
            )

            logger.warning(
                "MEXC market type: SPOT"
            )

            logger.warning(
                "MEXC active market: %s",
                SYMBOL,
            )

            logger.warning(
                "Base currency: %s",
                BASE_CURRENCY,
            )

            logger.warning(
                "Quote currency: %s",
                QUOTE_CURRENCY,
            )

            logger.warning(
                "Current price: %s",
                client.last_price(),
            )

        except Exception as exc:
            logger.error(
                "MEXC startup failed; retrying: %s",
                exc,
            )

            time.sleep(
                max(
                    5,
                    LOOP_INTERVAL_SECONDS,
                )
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

    try:
        state = reconcile_state_with_wallet(
            client,
            state,
        )

        state.pop(
            "wallet_reconciliation_pending",
            None,
        )

    except Exception as exc:
        logger.error(
            "Wallet state reconciliation failed; "
            "retaining state: %s",
            exc,
        )

        state[
            "wallet_reconciliation_pending"
        ] = True

        save_state(state)

    logger.warning(
        "State file: %s",
        STATE_FILE,
    )

    logger.warning(
        "Active positions loaded for %s: %s",
        SYMBOL,
        len(
            active_positions(state)
        ),
    )

    logger.warning(
        "Trading pair is locked to environment SYMBOL: %s",
        SYMBOL,
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

        elapsed = (
            time.time()
            - started
        )

        sleep_seconds = max(
            1,
            LOOP_INTERVAL_SECONDS
            - elapsed,
        )

        time.sleep(
            sleep_seconds
        )


if __name__ == "__main__":
    run()
