from __future__ import annotations

import json
import logging
import math
import os
import time
import traceback
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_DOWN
from typing import Any, Dict, Iterable, List, Optional, Tuple

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


# ---------------------------------------------------------------------------
# Numeric helpers
# ---------------------------------------------------------------------------

def finite_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, InvalidOperation):
        return default
    return result if math.isfinite(result) else default


def dec(value: Any) -> Decimal:
    return Decimal(str(value))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_ts() -> float:
    return time.time()


def get_symbol_currencies() -> Tuple[str, str]:
    symbol = str(SYMBOL).strip().upper()
    if "/" not in symbol:
        raise RuntimeError(f"Invalid SYMBOL={symbol!r}; expected BASE/QUOTE")
    base, quote = symbol.split("/", 1)
    base, quote = base.strip().upper(), quote.strip().upper()
    if not base or not quote:
        raise RuntimeError(f"Invalid SYMBOL={symbol!r}; expected BASE/QUOTE")
    return base, quote


BASE_CURRENCY, QUOTE_CURRENCY = get_symbol_currencies()


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------

def validate_runtime_configuration() -> None:
    if not LIVE_TRADING:
        raise RuntimeError(
            "LIVE_TRADING must be true for this production bot. "
            "The bot refuses to start in paper/simulation mode."
        )
    if TRADE_AMOUNT_USDT <= 0:
        raise RuntimeError("TRADE_AMOUNT_USDT must be greater than zero")
    if MAX_POSITIONS < 1:
        raise RuntimeError("MAX_POSITIONS must be at least 1")
    if LOOP_INTERVAL_SECONDS < 1:
        raise RuntimeError("LOOP_INTERVAL_SECONDS must be at least 1")
    if not 0.0 <= MEXC_BUY_FEE_RATE < 1.0:
        raise RuntimeError("MEXC_BUY_FEE_RATE must be in [0, 1)")
    if not 0.0 <= MEXC_SELL_FEE_RATE < 1.0:
        raise RuntimeError("MEXC_SELL_FEE_RATE must be in [0, 1)")
    if TARGET_NET_PROFIT_RATE < 0:
        raise RuntimeError("TARGET_NET_PROFIT_RATE must be non-negative")
    if STOP_LOSS_PCT < 0 or TRAILING_STOP_ACTIVATION_PCT < 0 or TRAILING_STOP_OFFSET_PCT < 0:
        raise RuntimeError("Stop/trailing percentages must be non-negative")


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def ensure_state_directory() -> None:
    directory = os.path.dirname(STATE_FILE)
    if directory:
        os.makedirs(directory, exist_ok=True)


def normalize_position(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    position = dict(raw)
    position["active"] = bool(position.get("active", False))
    position["symbol"] = str(position.get("symbol", SYMBOL)).strip().upper()
    position["amount"] = finite_float(position.get("amount"))
    position["entry_price"] = finite_float(position.get("entry_price"))
    position["highest_price"] = finite_float(position.get("highest_price"), position["entry_price"])
    position["last_observed_price"] = finite_float(position.get("last_observed_price"), position["entry_price"])
    position["take_profit"] = finite_float(position.get("take_profit"), 0.0)
    position["stop_loss"] = finite_float(position.get("stop_loss"), 0.0)
    position["trailing_decline_confirmations"] = max(
        0, int(finite_float(position.get("trailing_decline_confirmations"), 0))
    )
    position.setdefault("slot_id", "slot-unknown")
    position.setdefault("opened_at", utc_now())
    return position


def load_state() -> Dict[str, Any]:
    ensure_state_directory()
    default = {"positions": [], "last_buy_time": 0.0}
    if not os.path.exists(STATE_FILE):
        save_state(default)
        return default

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            raise ValueError("state root is not an object")

        raw_positions = data.get("positions", [])
        if not isinstance(raw_positions, list):
            logger.warning("Invalid positions state type %s; resetting positions", type(raw_positions).__name__)
            raw_positions = []

        positions = []
        for raw in raw_positions:
            position = normalize_position(raw)
            if position is not None:
                positions.append(position)
            else:
                logger.warning("Discarding malformed position state entry: %r", raw)

        data["positions"] = positions
        data["last_buy_time"] = finite_float(data.get("last_buy_time"), 0.0)
        return data
    except Exception as exc:
        logger.error("Could not load state file: %s", exc)
        return default


def save_state(state: Dict[str, Any]) -> None:
    ensure_state_directory()
    if not isinstance(state, dict):
        raise TypeError("state must be a dictionary")
    positions = state.get("positions", [])
    if not isinstance(positions, list):
        raise TypeError("state['positions'] must be a list")

    temporary = STATE_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, STATE_FILE)


