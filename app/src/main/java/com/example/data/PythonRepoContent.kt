package com.example.data

import com.example.model.PythonFileItem

object PythonRepoContent {
    val files = listOf(
        PythonFileItem(
            name = "strategy.py",
            description = "Multi-slot strategy evaluating Bollinger Bands %B, RSI, ATR, and anti-clustering distance rules.",
            badge = "Multi-Slot Strategy",
            code = """
# strategy.py - Multi-Slot Strategy with Anti-Clustering Decoupling
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
import pandas as pd
import numpy as np

@dataclass
class SignalResult:
    action: str  # BUY, SELL, HOLD
    price: float
    rsi_value: float
    percent_b: float
    atr_value: float
    reason: str
    symbol: Optional[str] = None
    suggested_sl: Optional[float] = None
    suggested_tp: Optional[float] = None
    target_slot_id: Optional[str] = None

class SpotStrategy:
    def __init__(
        self,
        rsi_period: int = 14,
        rsi_oversold: float = 36.0,
        rsi_overbought: float = 68.0,
        ema_period: int = 20,
        bollinger_period: int = 20,
        bollinger_std: float = 2.0,
        atr_period: int = 14,
        stop_loss_pct: float = 0.02,
        take_profit_pct: float = 0.03,
        min_slot_price_diff_pct: float = 0.8,
    ):
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.ema_period = ema_period
        self.bollinger_period = bollinger_period
        self.bollinger_std = bollinger_std
        self.atr_period = atr_period
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.min_slot_price_diff_pct = min_slot_price_diff_pct

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        sma = df["close"].rolling(window=self.bollinger_period).mean()
        std = df["close"].rolling(window=self.bollinger_period).std()
        df["bb_upper"] = sma + (std * self.bollinger_std)
        df["bb_lower"] = sma - (std * self.bollinger_std)
        band_diff = df["bb_upper"] - df["bb_lower"]
        df["bb_percent_b"] = np.where(band_diff > 0, (df["close"] - df["bb_lower"]) / band_diff, 0.5)

        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.rsi_period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.rsi_period).mean()
        rs = gain / loss.replace(0, np.nan)
        df["rsi"] = (100 - (100 / (1 + rs))).fillna(50.0)

        df["ema"] = df["close"].ewm(span=self.ema_period, adjust=False).mean()

        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df["atr"] = true_range.rolling(window=self.atr_period).mean().bfill()
        return df

    def evaluate_slot_exit(self, slot_id: str, slot: Dict[str, Any], current_price: float, rsi: float, pct_b: float) -> Optional[SignalResult]:
        if not slot.get("active"):
            return None

        entry_price = float(slot["entry_price"])
        sl_price = float(slot.get("stop_loss", entry_price * (1.0 - self.stop_loss_pct)))
        tp_price = float(slot.get("take_profit", entry_price * (1.0 + self.take_profit_pct)))

        if current_price <= sl_price:
            pnl_pct = ((current_price - entry_price) / entry_price) * 100.0
            return SignalResult(
                action="SELL",
                price=current_price,
                rsi_value=rsi,
                percent_b=pct_b,
                atr_value=0.0,
                reason=f"Stop/Trailing Exit on {slot_id} (Price: ${'$'}{current_price:,.2f}, Floor: ${'$'}{sl_price:,.2f}, PnL: {pnl_pct:+.2f}%)",
                target_slot_id=slot_id,
            )

        if current_price >= tp_price:
            pnl_pct = ((current_price - entry_price) / entry_price) * 100.0
            return SignalResult(
                action="SELL",
                price=current_price,
                rsi_value=rsi,
                percent_b=pct_b,
                atr_value=0.0,
                reason=f"Take-Profit Target reached on {slot_id} (Price: ${'$'}{current_price:,.2f}, TP: ${'$'}{tp_price:,.2f}, PnL: {pnl_pct:+.2f}%)",
                target_slot_id=slot_id,
            )

        pnl_pct = ((current_price - entry_price) / entry_price) * 100.0
        if pnl_pct >= 1.0 and pct_b > 0.95 and rsi >= self.rsi_overbought:
            return SignalResult(
                action="SELL",
                price=current_price,
                rsi_value=rsi,
                percent_b=pct_b,
                atr_value=0.0,
                reason=f"Exhaustion Exit on {slot_id} (%B: {pct_b:.2f}, RSI: {rsi:.1f}, PnL: {pnl_pct:+.2f}%)",
                target_slot_id=slot_id,
            )
        return None

    def evaluate_entry_decoupling(self, current_price: float, active_slots: List[Dict[str, Any]], symbol: Optional[str] = None) -> tuple[bool, str]:
        same_asset_slots = [s for s in active_slots if s.get("active") and (not symbol or s.get("symbol") == symbol)]
        for slot in same_asset_slots:
            entry_p = float(slot.get("entry_price", 0.0))
            if entry_p <= 0: continue
            diff_pct = abs(current_price - entry_p) / entry_p * 100.0
            if diff_pct < self.min_slot_price_diff_pct:
                return False, f"Anti-Clustering ({symbol}): ${'$'}{current_price:,.4f} is only {diff_pct:.2f}% from {slot.get('slot_id')} entry."
        return True, "Decoupled."

    def evaluate_entry_signal(self, symbol: str, df: pd.DataFrame, active_slots: List[Dict[str, Any]], available_slot_id: Optional[str]) -> SignalResult:
        if available_slot_id is None:
            return SignalResult("HOLD", float(df.iloc[-1]["close"]), float(df.iloc[-1]["rsi"]), float(df.iloc[-1]["bb_percent_b"]), float(df.iloc[-1]["atr"]), "All slots full.", symbol=symbol)

        last = df.iloc[-1]
        prev = df.iloc[-2]
        current_price = float(last["close"])
        rsi = float(last["rsi"])
        pct_b = float(last["bb_percent_b"])
        atr = float(last["atr"])

        is_dip = (pct_b < 0.15 and rsi <= self.rsi_oversold) or (float(prev["bb_percent_b"]) <= 0.05 and pct_b > float(prev["bb_percent_b"]) and rsi <= self.rsi_oversold + 4.0)

        if is_dip:
            can_enter, decouple_reason = self.evaluate_entry_decoupling(current_price, active_slots, symbol=symbol)
            if not can_enter:
                return SignalResult("HOLD", current_price, rsi, pct_b, atr, decouple_reason, symbol=symbol)

            dynamic_sl = current_price - max(1.5 * atr, current_price * self.stop_loss_pct)
            dynamic_tp = current_price + max(2.5 * atr, current_price * self.take_profit_pct)
            return SignalResult(
                action="BUY",
                price=current_price,
                rsi_value=rsi,
                percent_b=pct_b,
                atr_value=atr,
                symbol=symbol,
                suggested_sl=dynamic_sl,
                suggested_tp=dynamic_tp,
                reason=f"Dip Signal on {symbol} for {available_slot_id}: %B={pct_b:.2f}, RSI={rsi:.1f}",
                target_slot_id=available_slot_id,
            )

        return SignalResult("HOLD", current_price, rsi, pct_b, atr, f"Scanning {symbol} for micro-dip.", symbol=symbol)
            """.trimIndent()
        ),
        PythonFileItem(
            name = "main.py",
            description = "Multi-slot execution daemon with dynamic compounding, independent slot state, and auto-scaling.",
            badge = "Multi-Slot Daemon",
            code = """
# main.py - MEXC Independent Multi-Slot Compounding Engine
import os, sys, json, time, signal, logging, traceback, math
from datetime import datetime, timezone
import ccxt
from config import TradingConfig, setup_logger
from notifier import TelegramNotifier
from strategy import SpotStrategy, SignalResult
from exchange_client import MexcSpotClient

logger = setup_logger("mexc_trader.main")
STATE_FILE = "bot_state.json"

class MexcMultiSlotBot:
    def __init__(self, config: TradingConfig):
        self.config = config
        self.is_running = True
        self.slots = {}
        self.max_allowed_slots = config.initial_max_slots
        self.realized_pnl_usdt = 0.0
        self.total_trades_count = 0

        self.notifier = TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id)
        self.client = MexcSpotClient(config.mexc_api_key, config.mexc_api_secret, config.simulation_mode)
        self.strategy = SpotStrategy(
            rsi_period=config.rsi_period,
            rsi_oversold=config.rsi_oversold,
            rsi_overbought=config.rsi_overbought,
            ema_period=config.ema_period,
            bollinger_period=config.bollinger_period,
            bollinger_std=config.bollinger_std,
            atr_period=config.atr_period,
            stop_loss_pct=config.stop_loss_pct,
            take_profit_pct=config.take_profit_pct,
            min_slot_price_diff_pct=config.min_slot_price_diff_pct,
        )
        self._init_slots()
        self._setup_signals()
        self._load_state()

    def _init_slots(self):
        for i in range(1, self.max_allowed_slots + 1):
            sid = f"slot_{i}"
            if sid not in self.slots:
                self.slots[sid] = {"active": False, "slot_id": sid, "symbol": "", "entry_price": 0.0, "amount": 0.0, "cost_usdt": 0.0, "highest_price": 0.0, "stop_loss": 0.0, "take_profit": 0.0}

    def _setup_signals(self):
        def handle(signum, frame):
            self.is_running = False
            self._save_state()
            self.notifier.notify_shutdown("SIGTERM" if signum == signal.SIGTERM else "SIGINT")
        signal.signal(signal.SIGINT, handle)
        signal.signal(signal.SIGTERM, handle)

    def _load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r") as f:
                    d = json.load(f)
                    self.max_allowed_slots = int(d.get("max_allowed_slots", self.config.initial_max_slots))
                    self.realized_pnl_usdt = float(d.get("realized_pnl_usdt", 0.0))
                    for sid, sdata in d.get("slots", {}).items():
                        self.slots[sid] = sdata
                        self.slots[sid].setdefault("symbol", "")
                    self._init_slots()
            except Exception as e:
                logger.error(f"State load error: {e}")

    def _save_state(self):
        with open(STATE_FILE, "w") as f:
            json.dump({
                "symbols": self.config.trade_symbols,
                "slot_size_usdt": self.config.slot_size_usdt,
                "max_allowed_slots": self.max_allowed_slots,
                "realized_pnl_usdt": self.realized_pnl_usdt,
                "slots": self.slots,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }, f, indent=2)

    def _update_compounding_capacity(self, latest_prices):
        try:
            free_usdt = self.client.get_spot_balance("USDT")
            in_slots_val = sum(s.get("amount", 0.0) * (latest_prices.get(s.get("symbol", "")) or s.get("entry_price", 0.0)) for s in self.slots.values() if s.get("active"))
            equity = free_usdt + in_slots_val
            available = max(0.0, equity - self.config.cash_reserve_usdt)
            calc_slots = math.floor(available / self.config.slot_size_usdt)
            active_cnt = sum(1 for s in self.slots.values() if s.get("active"))
            new_capacity = max(self.config.initial_max_slots, active_cnt, calc_slots)
            if new_capacity > self.max_allowed_slots:
                self.max_allowed_slots = new_capacity
                self._init_slots()
                self._save_state()
                self.notifier.notify_milestone_expansion(self.max_allowed_slots, equity, free_usdt)
        except Exception as e:
            logger.warning(f"Equity check failed: {e}")

    def _update_trailing_stops_for_symbol(self, symbol, price):
        for sid, slot in self.slots.items():
            if not slot.get("active") or slot.get("symbol") != symbol: continue
            ep = slot["entry_price"]
            hp = max(slot.get("highest_price", ep), price)
            slot["highest_price"] = hp
            gain = (hp - ep) / ep
            if gain >= self.config.trailing_stop_activation_pct:
                new_floor = hp * (1.0 - self.config.trailing_stop_offset_pct)
                if new_floor > slot.get("stop_loss", 0.0):
                    slot["stop_loss"] = new_floor
                    self._save_state()

    def start(self):
        self.client.initialize()
        while self.is_running:
            try:
                self._iteration()
            except Exception as e:
                logger.error(f"Loop error: {e}")
                time.sleep(10)
            for _ in range(self.config.poll_interval_seconds):
                if not self.is_running: break
                time.sleep(1)

    def _iteration(self):
        latest_prices = {}
        for symbol in self.config.trade_symbols:
            try:
                df = self.client.fetch_closed_ohlcv(symbol, self.config.timeframe)
                df_ind = self.strategy.calculate_indicators(df)
                last = df_ind.iloc[-1]
                price, rsi, pct_b = float(last["close"]), float(last["rsi"]), float(last["bb_percent_b"])
                latest_prices[symbol] = price

                self._update_trailing_stops_for_symbol(symbol, price)

                # Independent Exits for slots holding this asset
                for slot in list(self.slots.values()):
                    if not slot.get("active") or slot.get("symbol") != symbol: continue
                    sig = self.strategy.evaluate_slot_exit(slot["slot_id"], slot, price, rsi, pct_b, symbol=symbol)
                    if sig and sig.action == "SELL":
                        self._execute_exit(slot["slot_id"], sig)

                # Dynamic Entry Evaluation
                avail_id = next((f"slot_{i}" for i in range(1, self.max_allowed_slots + 1) if not self.slots.get(f"slot_{i}", {}).get("active")), None)
                active_list = [s for s in self.slots.values() if s.get("active")]
                if avail_id:
                    sig = self.strategy.evaluate_entry_signal(symbol, df_ind, active_list, avail_id)
                    if sig.action == "BUY" and sig.target_slot_id:
                        self._execute_entry(sig)
            except Exception as e:
                logger.warning(f"Error scanning {symbol}: {e}")

        self._update_compounding_capacity(latest_prices)

    def _execute_entry(self, sig):
        sid = sig.target_slot_id
        symbol = sig.symbol or self.config.trade_symbol
        order = self.client.execute_market_buy(symbol, self.config.slot_size_usdt)
        self.slots[sid] = {
            "active": True, "slot_id": sid, "symbol": symbol, "entry_price": order["price"], "highest_price": order["price"],
            "amount": order["amount"], "cost_usdt": order["cost"], "entry_time": datetime.now(timezone.utc).isoformat(),
            "stop_loss": sig.suggested_sl or (order["price"] * (1.0 - self.config.stop_loss_pct)),
            "take_profit": sig.suggested_tp or (order["price"] * (1.0 + self.config.take_profit_pct)),
        }
        self._save_state()
        self.notifier.notify_slot_buy(sid, symbol, order["price"], order["amount"], order["cost"], sum(1 for s in self.slots.values() if s.get("active")), self.max_allowed_slots, sig.reason)

    def _execute_exit(self, sid, sig):
        slot = self.slots[sid]
        symbol = slot.get("symbol") or self.config.trade_symbol
        order = self.client.execute_market_sell(symbol, slot["amount"])
        pnl = order["cost"] - slot["cost_usdt"]
        pnl_pct = ((order["price"] - slot["entry_price"]) / slot["entry_price"]) * 100.0
        self.realized_pnl_usdt += pnl
        self.slots[sid] = {"active": False, "slot_id": sid, "symbol": "", "entry_price": 0.0, "amount": 0.0, "cost_usdt": 0.0, "highest_price": 0.0, "stop_loss": 0.0, "take_profit": 0.0}
        self._save_state()
        self.notifier.notify_slot_sell(sid, symbol, order["price"], order["amount"], order["cost"], pnl_pct, pnl, sum(1 for s in self.slots.values() if s.get("active")), self.max_allowed_slots, self.realized_pnl_usdt, sig.reason)

if __name__ == "__main__":
    cfg = TradingConfig.load_from_env()
    bot = MexcMultiSlotBot(cfg)
    bot.start()
            """.trimIndent()
        ),
        PythonFileItem(
            name = "config.py",
            description = "Configuration exposing SLOT_SIZE_USDT, INITIAL_MAX_SLOTS, CASH_RESERVE_USDT, and spacing parameters.",
            badge = "Config",
            code = """
# config.py - Multi-Slot Trading Configuration
import os, logging
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class TradingConfig:
    mexc_api_key: str
    mexc_api_secret: str
    trade_symbols: list[str] = field(default_factory=lambda: ["SOL/USDT", "DOGE/USDT"])
    timeframe: str = "15m"
    poll_interval_seconds: int = 30
    log_level: str = "INFO"
    simulation_mode: bool = False
    max_slippage_pct: float = 0.005

    slot_size_usdt: float = 4.0
    initial_max_slots: int = 2
    cash_reserve_usdt: float = 2.0
    min_slot_price_diff_pct: float = 0.8

    bollinger_period: int = 20
    bollinger_std: float = 2.0
    rsi_period: int = 14
    rsi_oversold: float = 36.0
    rsi_overbought: float = 68.0
    ema_period: int = 20
    atr_period: int = 14

    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.03
    trailing_stop_activation_pct: float = 0.008
    trailing_stop_offset_pct: float = 0.003

    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    @property
    def trade_symbol(self) -> str:
        return self.trade_symbols[0] if self.trade_symbols else "SOL/USDT"

    @classmethod
    def load_from_env(cls) -> "TradingConfig":
        raw_symbol = os.getenv("TRADE_SYMBOL") or os.getenv("PAIR", "SOL/USDT,DOGE/USDT")
        pairs = [p.strip().upper() for p in raw_symbol.split(",") if p.strip()] or ["SOL/USDT", "DOGE/USDT"]
        slot_sz = float(os.getenv("SLOT_SIZE_USDT") or os.getenv("TRADE_AMOUNT_USDT", "4.0"))
        max_s = int(os.getenv("INITIAL_MAX_SLOTS") or os.getenv("MAX_OPEN_TRADES", "2"))
        return cls(
            mexc_api_key=os.getenv("MEXC_API_KEY", "").strip(),
            mexc_api_secret=os.getenv("MEXC_API_SECRET", "").strip(),
            trade_symbols=pairs,
            timeframe=os.getenv("TIMEFRAME", "1m").strip(),
            poll_interval_seconds=int(os.getenv("CHECK_INTERVAL_SECONDS") or os.getenv("POLL_INTERVAL_SECONDS", "15")),
            slot_size_usdt=slot_sz,
            initial_max_slots=max_s,
            cash_reserve_usdt=float(os.getenv("CASH_RESERVE_USDT", "2.0")),
            min_slot_price_diff_pct=float(os.getenv("MIN_SLOT_PRICE_DIFF_PCT", "0.8")),
            trailing_stop_activation_pct=float(os.getenv("TRAILING_STOP_ACTIVATION_PCT", "0.8")) / 100.0,
            trailing_stop_offset_pct=float(os.getenv("TRAILING_STOP_OFFSET_PCT", "0.3")) / 100.0,
            rsi_oversold=float(os.getenv("RSI_OVERSOLD", "36.0")),
            rsi_overbought=float(os.getenv("RSI_OVERBOUGHT", "68.0")),
            stop_loss_pct=float(os.getenv("STOP_LOSS_PERCENT", "2.0")) / 100.0,
            take_profit_pct=float(os.getenv("TAKE_PROFIT_PERCENT", "3.0")) / 100.0,
            simulation_mode=os.getenv("SIMULATION_MODE", "false").lower() == "true",
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
        )
            """.trimIndent()
        ),
        PythonFileItem(
            name = "exchange_client.py",
            description = "MEXC Spot CCXT client with fee parsing, rate-limiting, and error-resilient market execution.",
            badge = "CCXT Client",
            code = """
# exchange_client.py - CCXT MEXC Client
import ccxt

class MexcSpotClient:
    def __init__(self, api_key: str, api_secret: str, simulation_mode: bool = False):
        self.simulation_mode = simulation_mode
        self.exchange = ccxt.mexc({
            "apiKey": api_key, "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot", "adjustForTimeDifference": True}
        })

    def initialize(self):
        self.exchange.load_markets()

    def get_spot_balance(self, currency="USDT") -> float:
        bal = self.exchange.fetch_balance(params={"type": "spot"})
        return float(bal.get("free", {}).get(currency.upper(), 0.0))

    def get_ticker(self, symbol: str):
        return self.exchange.fetch_ticker(symbol)

    def fetch_closed_ohlcv(self, symbol: str, timeframe="15m", limit=100):
        ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit + 1)
        import pandas as pd
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        for col in ["open", "high", "low", "close", "volume"]: df[col] = df[col].astype(float)
        return df.iloc[:-1].copy().reset_index(drop=True)

    def execute_market_buy(self, symbol: str, usdt_amount: float, max_slippage_pct=0.005):
        order = self.exchange.create_order(symbol=symbol, type="market", side="buy", amount=None, params={"quoteOrderQty": usdt_amount})
        exec_price = float(order.get("price") or (usdt_amount / float(order.get("filled", 1.0))))
        return {"id": order.get("id"), "price": exec_price, "amount": float(order.get("filled", 0.0)), "cost": float(order.get("cost", usdt_amount))}

    def execute_market_sell(self, symbol: str, token_amount: float):
        order = self.exchange.create_order(symbol=symbol, type="market", side="sell", amount=token_amount)
        exec_price = float(order.get("price", 0.0))
        return {"id": order.get("id"), "price": exec_price, "amount": float(order.get("filled", token_amount)), "cost": float(order.get("cost", 0.0))}
            """.trimIndent()
        ),
        PythonFileItem(
            name = "notifier.py",
            description = "Threaded non-blocking Telegram alerts dispatcher for slot entries, isolated exits, and milestone scaling.",
            badge = "Telegram Alerts",
            code = """
# notifier.py - Multi-Slot Telegram Alerts
import threading, requests

class TelegramNotifier:
    def __init__(self, bot_token, chat_id):
        self.bot_token, self.chat_id, self.enabled = bot_token, chat_id, bool(bot_token and chat_id)

    def send(self, text):
        if not self.enabled: return
        threading.Thread(target=lambda: requests.post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
            timeout=8
        ), daemon=True).start()

    def notify_startup(self, cfg):
        self.send(f"🚀 <b>Multi-Slot Scalper Booted</b>\\nPair: {cfg.get('trade_symbol')}\\nSlots: {cfg.get('max_slots')}\\nSize: ${'$'}{cfg.get('slot_size_usdt')}")

    def notify_slot_buy(self, sid, sym, price, amt, cost, active, max_s, reason):
        self.send(f"🟢 <b>BUY: {sid.upper()}</b>\\n{sym} at ${'$'}{price:,.2f}\\nCost: ${'$'}{cost:.2f} USDT\\nSlots: {active}/{max_s}\\n<i>{reason}</i>")

    def notify_slot_sell(self, sid, sym, price, amt, cost, pnl_pct, pnl, active, max_s, tot_pnl, reason):
        icon = "💰" if pnl >= 0 else "🛑"
        self.send(f"{icon} <b>EXIT: {sid.upper()}</b>\\n{sym} at ${'$'}{price:,.2f}\\nPnL: {pnl_pct:+.2f}% (${'$'}{pnl:+.2f})\\nSlots left: {active}/{max_s}\\nTotal PnL: ${'$'}{tot_pnl:,.2f}")

    def notify_milestone_expansion(self, new_s, eq, free):
        self.send(f"🎉 <b>Compounding Milestone</b>: Unlocked {new_s} slots! Total Equity: ${'$'}{eq:.2f} USDT")

    def notify_error(self, title, details):
        self.send(f"⚠️ <b>ALERT: {title}</b>\\n<code>{details[:400]}</code>")

    def notify_shutdown(self, sig):
        self.send(f"🛑 <b>Shutdown</b>: {sig}")
            """.trimIndent()
        ),
        PythonFileItem(
            name = "Procfile",
            description = "Worker process declaration for Railway deployment.",
            badge = "Railway Config",
            code = "worker: python main.py"
        ),
        PythonFileItem(
            name = "requirements.txt",
            description = "Pinned dependencies for Python 3.11+ deployment.",
            badge = "Dependencies",
            code = """
ccxt>=4.2.0
pandas>=2.1.0
python-dotenv>=1.0.0
pydantic>=2.5.0
requests>=2.31.0
            """.trimIndent()
        ),
        PythonFileItem(
            name = "README.md",
            description = "Deployment instructions for Multi-Slot Scalper on Railway.",
            badge = "Docs",
            code = """
# MEXC Independent Multi-Slot Compounding Scalper
1. Push this repository to GitHub (wife_2)
2. Deploy to Railway as a 24/7 worker process
3. Add Environment Variables:
   - MEXC_API_KEY
   - MEXC_API_SECRET
   - TELEGRAM_BOT_TOKEN
   - TELEGRAM_CHAT_ID
   - TRADE_SYMBOL=BTC/USDT
   - SLOT_SIZE_USDT=4.0
   - INITIAL_MAX_SLOTS=2
   - CASH_RESERVE_USDT=2.0
   - MIN_SLOT_PRICE_DIFF_PCT=1.0
   - TRAILING_STOP_ACTIVATION_PCT=1.2
   - TRAILING_STOP_OFFSET_PCT=0.5
   - STOP_LOSS_PERCENT=2.0
   - TAKE_PROFIT_PERCENT=3.0
4. Automated compounding unlocks new $4.0 slots as equity increases!
            """.trimIndent()
        )
    )
}
