from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def ensure_state_dir() -> None:
    directory = os.path.dirname(STATE_FILE)
    if directory:
        os.makedirs(directory, exist_ok=True)


def load_state() -> Dict[str, Any]:
    ensure_state_dir()
    if not os.path.exists(STATE_FILE):
        return {"positions": [], "last_buy_time": 0.0}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("state root must be an object")
        positions = data.get("positions", [])
        if not isinstance(positions, list):
            positions = []
        clean: List[Dict[str, Any]] = []
        for raw in positions:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            item["active"] = bool(item.get("active", False))
            item["symbol"] = str(item.get("symbol", SYMBOL)).upper()
            item["amount"] = max(0.0, finite(item.get("amount")))
            item["entry_price"] = max(0.0, finite(item.get("entry_price")))
            item["highest_price"] = max(item["entry_price"], finite(item.get("highest_price"), item["entry_price"]))
            item["take_profit"] = max(0.0, finite(item.get("take_profit")))
            item["stop_loss"] = max(0.0, finite(item.get("stop_loss")))
            item["trailing_confirmations"] = max(0, int(finite(item.get("trailing_confirmations"))))
            clean.append(item)
        data["positions"] = clean
        data["last_buy_time"] = max(0.0, finite(data.get("last_buy_time")))
        return data
    except Exception as exc:
        logger.error("STATE_LOAD_FAILED error=%s; starting with empty state", exc)
        return {"positions": [], "last_buy_time": 0.0}


def save_state(state: Dict[str, Any]) -> None:
    ensure_state_dir()
    temporary = STATE_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, STATE_FILE)