def iter_positions(state: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    positions = state.get("positions", [])
    if not isinstance(positions, list):
        return []
    return (p for p in positions if isinstance(p, dict))


def active_positions(state: Dict[str, Any], symbol: Optional[str] = SYMBOL) -> List[Dict[str, Any]]:
    target = str(symbol).upper() if symbol is not None else None
    return [
        p for p in iter_positions(state)
        if p.get("active") is True and (target is None or str(p.get("symbol", "")).upper() == target)
    ]


def all_active_positions(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [p for p in iter_positions(state) if p.get("active") is True]


def next_slot_id(state: Dict[str, Any]) -> str:
    existing = {str(p.get("slot_id")) for p in iter_positions(state)}
    index = 1
    while f"slot-{index}" in existing:
        index += 1
    return f"slot-{index}"


def state_positions_by_slot(state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(p.get("slot_id")): p
        for p in active_positions(state)
        if p.get("slot_id")
    }


# ---------------------------------------------------------------------------
# Wallet helpers / reconciliation
# ---------------------------------------------------------------------------

def get_free_balance_from_wallet(balances: Any, currency: str) -> float:
    if not isinstance(balances, dict):
        return 0.0
    free_balances = balances.get("free")
    if isinstance(free_balances, dict):
        value = free_balances.get(currency)
        if value is not None:
            return max(0.0, finite_float(value))
    currency_data = balances.get(currency)
    if isinstance(currency_data, dict):
        return max(0.0, finite_float(currency_data.get("free")))
    return 0.0


def reconcile_state_with_wallet(client: MEXCClient, state: Dict[str, Any]) -> Dict[str, Any]:
    """Reconcile every active slot against the real free base balance.

    Important: active_positions() returns a list. Never call .get() on that
    list. A single position is selected only after checking the list, using
    active[0], and this implementation reconciles all slots explicitly.
    """
    balances = client.balance()
    price = client.last_price()
    market = client.market()
    limits = market.get("limits", {}) if isinstance(market, dict) else {}
    amount_limits = limits.get("amount", {}) if isinstance(limits, dict) else {}
    cost_limits = limits.get("cost", {}) if isinstance(limits, dict) else {}
    min_amount = max(0.0, finite_float(amount_limits.get("min"))) if isinstance(amount_limits, dict) else 0.0
    min_cost = max(0.0, finite_float(cost_limits.get("min"))) if isinstance(cost_limits, dict) else 0.0

    free_base = get_free_balance_from_wallet(balances, BASE_CURRENCY)
    free_quote = get_free_balance_from_wallet(balances, QUOTE_CURRENCY)

    # Mark positions for other symbols as ignored, but do not mutate them into
    # the current symbol and never treat them as current-slot positions.
    for position in iter_positions(state):
        if position.get("active") is True and str(position.get("symbol", "")).upper() != SYMBOL:
            position["ignored_for_current_symbol"] = True

    active = active_positions(state)
    if active:
        # Safe single-object access: active is a list, active[0] is a dict.
        first_position = active[0]
        if not isinstance(first_position, dict):
            raise RuntimeError("State invariant violated: active[0] is not a position dictionary")

    # The wallet has one free base balance, so split it conservatively across
    # tracked slots rather than assigning the entire wallet amount to every slot.
    remaining_base = free_base
    for position in active:
        stored_amount = max(0.0, finite_float(position.get("amount")))
        allocatable = min(stored_amount if stored_amount > 0 else remaining_base, remaining_base)

        if (
            allocatable <= 0
            or (min_amount > 0 and allocatable < min_amount)
            or (min_cost > 0 and allocatable * price < min_cost)
        ):
            position["active"] = False
            position["amount"] = 0.0
            position["reconciled_at"] = utc_now()
            position["reconciliation_reason"] = f"No sellable {BASE_CURRENCY} remained for {SYMBOL}."
            logger.warning("POSITION_CLOSED_BY_RECONCILIATION slot=%s free_base=%.12f", position.get("slot_id"), remaining_base)
            continue

        position["amount"] = allocatable
        position["highest_price"] = max(
            finite_float(position.get("highest_price"), finite_float(position.get("entry_price"), price)),
            price,
        )
        position["last_observed_price"] = price
        remaining_base = max(0.0, remaining_base - allocatable)
        logger.info(
            "POSITION_RECONCILED slot=%s symbol=%s amount=%.12f price=%.12f",
            position.get("slot_id"), SYMBOL, allocatable, price,
        )

    if not active and free_base > 0:
        logger.info(
            "UNTRACKED_WALLET_BALANCE symbol=%s base=%s free_base=%.12f; not adopted automatically",
            SYMBOL, BASE_CURRENCY, free_base,
        )

    save_state(state)
    logger.info(
        "WALLET_SYNC symbol=%s BASE_FREE=%.12f QUOTE_FREE=%.8f active_positions=%d",
        SYMBOL, free_base, free_quote, len(active_positions(state)),
    )
    return state


# ---------------------------------------------------------------------------
# OHLCV / strategy
# ---------------------------------------------------------------------------

def build_dataframe(raw_ohlcv: Any) -> pd.DataFrame:
    if not isinstance(raw_ohlcv, list) or not raw_ohlcv:
        return pd.DataFrame()
    rows = []
    for row in raw_ohlcv:
        if isinstance(row, (list, tuple)) and len(row) >= 6:
            rows.append(list(row[:6]))
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    for column in ["timestamp", "open", "high", "low", "close", "volume"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close", "volume"])
    if len(df) > 1:
        df = df.iloc[:-1].copy()  # do not trade on an unfinished candle
    return df.reset_index(drop=True)


def create_strategy() -> SpotStrategy:
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


# ---------------------------------------------------------------------------
# Exchange order helpers
# ---------------------------------------------------------------------------

def ensure_order_dict(order: Any) -> Dict[str, Any]:
    if not isinstance(order, dict):
        raise RuntimeError(f"MEXC returned a non-dictionary order: {type(order).__name__}")
    return order


def position_amount_from_order(order: Dict[str, Any]) -> float:
    for value in (order.get("filled"), order.get("amount")):
        parsed = finite_float(value, -1.0)
        if parsed > 0:
            return parsed
    info = order.get("info")
    if isinstance(info, dict):
        for key in ("executedQty", "dealQuantity", "quantity", "filled"):
            parsed = finite_float(info.get(key), -1.0)
            if parsed > 0:
                return parsed
    raise RuntimeError("Could not determine filled base amount from MEXC order response")


def position_average_price(order: Dict[str, Any], fallback_price: float) -> float:
    for value in (order.get("average"), order.get("price")):
        parsed = finite_float(value, -1.0)
        if parsed > 0:
            return parsed
    info = order.get("info")
    if isinstance(info, dict):
        for key in ("avgPrice", "price", "averagePrice"):
            parsed = finite_float(info.get(key), -1.0)
            if parsed > 0:
                return parsed
    return float(fallback_price)


def order_cost(order: Dict[str, Any], fallback_price: float, fallback_amount: float) -> float:
    cost = finite_float(order.get("cost"), 0.0)
    if cost > 0:
        return cost
    info = order.get("info")
    if isinstance(info, dict):
        for key in ("cummulativeQuoteQty", "cumQuote", "dealFunds", "cost"):
            cost = finite_float(info.get(key), 0.0)
            if cost > 0:
                return cost
    return float(fallback_price) * float(fallback_amount)


def market_price_step(client: MEXCClient) -> Optional[Decimal]:
    market = client.market()
    precision = (market.get("precision", {}) or {}).get("price") if isinstance(market, dict) else None
    if precision is not None:
        p = dec(precision)
        if p > 0:
            return Decimal("1").scaleb(-int(p)) if p >= 1 else p
    info = market.get("info", {}) if isinstance(market, dict) else {}
    filters = info.get("filters", []) if isinstance(info, dict) else []
    if isinstance(filters, list):
        for item in filters:
            if isinstance(item, dict) and item.get("filterType") == "PRICE_FILTER":
                value = item.get("tickSize")
                if value:
                    step = dec(value)
                    if step > 0:
                        return step
    return None


def normalize_price_up(client: MEXCClient, price: float) -> float:
    """Round a target price upward to the exchange tick.

    Rounding a take-profit target downward can turn an exact fee-aware target
    into a price that no longer achieves the requested net result. For exits,
    ceiling to the next valid tick is therefore intentional.
    """
    raw = dec(price)
    if raw <= 0:
        raise ValueError("price must be greater than zero")
    step = market_price_step(client)
    if step is None:
        return client.normalize_price(float(raw))
    units = (raw / step).to_integral_value(rounding=ROUND_CEILING)
    return float(units * step)


def fee_aware_take_profit(entry_price: float) -> float:
    entry = dec(entry_price)
    buy_fee = dec(MEXC_BUY_FEE_RATE)
    sell_fee = dec(MEXC_SELL_FEE_RATE)
    target_net = dec(TARGET_NET_PROFIT_RATE)
    if entry <= 0:
        raise ValueError("entry_price must be greater than zero")
    if not Decimal("0") <= buy_fee < Decimal("1"):
        raise ValueError("invalid buy fee")
    if not Decimal("0") <= sell_fee < Decimal("1"):
        raise ValueError("invalid sell fee")
    if target_net < 0:
        raise ValueError("invalid target net profit")
    # Exact gross sale price required when both entry and exit fees are
    # percentage-of-notional fees and target_net is the desired net return.
    return float(entry * (Decimal("1") + target_net) / ((Decimal("1") - buy_fee) * (Decimal("1") - sell_fee)))


# ---------------------------------------------------------------------------
# Position risk / exits
# ---------------------------------------------------------------------------

def update_trailing_position(position: Dict[str, Any], current_price: float) -> Dict[str, Any]:
    entry = finite_float(position.get("entry_price"))
    current = finite_float(current_price)
    if entry <= 0 or current <= 0:
        return position

    highest = max(finite_float(position.get("highest_price"), entry), current)
    previous = finite_float(position.get("last_observed_price"), entry)
    position["highest_price"] = highest
    position["last_observed_price"] = current

    activation = entry * (1.0 + TRAILING_STOP_ACTIVATION_PCT / 100.0)
    initial_stop = entry * (1.0 - STOP_LOSS_PCT / 100.0)
    current_stop = finite_float(position.get("stop_loss"), initial_stop)

    if highest >= activation:
        trailing_stop = highest * (1.0 - TRAILING_STOP_OFFSET_PCT / 100.0)
        position["stop_loss"] = max(current_stop, trailing_stop)
        position["trailing_active"] = True
        if current < previous:
            position["trailing_decline_confirmations"] = int(position.get("trailing_decline_confirmations", 0)) + 1
        elif current >= previous:
            position["trailing_decline_confirmations"] = 0
    else:
        position["stop_loss"] = current_stop
        position.setdefault("trailing_active", False)

    return position


def position_exit_reason(position: Dict[str, Any], current_price: float, current_time: Optional[float] = None) -> Optional[str]:
    price = finite_float(current_price)
    if price <= 0 or position.get("active") is not True:
        return None

    tp = finite_float(position.get("take_profit"))
    sl = finite_float(position.get("stop_loss"))
    if tp > 0 and price >= tp:
        return "TAKE_PROFIT"
    if sl > 0 and price <= sl:
        if position.get("trailing_active"):
            confirmations = int(position.get("trailing_decline_confirmations", 0))
            if confirmations >= TRAILING_CONFIRMATION_CANDLES:
                return "TRAILING_STOP"
        else:
            return "STOP_LOSS"

    opened_at = finite_float(position.get("opened_timestamp"), 0.0)
    if opened_at <= 0:
        opened_text = position.get("opened_at")
        if isinstance(opened_text, str):
            try:
                opened_at = datetime.fromisoformat(opened_text.replace("Z", "+00:00")).timestamp()
            except ValueError:
                opened_at = 0.0
    if MAX_HOLD_TIME_SEC > 0 and opened_at > 0:
        if (current_time or now_ts()) - opened_at >= MAX_HOLD_TIME_SEC:
            return "MAX_HOLD_TIME"
    return None


# ---------------------------------------------------------------------------
# Trade execution
# ---------------------------------------------------------------------------

def execute_buy(client: MEXCClient, strategy: SpotStrategy, state: Dict[str, Any], signal: Any) -> Optional[Dict[str, Any]]:
    if len(active_positions(state)) >= MAX_POSITIONS:
        return None

    balances = client.balance()
    free_quote = get_free_balance_from_wallet(balances, QUOTE_CURRENCY)
    spendable = free_quote - CASH_RESERVE_USDT
    if spendable <= 0:
        logger.info("BUY_SKIPPED no spendable %s balance: free=%.8f reserve=%.8f", QUOTE_CURRENCY, free_quote, CASH_RESERVE_USDT)
        return None

    cost = min(float(TRADE_AMOUNT_USDT), spendable)
    min_cost = client.minimum_cost()
    if min_cost > 0 and cost < min_cost:
        logger.warning("BUY_SKIPPED cost %.8f below exchange minimum %.8f", cost, min_cost)
        return None

    market_price = client.last_price()
    logger.info("BUY_SIGNAL action=%s reason=%s signal_price=%.12f market_price=%.12f", getattr(signal, "action", None), getattr(signal, "reason", None), finite_float(getattr(signal, "price", 0)), market_price)

    order = ensure_order_dict(client.create_market_buy(cost))
    confirmed = ensure_order_dict(client.confirm_order(order))
    filled_amount = position_amount_from_order(confirmed)
    entry = position_average_price(confirmed, market_price)
    if filled_amount <= 0 or entry <= 0:
        raise RuntimeError(f"Invalid confirmed buy fill: amount={filled_amount} entry={entry}")

    # Fee-aware TP is calculated from the actual average fill, never from the
    # signal candle. The target is then rounded upward to a valid market tick.
    raw_tp = fee_aware_take_profit(entry)
    take_profit = normalize_price_up(client, raw_tp)
    stop_loss = client.normalize_price(entry * (1.0 - STOP_LOSS_PCT / 100.0))
    if take_profit <= entry:
        raise RuntimeError(f"Calculated take-profit {take_profit} is not above entry {entry}")

    slot_id = next_slot_id(state)
    opened_at = utc_now()
    position = {
        "slot_id": slot_id,
        "symbol": SYMBOL,
        "active": True,
        "amount": filled_amount,
        "entry_price": entry,
        "entry_cost": order_cost(confirmed, entry, filled_amount),
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "highest_price": entry,
        "last_observed_price": entry,
        "trailing_active": False,
        "trailing_decline_confirmations": 0,
        "opened_at": opened_at,
        "opened_timestamp": now_ts(),
        "buy_order_id": confirmed.get("id"),
        "buy_fee_rate": MEXC_BUY_FEE_RATE,
        "sell_fee_rate": MEXC_SELL_FEE_RATE,
        "target_net_profit_rate": TARGET_NET_PROFIT_RATE,
        "last_exit_reason": None,
    }
    state.setdefault("positions", []).append(position)
    state["last_buy_time"] = now_ts()
    save_state(state)

    logger.info(
        "BUY_FILLED slot=%s symbol=%s amount=%.12f entry=%.12f tp=%.12f sl=%.12f order=%s",
        slot_id, SYMBOL, filled_amount, entry, take_profit, stop_loss, confirmed.get("id"),
    )
    return position


def execute_sell(client: MEXCClient, state: Dict[str, Any], position: Dict[str, Any], reason: str) -> bool:
    amount = max(0.0, finite_float(position.get("amount")))
    if amount <= 0:
        position["active"] = False
        position["last_exit_reason"] = reason
        save_state(state)
        return True

    # Re-read wallet balance immediately before a live sell so the bot never
    # attempts to sell an amount that the exchange no longer holds.
    free_base = client.free_balance(BASE_CURRENCY)
    sell_amount = min(amount, free_base)
    if sell_amount <= 0:
        raise RuntimeError(f"No free {BASE_CURRENCY} available to close slot {position.get('slot_id')}")

    logger.info("SELL_SIGNAL slot=%s reason=%s amount=%.12f price=%.12f", position.get("slot_id"), reason, sell_amount, client.last_price())
    order = ensure_order_dict(client.create_market_sell(sell_amount))
    confirmed = ensure_order_dict(client.confirm_order(order))
    filled = position_amount_from_order(confirmed)

    remaining = max(0.0, sell_amount - filled)
    if remaining > 0:
        position["amount"] = remaining
        position["last_exit_reason"] = reason
        position["sell_order_id"] = confirmed.get("id")
        save_state(state)
        logger.warning("PARTIAL_SELL slot=%s requested=%.12f filled=%.12f remaining=%.12f", position.get("slot_id"), sell_amount, filled, remaining)
        return False

    position["active"] = False
    position["amount"] = 0.0
    position["closed_at"] = utc_now()
    position["closed_timestamp"] = now_ts()
    position["last_exit_reason"] = reason
    position["sell_order_id"] = confirmed.get("id")
    position["sell_average_price"] = position_average_price(confirmed, client.last_price())
    position["sell_filled_amount"] = filled
    save_state(state)

    logger.info("SELL_FILLED slot=%s reason=%s filled=%.12f order=%s", position.get("slot_id"), reason, filled, confirmed.get("id"))
    return True


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        force=True,
    )


def run_cycle(client: MEXCClient, strategy: SpotStrategy, state: Dict[str, Any]) -> Dict[str, Any]:
    # Wallet reconciliation happens every cycle before a sell decision. This
    # prevents stale local state from generating invalid orders.
    state = reconcile_state_with_wallet(client, state)
    current_price = client.last_price()

    # 1) Update risk state and process exits first.
    for position in list(active_positions(state)):
        update_trailing_position(position, current_price)
        reason = position_exit_reason(position, current_price)
        if reason:
            execute_sell(client, state, position, reason)

    save_state(state)

    # 2) Do not open a new slot until all exits have been processed.
    active = active_positions(state)
    if len(active) >= MAX_POSITIONS:
        return state

    last_buy_time = finite_float(state.get("last_buy_time"), 0.0)
    if BUY_COOLDOWN_SEC > 0 and now_ts() - last_buy_time < BUY_COOLDOWN_SEC:
        return state

    # 3) Build the entry signal from closed candles only.
    raw_ohlcv = client.ohlcv(timeframe=TIMEFRAME, limit=100)
    df = build_dataframe(raw_ohlcv)
    if df.empty:
        logger.warning("ENTRY_SKIPPED no usable OHLCV data")
        return state

    available_slot = next_slot_id(state)
    positions_for_strategy = state_positions_by_slot(state)
    signal = strategy.evaluate_entry_signal(
        SYMBOL,
        df,
        positions_for_strategy,
        available_slot,
    )

    action = str(getattr(signal, "action", "HOLD")).upper()
    reason = str(getattr(signal, "reason", ""))
    logger.info("SIGNAL symbol=%s action=%s reason=%s price=%.12f", SYMBOL, action, reason, finite_float(getattr(signal, "price", 0)))

    if action == "BUY":
        execute_buy(client, strategy, state, signal)
    return state


def main() -> None:
    configure_logging()
    logger.info("Starting wife_2 MEXC Spot bot: symbol=%s live=%s", SYMBOL, LIVE_TRADING)
    validate_runtime_configuration()

    client = MEXCClient()
    strategy = create_strategy()
    state = load_state()
    state = reconcile_state_with_wallet(client, state)

    logger.info(
        "BOT_READY symbol=%s timeframe=%s trade_amount=%.8f max_positions=%d loop=%ss",
        SYMBOL, TIMEFRAME, TRADE_AMOUNT_USDT, MAX_POSITIONS, LOOP_INTERVAL_SECONDS,
    )

    while True:
        cycle_started = now_ts()
        try:
            state = run_cycle(client, strategy, state)
        except KeyboardInterrupt:
            logger.info("Shutdown requested")
            break
        except Exception as exc:
            # A transient exchange/API error must not kill the Railway process.
            # State is persisted before each risky mutation, so the next cycle
            # can reconcile from the real wallet again.
            logger.exception("TRADING_CYCLE_ERROR: %s", exc)
            traceback.print_exc()

        elapsed = max(0.0, now_ts() - cycle_started)
        sleep_for = max(0.1, float(LOOP_INTERVAL_SECONDS) - elapsed)
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
