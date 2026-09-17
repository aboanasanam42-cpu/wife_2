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
    symbol = str(SYMBOL).strip().upper()
    if "/" not in symbol:
        raise RuntimeError(f"Invalid SYMBOL={symbol!r}. Expected BASE/QUOTE.")

    base_currency, quote_currency = symbol.split("/", 1)
    base_currency = base_currency.strip().upper()
    quote_currency = quote_currency.strip().upper()

    if not base_currency or not quote_currency:
        raise RuntimeError(f"Invalid SYMBOL={symbol!r}. Expected BASE/QUOTE.")

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
        state = {"positions": [], "last_buy_time": 0.0}
        save_state(state)
        return state

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, dict):
            raise ValueError("Invalid state format")

        data.setdefault("positions", [])
        data.setdefault("last_buy_time", 0.0)

        if not isinstance(data["positions"], list):
            data["positions"] = []

        return data
    except Exception as exc:
        logger.error("Could not load state file: %s", exc)
        return {"positions": [], "last_buy_time": 0.0}


def save_state(state):
    ensure_state_directory()
    temporary = STATE_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)
    os.replace(temporary, STATE_FILE)


def active_positions(state):
    return [
        pos for pos in state.get("positions", [])
        if pos.get("active") is True and pos.get("symbol") == SYMBOL
    ]


def all_active_positions(state):
    return [pos for pos in state.get("positions", []) if pos.get("active") is True]


def next_slot_id(state):
    existing = {str(pos.get("slot_id")) for pos in state.get("positions", [])}
    index = 1
    while f"slot-{index}" in existing:
        index += 1
    return f"slot-{index}"


# =========================================================
# WALLET HELPERS
# =========================================================

def get_free_balance_from_wallet(balances, currency):
    free_balances = balances.get("free", {}) or {}
    value = free_balances.get(currency)
    if value is None:
        currency_data = balances.get(currency, {}) or {}
        value = currency_data.get("free", 0.0)
    return float(value or 0.0)


def reconcile_state_with_wallet(client, state):
    balances = client.balance()
    base_currency = BASE_CURRENCY
    quote_currency = QUOTE_CURRENCY

    free_base = get_free_balance_from_wallet(balances, base_currency)
    free_quote = get_free_balance_from_wallet(balances, quote_currency)
    price = client.last_price()

    market = client.market()
    limits = market.get("limits", {}) or {}
    min_amount = float((limits.get("amount", {}) or {}).get("min") or 0.0)
    min_cost = float((limits.get("cost", {}) or {}).get("min") or 0.0)

    active = active_positions(state)
    mismatched_active = [
        pos for pos in state.get("positions", [])
        if pos.get("active") is True and pos.get("symbol") != SYMBOL
    ]

    if mismatched_active:
        for position in mismatched_active:
            logger.warning(
                "Ignoring active position from different symbol: position_symbol=%s current_symbol=%s slot=%s",
                position.get("symbol"), SYMBOL, position.get("slot_id"),
            )
            position["ignored_for_current_symbol"] = True

    if active:
        position = active[0]
        stored_amount = float(position.get("amount", 0.0))

        if (
            free_base <= 0
            or (min_amount > 0 and free_base < min_amount)
            or (min_cost > 0 and free_base * price < min_cost)
        ):
            position["active"] = False
            position["reconciled_at"] = utc_now()
            position["reconciliation_reason"] = f"No sellable {base_currency} remained for {SYMBOL}."
            logger.warning("POSITION CLOSED BY WALLET RECONCILIATION: symbol=%s base=%s free_base=%.12f", SYMBOL, base_currency, free_base)
        else:
            position["amount"] = min(stored_amount if stored_amount > 0 else free_base, free_base)
            position["highest_price"] = max(float(position.get("highest_price", position.get("entry_price", price))), price)
            position["last_observed_price"] = price
            position["symbol"] = SYMBOL
            logger.info("POSITION_RECONCILED symbol=%s base=%s amount=%.12f price=%.8f", SYMBOL, base_currency, position["amount"], price)
    else:
        if free_base > 0:
            logger.info("UNTRACKED_WALLET_BALANCE symbol=%s base=%s free_base=%.12f. Not adopting it as a bot position.", SYMBOL, base_currency, free_base)

    save_state(state)
    logger.info("WALLET_SYNC symbol=%s BASE=%s BASE_FREE=%.12f QUOTE=%s QUOTE_FREE=%.8f active_positions=%d", SYMBOL, base_currency, free_base, quote_currency, free_quote, len(active_positions(state)))
    return state


# =========================================================
# DATAFRAME
# =========================================================

def build_dataframe(raw_ohlcv):
    if not raw_ohlcv:
        return pd.DataFrame()

    df = pd.DataFrame(raw_ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    for column in ["open", "high", "low", "close", "volume"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df = df.dropna()
    if len(df) > 1:
        df = df.iloc[:-1].copy()
    return df.reset_index(drop=True)


# =========================================================
# STRATEGY
# =========================================================

def create_strategy():
    return SpotStrategy(
        rsi_period=RSI_PERIOD,
        stop_loss_pct=STOP_LOSS_PCT / 100.0,
        trailing_stop_activation_pct=TRAILING_STOP_ACTIVATION_PCT / 100.0,
        trailing_stop_offset_pct=TRAILING_STOP_OFFSET_PCT / 100.0,
        min_slot_price_diff_pct=MIN_SLOT_PRICE_DIFF_PCT / 100.0,
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
    for key in ("executedQty", "dealQuantity", "quantity"):
        value = info.get(key)
        if value is not None:
            return float(value)

    raise RuntimeError("Could not determine filled base amount from MEXC order response")


def position_average_price(order, fallback_price):
    average = order.get("average")
    if average is not None:
        return float(average)

    price = order.get("price")
    if price is not None and float(price) > 0:
        return float(price)

    info = order.get("info") or {}
    for key in ("avgPrice", "price"):
        value = info.get(key)
        if value is not None and float(value) > 0:
            return float(value)

    return float(fallback_price)


# =========================================================
# TRAILING STOP
# =========================================================

def update_trailing_position(position, current_price):
    entry = float(position["entry_price"])
    highest = max(float(position.get("highest_price", entry)), current_price)
    previous_price = float(position.get("last_observed_price", entry))

    position["highest_price"] = highest
    position["last_observed_price"] = current_price

    activation = entry * (1.0 + TRAILING_STOP_ACTIVATION_PCT / 100.0)

    if highest >= activation:
        trailing_stop = highest * (1.0 - TRAILING_STOP_OFFSET_PCT / 100.0)
        current_stop = float(position.get("stop_loss", entry * (1.0 - STOP_LOSS_PCT / 100.0)))

        position["stop_loss"] = max(current_stop, trailing_stop)
        position["trailing_active"] = True

        drawdown_pct = ((highest - current_price) / highest * 100.0) if highest > 0 else 0.0

        if current_price < previous_price and drawdown_pct >= TRAILING_STOP_OFFSET_PCT:
            position["trailing_decline_confirmations"] = int(position.get("trailing_decline_confirmations", 0)) + 1
        elif current_price >= previous_price:
            position["trailing_decline_confirmations"] = 0

    return position


def should_exit_position(position, current_price):
    current_price = float(current_price)
    entry = float(position["entry_price"])