def active_positions(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    positions = state.get("positions", [])
    if not isinstance(positions, list):
        return []
    return [
        p for p in positions
        if isinstance(p, dict)
        and p.get("active") is True
        and str(p.get("symbol", "")).upper() == SYMBOL
    ]


def next_slot(state: Dict[str, Any]) -> str:
    used = {str(p.get("slot_id")) for p in state.get("positions", []) if isinstance(p, dict)}
    index = 1
    while f"slot-{index}" in used:
        index += 1
    return f"slot-{index}"


def build_dataframe(raw: Any) -> pd.DataFrame:
    if not isinstance(raw, list):
        return pd.DataFrame()
    rows = [list(row[:6]) for row in raw if isinstance(row, (list, tuple)) and len(row) >= 6]
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    for column in ["timestamp", "open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["open", "high", "low", "close", "volume"])
    if len(frame) > 1:
        frame = frame.iloc[:-1]
    return frame.reset_index(drop=True)


def position_amount_from_order(order: Dict[str, Any]) -> float:
    for value in (order.get("filled"), order.get("amount")):
        amount = finite(value, -1.0)
        if amount > 0:
            return amount
    info = order.get("info")
    if isinstance(info, dict):
        for key in ("executedQty", "dealQuantity", "quantity", "filled"):
            amount = finite(info.get(key), -1.0)
            if amount > 0:
                return amount
    raise RuntimeError("MEXC order response contains no filled base amount")


def order_average(order: Dict[str, Any], fallback: float) -> float:
    for value in (order.get("average"), order.get("price")):
        price = finite(value, -1.0)
        if price > 0:
            return price
    info = order.get("info")
    if isinstance(info, dict):
        for key in ("avgPrice", "averagePrice", "price"):
            price = finite(info.get(key), -1.0)
            if price > 0:
                return price
    return fallback


def order_cost(order: Dict[str, Any], price: float, amount: float) -> float:
    cost = finite(order.get("cost"))
    if cost > 0:
        return cost
    info = order.get("info")
    if isinstance(info, dict):
        for key in ("cummulativeQuoteQty", "cumQuote", "dealFunds", "cost"):
            cost = finite(info.get(key))
            if cost > 0:
                return cost
    return price * amount


def reconcile_wallet(client: MEXCClient, state: Dict[str, Any]) -> None:
    """Keep state aligned with the real wallet without inventing positions."""
    balances = client.balance()
    market = client.market()
    base = str(market.get("base", "")).upper()
    quote = str(market.get("quote", "")).upper()
    free = balances.get("free", {}) if isinstance(balances, dict) else {}
    free_base = finite(free.get(base)) if isinstance(free, dict) else 0.0
    free_quote = finite(free.get(quote)) if isinstance(free, dict) else 0.0

    if not isinstance(state.get("positions"), list):
        state["positions"] = []

    tracked = active_positions(state)
    remaining = free_base
    for position in tracked:
        wanted = max(0.0, finite(position.get("amount")))
        amount = min(wanted, remaining) if wanted > 0 else 0.0
        if amount <= 0:
            position["active"] = False
            position["amount"] = 0.0
            position["closed_at"] = now_iso()
            position["close_reason"] = "WALLET_NO_BASE_BALANCE"
            logger.warning("POSITION_CLOSED_BY_WALLET_SYNC slot=%s", position.get("slot_id"))
        else:
            position["amount"] = amount
            remaining = max(0.0, remaining - amount)

    if not tracked and free_base > 0:
        logger.info("UNTRACKED_WALLET_BALANCE symbol=%s base=%s free=%.12f; not adopted automatically", SYMBOL, base, free_base)

    save_state(state)
    logger.info("WALLET_SYNC symbol=%s BASE_FREE=%.12f QUOTE_FREE=%.8f active_positions=%d", SYMBOL, free_base, free_quote, len(active_positions(state)))


def create_strategy() -> SpotStrategy:
    return SpotStrategy(
        rsi_period=max(2, int(os.getenv("RSI_PERIOD", "14"))),
        stop_loss_pct=STOP_LOSS_PCT / 100.0,
        trailing_stop_activation_pct=TRAILING_STOP_ACTIVATION_PCT / 100.0,
        trailing_stop_offset_pct=TRAILING_STOP_OFFSET_PCT / 100.0,
        buy_fee=MEXC_BUY_FEE_RATE,
        sell_fee=MEXC_SELL_FEE_RATE,
        net_profit_rate=TARGET_NET_PROFIT_RATE,
    )


def validate_runtime() -> None:
    if not LIVE_TRADING:
        raise RuntimeError("LIVE_TRADING must be true; this production worker does not run paper trading")
    if TRADE_AMOUNT_USDT <= 0:
        raise RuntimeError("TRADE_AMOUNT_USDT must be greater than zero")
    if MAX_POSITIONS < 1:
        raise RuntimeError("MAX_POSITIONS must be at least 1")


def execute_buy(client: MEXCClient, strategy: SpotStrategy, state: Dict[str, Any], signal: Any) -> None:
    if len(active_positions(state)) >= MAX_POSITIONS:
        return
    if time.time() - finite(state.get("last_buy_time")) < BUY_COOLDOWN_SEC:
        return

    quote = str(client.market().get("quote", "USDT")).upper()
    free_quote = client.free_balance(quote)
    spendable = max(0.0, free_quote - CASH_RESERVE_USDT)
    cost = min(float(TRADE_AMOUNT_USDT), spendable)
    minimum_cost = client.minimum_cost()
    if cost <= 0:
        logger.warning("BUY_SKIPPED reason=INSUFFICIENT_%s free=%.8f reserve=%.8f required=%.8f", quote, free_quote, CASH_RESERVE_USDT, TRADE_AMOUNT_USDT)
        return
    if minimum_cost > 0 and cost < minimum_cost:
        logger.warning("BUY_SKIPPED reason=BELOW_EXCHANGE_MINIMUM cost=%.8f minimum=%.8f", cost, minimum_cost)
        return

    market_price = client.last_price()
    logger.info("BUY_SIGNAL symbol=%s market_price=%.12f cost=%.8f rsi=%.2f percent_b=%.4f reason=%s", SYMBOL, market_price, cost, finite(getattr(signal, "rsi_value", 0.0)), finite(getattr(signal, "percent_b", 0.0)), getattr(signal, "reason", ""))
    order = client.create_market_buy(cost)
    order = client.confirm_order(order)
    amount = position_amount_from_order(order)
    entry = order_average(order, market_price)
    actual_cost = order_cost(order, entry, amount)
    take_profit = strategy.calculate_take_profit_price(entry)
    stop_loss = entry * (1.0 - STOP_LOSS_PCT / 100.0) if STOP_LOSS_PCT > 0 else 0.0

    state.setdefault("positions", []).append({
        "active": True,
        "slot_id": next_slot(state),
        "symbol": SYMBOL,
        "amount": amount,
        "entry_price": entry,
        "highest_price": entry,
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "opened_at": now_iso(),
        "opened_ts": time.time(),
        "cost_usdt": actual_cost,
        "last_observed_price": entry,
        "trailing_confirmations": 0,
    })
    state["last_buy_time"] = time.time()
    save_state(state)
    logger.info("BUY_FILLED symbol=%s amount=%.12f entry=%.12f cost=%.8f tp=%.12f sl=%.12f", SYMBOL, amount, entry, actual_cost, take_profit, stop_loss)


def close_position(client: MEXCClient, state: Dict[str, Any], position: Dict[str, Any], reason: str, price: float) -> None:
    amount = max(0.0, finite(position.get("amount")))
    if amount <= 0:
        position["active"] = False
        return
    logger.info("SELL_TRIGGER symbol=%s slot=%s reason=%s price=%.12f amount=%.12f", SYMBOL, position.get("slot_id"), reason, price, amount)
    order = client.create_market_sell(amount)
    order = client.confirm_order(order)
    filled = position_amount_from_order(order)
    avg = order_average(order, price)
    entry = finite(position.get("entry_price"))
    pnl_pct = ((avg - entry) / entry * 100.0) if entry > 0 else 0.0
    position["active"] = False
    position["amount"] = 0.0
    position["closed_at"] = now_iso()
    position["close_reason"] = reason
    position["exit_price"] = avg
    position["exit_amount"] = filled
    position["pnl_pct"] = pnl_pct
    save_state(state)
    logger.info("SELL_FILLED symbol=%s slot=%s amount=%.12f exit=%.12f pnl_pct=%+.4f reason=%s", SYMBOL, position.get("slot_id"), filled, avg, pnl_pct, reason)


def manage_positions(client: MEXCClient, state: Dict[str, Any]) -> None:
    for position in list(active_positions(state)):
        try:
            price = client.last_price()
            entry = finite(position.get("entry_price"))
            highest = max(finite(position.get("highest_price"), entry), price)
            position["highest_price"] = highest
            position["last_observed_price"] = price

            if entry <= 0:
                continue

            if price >= finite(position.get("take_profit")) > 0:
                close_position(client, state, position, "TAKE_PROFIT", price)
                continue
            if finite(position.get("stop_loss")) > 0 and price <= finite(position.get("stop_loss")):
                close_position(client, state, position, "STOP_LOSS", price)
                continue

            activation = entry * (1.0 + TRAILING_STOP_ACTIVATION_PCT / 100.0)
            if TRAILING_STOP_ACTIVATION_PCT > 0 and highest >= activation:
                trailing_price = highest * (1.0 - TRAILING_STOP_OFFSET_PCT / 100.0)
                if price <= trailing_price:
                    position["trailing_confirmations"] = int(position.get("trailing_confirmations", 0)) + 1
                else:
                    position["trailing_confirmations"] = 0
                if position["trailing_confirmations"] >= TRAILING_CONFIRMATION_CANDLES:
                    close_position(client, state, position, "TRAILING_STOP", price)
                    continue

            opened_ts = finite(position.get("opened_ts"))
            if MAX_HOLD_TIME_SEC > 0 and opened_ts > 0 and time.time() - opened_ts >= MAX_HOLD_TIME_SEC:
                close_position(client, state, position, "MAX_HOLD_TIME", price)
                continue
        except Exception:
            logger.exception("POSITION_MANAGEMENT_FAILED slot=%s", position.get("slot_id"))
    save_state(state)


def evaluate_and_trade(client: MEXCClient, strategy: SpotStrategy, state: Dict[str, Any]) -> None:
    raw = client.ohlcv(TIMEFRAME, limit=120)
    df = build_dataframe(raw)
    if df.empty:
        logger.warning("MARKET_DATA_EMPTY symbol=%s timeframe=%s", SYMBOL, TIMEFRAME)
        return

    market_price = client.last_price()
    positions = {str(p.get("slot_id")): p for p in active_positions(state)}
    available_slot = next_slot(state) if len(positions) < MAX_POSITIONS else None
    signal = strategy.evaluate_entry_signal(SYMBOL, df, positions, available_slot)

    action = str(getattr(signal, "action", "HOLD")).upper()
    reason = str(getattr(signal, "reason", ""))
    logger.info(
        "SIGNAL symbol=%s action=%s reason=%s market_price=%.12f signal_price=%.12f rsi=%.2f percent_b=%.4f active=%d",
        SYMBOL,
        action,
        reason,
        market_price,
        finite(getattr(signal, "price", 0.0)),
        finite(getattr(signal, "rsi_value", 0.0)),
        finite(getattr(signal, "percent_b", 0.0)),
        len(active_positions(state)),
    )

    if action == "BUY":
        execute_buy(client, strategy, state, signal)


def configure_logging() -> None:
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        force=True,
    )


def main() -> None:
    configure_logging()
    validate_runtime()
    logger.info("Starting wife_2 MEXC Spot bot: symbol=%s live=%s", SYMBOL, LIVE_TRADING)

    client = MEXCClient()
    strategy = create_strategy()
    state = load_state()
    reconcile_wallet(client, state)

    logger.info(
        "BOT_READY symbol=%s timeframe=%s trade_amount=%.8f max_positions=%d loop=%ss target_net_profit=%.4f%%",
        SYMBOL,
        TIMEFRAME,
        TRADE_AMOUNT_USDT,
        MAX_POSITIONS,
        LOOP_INTERVAL_SECONDS,
        TARGET_NET_PROFIT_RATE * 100.0,
    )

    while True:
        started = time.time()
        try:
            reconcile_wallet(client, state)
            manage_positions(client, state)
            if not active_positions(state):
                evaluate_and_trade(client, strategy, state)
        except Exception:
            logger.exception("MAIN_LOOP_ERROR")
        elapsed = time.time() - started
        time.sleep(max(0.5, LOOP_INTERVAL_SECONDS - elapsed))


if __name__ == "__main__":
    main()
