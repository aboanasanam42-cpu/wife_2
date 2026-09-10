package com.example.data

import com.example.model.PythonFileItem

object PythonRepoContent {
    private const val D = "$"

    val files: List<PythonFileItem> = listOf(
        PythonFileItem(
            name = "main.py",
            description = "24/7 background worker execution loop, signal handling (SIGTERM/SIGINT), position state recovery.",
            badge = "Worker Daemon",
            code = """
# main.py - MEXC 24/7 Spot Trading Engine
import os, sys, json, time, signal, traceback
from datetime import datetime, timezone
import ccxt

from config import TradingConfig, setup_logger
from notifier import TelegramNotifier
from strategy import SpotStrategy
from exchange_client import MexcSpotClient

logger = setup_logger("mexc_trader.main")
STATE_FILE = "bot_state.json"

class MexcSpotTradingBot:
    def __init__(self, config: TradingConfig):
        self.config = config
        self.is_running = True
        self.current_position = None
        self.notifier = TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id)
        self.strategy = SpotStrategy(
            rsi_period=config.rsi_period,
            rsi_oversold=config.rsi_oversold,
            rsi_overbought=config.rsi_overbought,
            ema_period=config.ema_period,
            stop_loss_pct=config.stop_loss_pct,
            take_profit_pct=config.take_profit_pct,
        )
        self.client = MexcSpotClient(
            api_key=config.mexc_api_key,
            api_secret=config.mexc_api_secret,
            simulation_mode=config.simulation_mode,
        )
        self._setup_signals()
        self._load_state()

    def _setup_signals(self):
        def handle_termination(signum, frame):
            sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
            logger.info(f"Termination signal ({sig_name}) received.")
            self.is_running = False
            self.notifier.notify_shutdown(sig_name)

        signal.signal(signal.SIGINT, handle_termination)
        signal.signal(signal.SIGTERM, handle_termination)

    def _load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    self.current_position = json.load(f)
            except Exception as e:
                logger.error(f"Error reading state file: {e}")

    def _save_state(self):
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.current_position, f, indent=2)

    def start(self):
        logger.info("Initializing MEXC Spot Trading Bot...")
        self.notifier.notify_startup({
            "trade_symbol": self.config.trade_symbol,
            "timeframe": self.config.timeframe,
            "trade_amount_usdt": self.config.trade_amount_usdt,
            "rsi_period": self.config.rsi_period,
            "ema_period": self.config.ema_period,
            "stop_loss_pct": self.config.stop_loss_pct,
            "take_profit_pct": self.config.take_profit_pct,
            "simulation_mode": self.config.simulation_mode,
        })
        self.client.initialize()

        consecutive_errors = 0
        while self.is_running:
            try:
                self._iteration()
                consecutive_errors = 0
            except (ccxt.NetworkError, ccxt.ExchangeError) as net_err:
                consecutive_errors += 1
                backoff = min(300, 10 * consecutive_errors)
                logger.warning(f"Network anomaly: {net_err}. Backing off {backoff}s...")
                time.sleep(backoff)
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Loop error: {e}")
                time.sleep(15)

            for _ in range(self.config.poll_interval_seconds):
                if not self.is_running:
                    break
                time.sleep(1)

    def _iteration(self):
        df = self.client.fetch_closed_ohlcv(self.config.trade_symbol, self.config.timeframe)
        df = self.strategy.calculate_indicators(df)
        signal = self.strategy.evaluate_signals(df, self.current_position)
        logger.info(f"Cycle: {self.config.trade_symbol} | Close: {signal.price} | RSI: {signal.rsi_value:.1f} | Signal: {signal.action}")
        if signal.action == "BUY" and self.current_position is None:
            self._execute_entry(signal)
        elif signal.action == "SELL" and self.current_position is not None:
            self._execute_exit(signal)

    def _execute_entry(self, signal):
        order = self.client.execute_market_buy(self.config.trade_symbol, self.config.trade_amount_usdt)
        self.current_position = {
            "active": True,
            "symbol": self.config.trade_symbol,
            "entry_price": order["price"],
            "amount": order["amount"],
            "cost_usdt": order["cost"],
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "order_id": order["id"],
            "stop_loss": signal.suggested_sl or (order["price"] * (1.0 - self.config.stop_loss_pct)),
            "take_profit": signal.suggested_tp or (order["price"] * (1.0 + self.config.take_profit_pct)),
        }
        self._save_state()
        self.notifier.notify_trade("BUY", self.config.trade_symbol, order["price"], order["amount"], order["cost"], signal.reason)

    def _execute_exit(self, signal):
        pos = self.current_position
        order = self.client.execute_market_sell(self.config.trade_symbol, pos["amount"])
        pnl = order["cost"] - pos["cost_usdt"]
        pnl_pct = ((order["price"] - pos["entry_price"]) / pos["entry_price"]) * 100.0
        self.current_position = None
        self._save_state()
        self.notifier.notify_trade("SELL", self.config.trade_symbol, order["price"], order["amount"], order["cost"], signal.reason, pnl_pct=pnl_pct, pnl_usdt=pnl)

if __name__ == "__main__":
    config = TradingConfig.load_from_env()
    bot = MexcSpotTradingBot(config)
    bot.start()
            """.trimIndent()
        ),
        PythonFileItem(
            name = "strategy.py",
            description = "Calculates RSI(14) & 20 EMA, evaluates trend filters, dynamic oversold/overbought, Stop-Loss & Take-Profit targets.",
            badge = "TA Strategy",
            code = """
# strategy.py - Technical Indicators & Signal Generation
import logging
from dataclasses import dataclass
from typing import Optional
import pandas as pd
import numpy as np

@dataclass
class TradeSignal:
    action: str  # BUY, SELL, HOLD
    price: float
    reason: str
    rsi_value: float
    ema_value: float
    suggested_sl: Optional[float] = None
    suggested_tp: Optional[float] = None

class SpotStrategy:
    def __init__(self, rsi_period=14, rsi_oversold=30.0, rsi_overbought=70.0, ema_period=20, stop_loss_pct=0.02, take_profit_pct=0.04):
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.ema_period = ema_period
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df[f"EMA_{self.ema_period}"] = df["close"].ewm(span=self.ema_period, adjust=False).mean()
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1.0/self.rsi_period, min_periods=self.rsi_period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0/self.rsi_period, min_periods=self.rsi_period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df[f"RSI_{self.rsi_period}"] = (100 - (100 / (1 + rs))).fillna(50.0)
        return df

    def evaluate_signals(self, df: pd.DataFrame, current_position: Optional[dict] = None) -> TradeSignal:
        latest = df.iloc[-1]
        previous = df.iloc[-2]
        close = float(latest["close"])
        rsi = float(latest[f"RSI_{self.rsi_period}"])
        prev_rsi = float(previous[f"RSI_{self.rsi_period}"])
        ema = float(latest[f"EMA_{self.ema_period}"])

        if current_position and current_position.get("active"):
            entry_price = float(current_position["entry_price"])
            sl = entry_price * (1.0 - self.stop_loss_pct)
            tp = entry_price * (1.0 + self.take_profit_pct)
            if close <= sl:
                return TradeSignal("SELL", close, f"Stop-Loss hit ({close:.2f} <= {sl:.2f})", rsi, ema)
            if close >= tp:
                return TradeSignal("SELL", close, f"Take-Profit hit ({close:.2f} >= {tp:.2f})", rsi, ema)
            if rsi >= self.rsi_overbought:
                return TradeSignal("SELL", close, f"RSI Overbought ({rsi:.1f})", rsi, ema)
            return TradeSignal("HOLD", close, "Holding active position", rsi, ema)

        if close >= ema and (rsi <= self.rsi_oversold or (prev_rsi <= self.rsi_oversold and rsi > prev_rsi)):
            return TradeSignal("BUY", close, "RSI Oversold + Price > 20 EMA", rsi, ema, close*(1-self.stop_loss_pct), close*(1+self.take_profit_pct))

        return TradeSignal("HOLD", close, "Awaiting valid trigger", rsi, ema)
            """.trimIndent()
        ),
        PythonFileItem(
            name = "exchange_client.py",
            description = "MEXC API handler via ccxt with rate limiting, candle fetching, order execution, and error handling.",
            badge = "CCXT Client",
            code = """
# exchange_client.py - Authenticated CCXT MEXC Spot Client
import ccxt
import pandas as pd

class MexcSpotClient:
    def __init__(self, api_key: str, api_secret: str, simulation_mode: bool = False):
        self.simulation_mode = simulation_mode
        self.exchange = ccxt.mexc({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"}
        })

    def initialize(self):
        self.exchange.load_markets()

    def get_spot_balance(self, currency="USDT"):
        balance = self.exchange.fetch_balance(params={"type": "spot"})
        return float(balance.get("free", {}).get(currency.upper(), 0.0))

    def fetch_closed_ohlcv(self, symbol, timeframe="15m", limit=100):
        ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit + 1)
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        return df.iloc[:-1].copy().reset_index(drop=True)

    def execute_market_buy(self, symbol, usdt_amount):
        order = self.exchange.create_order(
            symbol=symbol,
            type="market",
            side="buy",
            amount=None,
            params={"quoteOrderQty": usdt_amount}
        )
        return {
            "id": order["id"],
            "price": float(order.get("price", 0.0)),
            "amount": float(order.get("filled", 0.0)),
            "cost": float(order.get("cost", usdt_amount)),
        }

    def execute_market_sell(self, symbol, token_amount):
        order = self.exchange.create_order(
            symbol=symbol,
            type="market",
            side="sell",
            amount=token_amount
        )
        return {
            "id": order["id"],
            "price": float(order.get("price", 0.0)),
            "amount": float(order.get("filled", token_amount)),
            "cost": float(order.get("cost", 0.0)),
        }
            """.trimIndent()
        ),
        PythonFileItem(
            name = "config.py",
            description = "Environment variables validation, typed dataclass configuration, and logging setup.",
            badge = "Config & Env",
            code = """
# config.py - Environment & Configuration Validation
import os, logging
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class TradingConfig:
    mexc_api_key: str
    mexc_api_secret: str
    telegram_bot_token: Optional[str]
    telegram_chat_id: Optional[str]
    trade_symbol: str
    trade_amount_usdt: float
    timeframe: str
    poll_interval_seconds: int
    rsi_period: int
    rsi_oversold: float
    rsi_overbought: float
    ema_period: int
    stop_loss_pct: float
    take_profit_pct: float
    max_slippage_pct: float
    max_open_trades: int
    simulation_mode: bool
    log_level: str

    @classmethod
    def load_from_env(cls) -> "TradingConfig":
        sl = float(os.getenv("STOP_LOSS_PERCENT", "1.5")) / 100.0 if "STOP_LOSS_PERCENT" in os.environ else float(os.getenv("STOP_LOSS_PCT", "0.015"))
        tp = float(os.getenv("TAKE_PROFIT_PERCENT", "2.5")) / 100.0 if "TAKE_PROFIT_PERCENT" in os.environ else float(os.getenv("TAKE_PROFIT_PCT", "0.025"))
        poll_sec = int(os.getenv("CHECK_INTERVAL_SECONDS") or os.getenv("POLL_INTERVAL_SECONDS", "30"))

        return cls(
            mexc_api_key=os.getenv("MEXC_API_KEY", ""),
            mexc_api_secret=os.getenv("MEXC_API_SECRET", ""),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
            trade_symbol=os.getenv("TRADE_SYMBOL", "BTC/USDT"),
            trade_amount_usdt=float(os.getenv("TRADE_AMOUNT_USDT", "15.0")),
            timeframe=os.getenv("TIMEFRAME", "15m"),
            poll_interval_seconds=poll_sec,
            rsi_period=int(os.getenv("RSI_PERIOD", "14")),
            rsi_oversold=float(os.getenv("RSI_OVERSOLD", "30.0")),
            rsi_overbought=float(os.getenv("RSI_OVERBOUGHT", "70.0")),
            ema_period=int(os.getenv("EMA_PERIOD", "20")),
            stop_loss_pct=sl,
            take_profit_pct=tp,
            max_slippage_pct=float(os.getenv("MAX_SLIPPAGE_PCT", "0.005")),
            max_open_trades=int(os.getenv("MAX_OPEN_TRADES", "1")),
            simulation_mode=os.getenv("SIMULATION_MODE", "False").lower() in ("true", "1"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
            """.trimIndent()
        ),
        PythonFileItem(
            name = "notifier.py",
            description = "Threaded non-blocking Telegram alerts dispatcher for startup, order execution, and error notifications.",
            badge = "Telegram Alerts",
            code = """
# notifier.py - Threaded Telegram Notifications
import threading, logging, requests

class TelegramNotifier:
    def __init__(self, bot_token, chat_id):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)

    def send_message(self, text: str):
        if not self.enabled:
            return
        def _send():
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            requests.post(url, json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}, timeout=8)
        threading.Thread(target=_send, daemon=True).start()

    def notify_startup(self, cfg):
        self.send_message(f"MEXC Spot Bot Started | Symbol: {cfg.get('trade_symbol')} | Timeframe: {cfg.get('timeframe')}")

    def notify_trade(self, action, symbol, price, amount, cost_usdt, reason, fee=None, pnl_pct=None, pnl_usdt=None):
        tag = "BUY ORDER" if action.upper() == "BUY" else "SELL ORDER"
        pnl = f" | Realized PnL: {pnl_pct:+.2f}%" if pnl_pct is not None else ""
        self.send_message(f"[{tag}] {symbol} at price {price} (Cost: {cost_usdt} USDT). Reason: {reason}{pnl}")

    def notify_error(self, title, details):
        self.send_message(f"ALERT: {title} | {details[:400]}")

    def notify_shutdown(self, signal_name):
        self.send_message(f"Bot Shutting Down: {signal_name}")
            """.trimIndent()
        ),
        PythonFileItem(
            name = "requirements.txt",
            description = "Pinned dependencies for Python 3.11+ deployment.",
            badge = "Dependencies",
            code = """
ccxt>=4.2.0
pandas>=2.1.0
pandas_ta>=0.3.14b0
python-dotenv>=1.0.0
pydantic>=2.5.0
requests>=2.31.0
            """.trimIndent()
        ),
        PythonFileItem(
            name = "Procfile",
            description = "Worker process declaration for Railway deployment.",
            badge = "Railway Config",
            code = "worker: python main.py"
        ),
        PythonFileItem(
            name = "README.md",
            description = "Complete deployment guide, GitHub push instructions, and MEXC API setup.",
            badge = "Docs",
            code = """
# MEXC Spot Trading Bot
1. Push this repository to GitHub
2. Create project in Railway: 'Deploy from GitHub'
3. Add Environment Variables in Railway:
   - MEXC_API_KEY
   - MEXC_API_SECRET
   - TELEGRAM_BOT_TOKEN
   - TELEGRAM_CHAT_ID
   - TRADE_SYMBOL=BTC/USDT
   - TRADE_AMOUNT_USDT=15.0
4. Railway runs 'worker: python main.py' automatically 24/7!
            """.trimIndent()
        )
    )
}
