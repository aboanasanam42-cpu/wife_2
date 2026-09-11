package com.example.data

import com.example.model.PythonFileItem

object PythonRepoContent {
    val files = listOf(
        PythonFileItem(
            name = "strategy.py",
            description = "InfinityGridEngine: Geometric rebalancing keeping base asset USDT value constant without upper ceiling.",
            badge = "Infinity Grid",
            code = """
# strategy.py - Spot Infinity Grid Strategy
import logging
from dataclasses import dataclass

logger = logging.getLogger("mexc_trader.infinity_grid")

@dataclass
class GridSignal:
    action: str  # BUY, SELL, HOLD
    current_price: float
    last_rebalance_price: float
    target_value_usdt: float
    current_value_usdt: float
    price_change_pct: float
    order_usdt: float
    order_amount: float
    reason: str

class InfinityGridEngine:
    def __init__(self, grid_step_pct=0.01, lower_bound_price=50000.0, target_asset_value_usdt=10.0, min_order_cost_usdt=5.0):
        self.grid_step_pct = grid_step_pct
        self.lower_bound_price = lower_bound_price
        self.target_asset_value_usdt = target_asset_value_usdt
        self.min_order_cost_usdt = min_order_cost_usdt

    def evaluate(self, current_price: float, last_rebalance_price: float, base_asset_balance: float) -> GridSignal:
        if current_price <= 0:
            return GridSignal("HOLD", current_price, last_rebalance_price, self.target_asset_value_usdt, 0.0, 0.0, 0.0, 0.0, "Invalid price")

        if last_rebalance_price <= 0:
            last_rebalance_price = current_price

        current_value = base_asset_balance * current_price
        pct_change = ((current_price - last_rebalance_price) / last_rebalance_price) * 100.0
        sell_trigger = last_rebalance_price * (1.0 + self.grid_step_pct)
        buy_trigger = last_rebalance_price * (1.0 - self.grid_step_pct)

        # 1. UPWARD MOVEMENT (PROFIT RELEASE)
        if current_price >= sell_trigger:
            surplus_usdt = current_value - self.target_asset_value_usdt
            tokens_to_sell = min(surplus_usdt / current_price, base_asset_balance)
            if surplus_usdt >= self.min_order_cost_usdt and tokens_to_sell > 0:
                reason = f"Infinity Grid UP (+{pct_change:+.2f}%): Selling surplus ${'$'}{surplus_usdt:.2f} USDT"
                return GridSignal("SELL", current_price, last_rebalance_price, self.target_asset_value_usdt, current_value, pct_change, surplus_usdt, tokens_to_sell, reason)

        # 2. DOWNWARD MOVEMENT (DIP ACCUMULATION)
        if current_price <= buy_trigger:
            if current_price < self.lower_bound_price:
                reason = f"PROTECTION: Price ${'$'}{current_price:,.2f} < Floor ${'$'}{self.lower_bound_price:,.2f}. Buying paused."
                return GridSignal("HOLD", current_price, last_rebalance_price, self.target_asset_value_usdt, current_value, pct_change, 0.0, 0.0, reason)

            order_cost = max(self.target_asset_value_usdt - current_value, self.min_order_cost_usdt)
            tokens_to_buy = order_cost / current_price
            reason = f"Infinity Grid DIP ({pct_change:+.2f}%): Restoring target value with ${'$'}{order_cost:.2f} USDT"
            return GridSignal("BUY", current_price, last_rebalance_price, self.target_asset_value_usdt, current_value, pct_change, order_cost, tokens_to_buy, reason)

        return GridSignal("HOLD", current_price, last_rebalance_price, self.target_asset_value_usdt, current_value, pct_change, 0.0, 0.0, f"Holding in grid range (${'$'}{buy_trigger:,.2f} - ${'$'}{sell_trigger:,.2f})")
            """.trimIndent()
        ),
        PythonFileItem(
            name = "main.py",
            description = "24/7 Geometric rebalancing execution engine with persistent JSON state, graceful shutdown, and Telegram alerts.",
            badge = "Main Engine",
            code = """
# main.py - MEXC Spot Infinity Grid Worker
import os, sys, json, time, signal, logging, traceback
from datetime import datetime, timezone
import ccxt
from config import TradingConfig, setup_logger
from notifier import TelegramNotifier
from strategy import InfinityGridEngine, GridSignal
from exchange_client import MexcSpotClient

logger = setup_logger("mexc_trader.main")
STATE_FILE = "bot_state.json"

class MexcInfinityGridBot:
    def __init__(self, config: TradingConfig):
        self.config = config
        self.is_running = True
        self.last_rebalance_price = 0.0
        self.grid_level = 0
        self.realized_pnl_usdt = 0.0
        self.base_asset_balance = 0.0
        self.base_asset = config.trade_symbol.split("/")[0].upper()
        self.notifier = TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id)
        self.client = MexcSpotClient(config.mexc_api_key, config.mexc_api_secret, config.simulation_mode)
        self._setup_signals()

    def _setup_signals(self):
        def handle_term(signum, frame):
            self.is_running = False
            self._save_state()
            self.notifier.notify_shutdown("SIGTERM" if signum == signal.SIGTERM else "SIGINT")
        signal.signal(signal.SIGINT, handle_term)
        signal.signal(signal.SIGTERM, handle_term)

    def _load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r") as f:
                    d = json.load(f)
                    self.last_rebalance_price = float(d.get("last_rebalance_price", 0.0))
                    self.grid_level = int(d.get("grid_level", 0))
                    self.realized_pnl_usdt = float(d.get("realized_pnl_usdt", 0.0))
                    self.base_asset_balance = float(d.get("base_asset_balance", 0.0))
            except Exception as e:
                logger.error(f"Error reading state: {e}")

    def _save_state(self):
        with open(STATE_FILE, "w") as f:
            json.dump({
                "symbol": self.config.trade_symbol,
                "last_rebalance_price": self.last_rebalance_price,
                "grid_level": self.grid_level,
                "realized_pnl_usdt": self.realized_pnl_usdt,
                "base_asset_balance": self.base_asset_balance,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }, f, indent=2)

    def start(self):
        self.client.initialize()
        self._load_state()
        constraints = self.client.get_symbol_constraints(self.config.trade_symbol)
        self.grid_engine = InfinityGridEngine(
            grid_step_pct=self.config.grid_step_pct,
            lower_bound_price=self.config.lower_bound_price,
            target_asset_value_usdt=self.config.allocation_usdt,
            min_order_cost_usdt=constraints.get("min_cost", 5.0)
        )
        self._calibrate_initial_benchmark()

        while self.is_running:
            try:
                self._iteration()
            except Exception as e:
                logger.error(f"Iteration error: {e}")
                time.sleep(10)
            for _ in range(self.config.check_interval_seconds):
                if not self.is_running: break
                time.sleep(1)

    def _calibrate_initial_benchmark(self):
        ticker = self.client.get_ticker(self.config.trade_symbol)
        price = float(ticker.get("last") or ticker.get("ask"))
        if not self.config.simulation_mode:
            self.base_asset_balance = self.client.get_spot_balance(self.base_asset)
        if self.last_rebalance_price <= 0:
            self.last_rebalance_price = price
            self._save_state()

    def _iteration(self):
        ticker = self.client.get_ticker(self.config.trade_symbol)
        price = float(ticker.get("last") or ticker.get("ask"))
        if not self.config.simulation_mode:
            self.base_asset_balance = self.client.get_spot_balance(self.base_asset)

        sig = self.grid_engine.evaluate(price, self.last_rebalance_price, self.base_asset_balance)
        if sig.action == "SELL" and sig.order_amount > 0:
            order = self.client.execute_market_sell(self.config.trade_symbol, sig.order_amount)
            self.base_asset_balance = max(0.0, self.base_asset_balance - order["amount"])
            profit = max(0.0, order["cost"] - (order["amount"] * self.last_rebalance_price))
            self.realized_pnl_usdt += profit
            self.last_rebalance_price = order["price"]
            self.grid_level += 1
            self._save_state()
            self.notifier.notify_grid_execution("SELL", self.config.trade_symbol, order["price"], order["amount"], order["cost"], self.grid_level, self.realized_pnl_usdt, sig.reason)
        elif sig.action == "BUY" and sig.order_usdt > 0:
            order = self.client.execute_market_buy(self.config.trade_symbol, sig.order_usdt)
            self.base_asset_balance += order["amount"]
            self.last_rebalance_price = order["price"]
            self.grid_level -= 1
            self._save_state()
            self.notifier.notify_grid_execution("BUY", self.config.trade_symbol, order["price"], order["amount"], order["cost"], self.grid_level, self.realized_pnl_usdt, sig.reason)

if __name__ == "__main__":
    cfg = TradingConfig.load_from_env()
    bot = MexcInfinityGridBot(cfg)
    bot.start()
            """.trimIndent()
        ),
        PythonFileItem(
            name = "config.py",
            description = "Typed environment configuration exposing GRID_STEP_PERCENT, LOWER_BOUND_PRICE, ALLOCATION_USDT, and logging.",
            badge = "Config",
            code = """
# config.py - Infinity Grid Configuration
import os, sys, logging
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
    check_interval_seconds: int
    grid_step_pct: float
    lower_bound_price: float
    allocation_usdt: float
    max_slippage_pct: float
    simulation_mode: bool
    log_level: str

    @classmethod
    def load_from_env(cls) -> "TradingConfig":
        step_raw = float(os.getenv("GRID_STEP_PERCENT", "1.0"))
        step_pct = step_raw / 100.0 if step_raw >= 0.05 else step_raw
        alloc_raw = os.getenv("ALLOCATION_USDT") or os.getenv("TRADE_AMOUNT_USDT", "10.0")
        check_sec = int(os.getenv("CHECK_INTERVAL_SECONDS") or os.getenv("POLL_INTERVAL_SECONDS", "20"))
        return cls(
            mexc_api_key=os.getenv("MEXC_API_KEY", "").strip(),
            mexc_api_secret=os.getenv("MEXC_API_SECRET", "").strip(),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
            trade_symbol=os.getenv("TRADE_SYMBOL", "BTC/USDT").strip().upper(),
            check_interval_seconds=check_sec,
            grid_step_pct=step_pct,
            lower_bound_price=float(os.getenv("LOWER_BOUND_PRICE", "50000.0")),
            allocation_usdt=float(alloc_raw),
            max_slippage_pct=float(os.getenv("MAX_SLIPPAGE_PCT", "0.005")),
            simulation_mode=os.getenv("SIMULATION_MODE", "False").lower() in ("true", "1"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
            """.trimIndent()
        ),
        PythonFileItem(
            name = "exchange_client.py",
            description = "MEXC API handler via ccxt with lot precision (amount_to_precision), quoteOrderQty market buying, and balance tracking.",
            badge = "CCXT Client",
            code = """
# exchange_client.py - CCXT MEXC Spot Client with Lot Precision
import ccxt

class MexcSpotClient:
    def __init__(self, api_key: str, api_secret: str, simulation_mode: bool = False):
        self.simulation_mode = simulation_mode
        self.exchange = ccxt.mexc({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot", "adjustForTimeDifference": True}
        })

    def initialize(self):
        self.exchange.load_markets()

    def get_spot_balance(self, currency="USDT") -> float:
        balance = self.exchange.fetch_balance(params={"type": "spot"})
        return float(balance.get("free", {}).get(currency.upper(), 0.0))

    def get_ticker(self, symbol: str):
        return self.exchange.fetch_ticker(symbol)

    def get_symbol_constraints(self, symbol: str):
        market = self.exchange.market(symbol) if symbol in self.exchange.markets else {}
        limits = market.get("limits", {})
        return {
            "min_cost": float(limits.get("cost", {}).get("min", 5.0) or 5.0),
            "min_amount": float(limits.get("amount", {}).get("min", 0.0001) or 0.0001),
            "amount_precision": int(market.get("precision", {}).get("amount", 6) or 6),
        }

    def execute_market_buy(self, symbol: str, usdt_amount: float):
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
            "fee": order.get("fee", {}).get("cost", 0.0)
        }

    def execute_market_sell(self, symbol: str, token_amount: float):
        constraints = self.get_symbol_constraints(symbol)
        prec = constraints["amount_precision"]
        formatted_amount = float(f"{token_amount:.{prec}f}")
        order = self.exchange.create_order(
            symbol=symbol,
            type="market",
            side="sell",
            amount=formatted_amount
        )
        return {
            "id": order["id"],
            "price": float(order.get("price", 0.0)),
            "amount": float(order.get("filled", formatted_amount)),
            "cost": float(order.get("cost", 0.0)),
            "fee": order.get("fee", {}).get("cost", 0.0)
        }
            """.trimIndent()
        ),
        PythonFileItem(
            name = "notifier.py",
            description = "Threaded non-blocking Telegram alerts dispatcher for grid executions, startups, and shutdowns.",
            badge = "Telegram Alerts",
            code = """
# notifier.py - Threaded Telegram Notifications for Infinity Grid
import threading, requests

class TelegramNotifier:
    def __init__(self, bot_token, chat_id):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)

    def send_message(self, text: str):
        if not self.enabled: return
        def _send():
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            requests.post(url, json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}, timeout=8)
        threading.Thread(target=_send, daemon=True).start()

    def notify_startup(self, cfg):
        self.send_message(f"♾️ <b>MEXC Infinity Grid Started</b>\\nSymbol: <code>{cfg.get('trade_symbol')}</code>\\nStep: {cfg.get('grid_step_pct', 1.0)}%\\nFloor: ${'$'}{cfg.get('lower_bound_price', 0):,.2f}")

    def notify_grid_execution(self, action, symbol, price, amount, cost, level, pnl, reason, fee=None):
        tag = "🟢 GRID BUY (DIP)" if action == "BUY" else "💰 GRID SELL (PROFIT)"
        self.send_message(f"<b>{tag}</b>\\n{symbol} at ${'$'}{price:,.4f}\\nVolume: ${'$'}{cost:.2f} USDT\\nLevel: #{level}\\nPnL: +${'$'}{pnl:.2f} USDT\\n<i>{reason}</i>")

    def notify_error(self, title, details):
        self.send_message(f"⚠️ <b>ALERT: {title}</b>\\n<code>{details[:400]}</code>")

    def notify_shutdown(self, signal_name):
        self.send_message(f"🛑 <b>Bot Shutting Down</b>: {signal_name}")
            """.trimIndent()
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
# MEXC Spot Infinity Grid Bot
1. Push this repository to GitHub
2. Deploy to Railway as a 24/7 background worker
3. Add Environment Variables:
   - MEXC_API_KEY
   - MEXC_API_SECRET
   - TELEGRAM_BOT_TOKEN
   - TELEGRAM_CHAT_ID
   - TRADE_SYMBOL=BTC/USDT
   - GRID_STEP_PERCENT=1.0
   - LOWER_BOUND_PRICE=50000.0
   - ALLOCATION_USDT=10.0
   - CHECK_INTERVAL_SECONDS=20
4. Automatically maintains target portfolio value without upper ceiling!
            """.trimIndent()
        )
    )
}
