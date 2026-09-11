import json
import logging
import math
import os
import signal
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import ccxt

from config import TradingConfig, setup_logger
from exchange_client import MexcSpotClient
from notifier import TelegramNotifier
from strategy import SpotStrategy

logger = setup_logger("mexc_trader.main")
STATE_FILE = "bot_state.json"


class MultiSlotBot:
    def __init__(self, cfg: TradingConfig):
        self.cfg = cfg
        self.running = True
        self.client = MexcSpotClient(cfg.mexc_api_key, cfg.mexc_api_secret, cfg.simulation_mode)
        self.strategy = SpotStrategy(cfg.rsi_period, cfg.rsi_oversold, cfg.bollinger_period, cfg.bollinger_std, cfg.atr_period, cfg.stop_loss_pct, cfg.min_slot_price_diff_pct)
        self.notifier = TelegramNotifier(cfg.telegram_bot_token, cfg.telegram_chat_id)
        self.slots: Dict[str, Dict[str, Any]] = {}
        self.max_slots = cfg.initial_max_slots
        self.realized_pnl = 0.0
        self.trades = 0
        self.trade_symbols = list(cfg.trade_symbols)
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)
        self.load_state()

    def stop(self, signum=None, frame=None):
        if self.running:
            logger.info("Shutdown signal received; saving state.")
            self.running = False
            self.save_state()
            self.notifier.notify_shutdown()

    def load_state(self):
        if not os.path.exists(STATE_FILE):
            self.ensure_slots()
            return
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as fh:
                state = json.load(fh)
            self.max_slots = max(self.cfg.initial_max_slots, int(state.get("max_slots", self.cfg.initial_max_slots)))
            self.realized_pnl = float(state.get("realized_pnl_usdt", 0.0))
            self.trades = int(state.get("trades", 0))
            self.slots = state.get("slots", {})
        except Exception as exc:
            logger.error("State load failed; starting with empty slot state: %s", exc)
            self.slots = {}
        self.ensure_slots()

    def ensure_slots(self):
        for i in range(1, self.max_slots + 1):
            sid = f"slot_{i}"
            self.slots.setdefault(sid, {"slot_id": sid, "active": False})

    def save_state(self):
        data = {"version": 3, "max_slots": self.max_slots, "realized_pnl_usdt": self.realized_pnl, "trades": self.trades, "slots": self.slots, "selected_symbols": self.trade_symbols, "updated_at": datetime.now(timezone.utc).isoformat()}
        directory = os.path.dirname(os.path.abspath(STATE_FILE)) or "."
        fd, tmp = tempfile.mkstemp(prefix="bot_state.", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, STATE_FILE)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            logger.exception("State save failed")

    def active_slots(self):
        return [s for s in self.slots.values() if s.get("active")]

    def free_slot(self) -> Optional[str]:
        self.ensure_slots()
        for i in range(1, self.max_slots + 1):
            sid = f"slot_{i}"
            if not self.slots[sid].get("active"):
                return sid
        return None

    def update_compounding(self, prices: Dict[str, float]):
        try:
            free = self.client.get_spot_balance("USDT")
        except Exception as exc:
            logger.warning("Cannot calculate compounding capacity: %s", exc)
            return
        invested = sum(float(s.get("amount", 0)) * float(prices.get(s.get("symbol"), s.get("entry_price", 0))) for s in self.active_slots())
        equity = free + invested
        unlocked = math.floor(max(0.0, equity - self.cfg.cash_reserve_usdt) / self.cfg.slot_size_usdt)
        target = max(self.cfg.initial_max_slots, unlocked, len(self.active_slots()))
        if target > self.max_slots:
            old = self.max_slots
            self.max_slots = target
            self.ensure_slots()
            self.save_state()
            self.notifier.send_message(f"🎉 <b>New slot capacity</b>: {old} → {target}; equity <code>{equity:.4f} USDT</code>")

    def process_symbol(self, symbol: str):
        df = self.client.fetch_closed_ohlcv(symbol, self.cfg.timeframe, 100)
        ind = self.strategy.calculate_indicators(df)
        last = ind.iloc[-1]
        ticker = self.client.get_ticker(symbol)
        current = float(ticker.get("last") or ticker.get("bid") or ticker.get("ask") or last["close"])
        prices = {symbol: current}
        for slot in list(self.active_slots()):
            if slot.get("symbol") != symbol:
                continue
            entry = float(slot["entry_price"])
            peak = max(float(slot.get("highest_price") or entry), current)
            slot["highest_price"] = peak
            gain = (peak - entry) / entry if entry else 0.0
            if gain >= self.cfg.trailing_stop_activation_pct:
                floor = peak * (1 - self.cfg.trailing_stop_offset_pct)
                slot["stop_loss"] = max(float(slot.get("stop_loss") or 0), floor)
            exit_signal = self.strategy.evaluate_exit(slot, current, float(last["rsi"]), float(last["bb_percent_b"]))
            if exit_signal:
                self.close_slot(slot, current, exit_signal.reason)
        return prices, ind

    def close_slot(self, slot: Dict[str, Any], current: float, reason: str):
        sid, symbol = slot["slot_id"], slot["symbol"]
        result = self.client.execute_market_sell(symbol, float(slot["amount"]))
        pnl = float(result["cost"]) - float(slot["cost_usdt"])
        self.realized_pnl += pnl
        self.trades += 1
        self.notifier.notify_trade("SELL", symbol, sid, float(result["price"]), float(result["amount"]), pnl, reason)
        self.slots[sid] = {"slot_id": sid, "active": False}
        self.save_state()

    def try_entries(self, indicators: Dict[str, Any]):
        sid = self.free_slot()
        if sid is None:
            return
        for symbol, ind in indicators.items():
            if sid is None:
                break
            signal_result = self.strategy.evaluate_entry(symbol, ind, self.active_slots(), sid)
            if signal_result.action != "BUY":
                continue
            try:
                free = self.client.get_spot_balance("USDT")
                if free < self.cfg.cash_reserve_usdt + self.cfg.slot_size_usdt:
                    logger.info("Reserve shield blocked entry: free=%.4f USDT", free)
                    continue
                result = self.client.execute_market_buy(symbol, self.cfg.slot_size_usdt, self.cfg.max_slippage_pct)
                entry = float(result["price"])
                self.slots[sid] = {"slot_id": sid, "active": True, "symbol": symbol, "entry_price": entry, "amount": float(result["amount"]), "cost_usdt": float(result["cost"]), "highest_price": entry, "stop_loss": entry * (1 - self.cfg.stop_loss_pct), "entry_time": datetime.now(timezone.utc).isoformat(), "order_id": result.get("id", "")}
                self.trades += 1
                self.notifier.notify_trade("BUY", symbol, sid, entry, float(result["amount"]), None, signal_result.reason)
                self.save_state()
                sid = self.free_slot()
            except (ccxt.NetworkError, ccxt.ExchangeError, ValueError, RuntimeError) as exc:
                logger.warning("Entry skipped for %s: %s", symbol, exc)
                self.notifier.notify_error(f"Entry skipped: {symbol}", str(exc))

    def run_once(self):
        prices: Dict[str, float] = {}
        indicators: Dict[str, Any] = {}
        symbols = list(dict.fromkeys(self.trade_symbols + [s.get("symbol") for s in self.active_slots() if s.get("symbol")]))
        with ThreadPoolExecutor(max_workers=max(1, len(symbols))) as pool:
            futures = {pool.submit(self.process_symbol, symbol): symbol for symbol in symbols}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    p, ind = future.result()
                    prices.update(p)
                    indicators[symbol] = ind
                except (ccxt.NetworkError, ccxt.ExchangeError) as exc:
                    logger.warning("Market scan failed for %s: %s", symbol, exc)
                except Exception as exc:
                    logger.exception("Unexpected scan error for %s: %s", symbol, exc)
        self.update_compounding(prices)
        self.try_entries(indicators)
        self.save_state()

    def run(self):
        self.client.initialize()
        if self.cfg.auto_select_symbols:
            self.trade_symbols = self.client.select_top_usdt_symbols(self.cfg.auto_select_count)
        elif not self.trade_symbols:
            raise ValueError("No trading pairs configured and automatic selection is disabled.")
        logger.info("Active trading pairs: %s", ", ".join(self.trade_symbols))
        self.notifier.notify_startup({"pairs": ", ".join(self.trade_symbols), "slot": self.cfg.slot_size_usdt, "max_slots": self.max_slots, "reserve": self.cfg.cash_reserve_usdt, "timeframe": self.cfg.timeframe, "interval": self.cfg.poll_interval_seconds, "simulation": self.cfg.simulation_mode, "auto_selection": self.cfg.auto_select_symbols})
        self.save_state()
        delay = 5
        while self.running:
            started = time.monotonic()
            try:
                self.run_once()
                delay = 5
            except (ccxt.NetworkError, ccxt.ExchangeError) as exc:
                logger.warning("Exchange error: %s; retrying in %ss", exc, delay)
                self.notifier.notify_error("Exchange connection error", str(exc))
                time.sleep(delay)
                delay = min(delay * 2, 60)
            except Exception as exc:
                logger.exception("Main cycle failed")
                self.notifier.notify_error("Main cycle error", str(exc))
                time.sleep(min(delay, 30))
            remaining = max(0.0, self.cfg.poll_interval_seconds - (time.monotonic() - started))
            end = time.monotonic() + remaining
            while self.running and time.monotonic() < end:
                time.sleep(min(1.0, end - time.monotonic()))


if __name__ == "__main__":
    MultiSlotBot(TradingConfig.load_from_env()).run()
