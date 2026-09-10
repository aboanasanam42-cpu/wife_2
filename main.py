"""
MEXC 24/7 Automated Spot Trading Bot - Enhanced Volatility Scalper
"""

import logging
import os
import sys
import json
import time
import signal
import traceback
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

        logger.setLevel(getattr(logging, config.log_level, logging.INFO))
        self.notifier = TelegramNotifier(
            bot_token=config.telegram_bot_token,
            chat_id=config.telegram_chat_id,
        )
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
            logger.info("Signal (%s) received. Exiting...", sig_name)
            self.is_running = False
            self.notifier.notify_shutdown(sig_name)

        signal.signal(signal.SIGINT, handle_termination)
        signal.signal(signal.SIGTERM, handle_termination)

    def _load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    self.current_position = json.load(f)
                    if not (self.current_position and self.current_position.get("active")):
                        self.current_position = None
            except Exception as e:
                logger.error("Error reading state: %s", e)
                self.current_position = None

    def _save_state(self):
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.current_position, f, indent=2)
        except Exception as e:
            logger.error("Failed saving state: %s", e)

    def _update_trailing_stops(self, current_price: float):
        """Updates trailing take profit floor dynamically."""
        if not self.current_position or not self.current_position.get("active"):
            return

        entry_price = float(self.current_position["entry_price"])
        highest_price = float(self.current_position.get("highest_price", entry_price))

        if current_price > highest_price:
            highest_price = current_price
            self.current_position["highest_price"] = highest_price

        gain_pct = (highest_price - entry_price) / entry_price

        # Check if trailing activation threshold reached
        if gain_pct >= self.config.trailing_stop_activation_pct:
            new_trailing_floor = highest_price * (1.0 - self.config.trailing_stop_offset_pct)
            current_sl = float(self.current_position.get("stop_loss", 0.0))

            if new_trailing_floor > current_sl:
                self.current_position["stop_loss"] = new_trailing_floor
                logger.info(
                    "Trailing Stop Raised: $%.2f (Highest: $%.2f, Gain: +%.2f%%)",
                    new_trailing_floor, highest_price, gain_pct * 100
                )
                self._save_state()

    def start(self):
        logger.info("Initializing MEXC Scalping Bot (BB + RSI + Trailing TP)...")
        logger.info("Pair: %s | Timeframe: %s | Amount: $%.2f",
                    self.config.trade_symbol, self.config.timeframe, self.config.trade_amount_usdt)

        try:
            self.client.initialize()
        except Exception as e:
            logger.error("Initialization failed: %s", e)
            if not self.config.simulation_mode:
                sys.exit(1)

        while self.is_running:
            try:
                self._iteration()
            except (ccxt.NetworkError, ccxt.ExchangeError) as net_err:
                logger.warning("Exchange error: %s. Retrying in 10s...", net_err)
                time.sleep(10)
            except Exception as e:
                logger.error("Unexpected error: %s\n%s", e, traceback.format_exc())
                time.sleep(15)

            for _ in range(self.config.poll_interval_seconds):
                if not self.is_running:
                    break
                time.sleep(1)

    def _iteration(self):
        symbol = self.config.trade_symbol
        df = self.client.fetch_closed_ohlcv(symbol=symbol, timeframe=self.config.timeframe, limit=100)
        df_indicators = self.strategy.calculate_indicators(df)
        
        current_price = float(df_indicators.iloc[-1]["close"])
        self._update_trailing_stops(current_price)

        signal_res = self.strategy.evaluate_signals(df=df_indicators, current_position=self.current_position)

        logger.info(
            "[%s] Close: $%.2f | %%B: %.2f | RSI: %.1f | Action: %s (%s)",
            symbol, signal_res.price, signal_res.percent_b, signal_res.rsi_value, signal_res.action, signal_res.reason
        )

        if signal_res.action == "BUY" and self.current_position is None:
            self._execute_entry(signal_res)
        elif signal_res.action == "SELL" and self.current_position is not None:
            self._execute_exit(signal_res)

    def _execute_entry(self, signal_res):
        symbol = self.config.trade_symbol
        amount = self.config.trade_amount_usdt
        logger.info("Placing Market BUY for %s: $%.2f", symbol, amount)

        order = self.client.execute_market_buy(symbol=symbol, usdt_amount=amount, max_slippage_pct=self.config.max_slippage_pct)
        exec_price = order["price"]

        self.current_position = {
            "active": True,
            "symbol": symbol,
            "entry_price": exec_price,
            "highest_price": exec_price,
            "amount": order["amount"],
            "cost_usdt": order["cost"],
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "order_id": order["id"],
            "stop_loss": signal_res.suggested_sl or (exec_price * (1.0 - self.config.stop_loss_pct)),
            "take_profit": signal_res.suggested_tp or (exec_price * (1.0 + self.config.take_profit_pct)),
        }
        self._save_state()
        self.notifier.notify_trade(action="BUY", symbol=symbol, price=exec_price, amount=order["amount"], cost_usdt=order["cost"], reason=signal_res.reason)

    def _execute_exit(self, signal_res):
        symbol = self.config.trade_symbol
        pos = self.current_position
        logger.info("Placing Market SELL for %s: %.6f tokens", symbol, pos["amount"])

        order = self.client.execute_market_sell(symbol=symbol, token_amount=pos["amount"])
        exit_price = order["price"]
        pnl_usdt = order["cost"] - pos["cost_usdt"]
        pnl_pct = ((exit_price - pos["entry_price"]) / pos["entry_price"]) * 100.0

        logger.info("Closed Position. PnL: %+.2f USDT (%+.2f%%)", pnl_usdt, pnl_pct)

        self.current_position = None
        self._save_state()
        self.notifier.notify_trade(action="SELL", symbol=symbol, price=exit_price, amount=order["amount"], cost_usdt=order["cost"], reason=signal_res.reason, pnl_pct=pnl_pct, pnl_usdt=pnl_usdt)

if __name__ == "__main__":
    try:
        config = TradingConfig.load_from_env()
    except Exception as e:
        logger.error("Configuration Error: %s", e)
        sys.exit(1)

    bot = MexcSpotTradingBot(config)
    bot.start()
