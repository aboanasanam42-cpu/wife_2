"""
MEXC 24/7 Automated Spot Trading Bot - Main Execution Engine.
Runs continuous background worker loop with signal handling, state persistence,
dynamic RSI + EMA strategy evaluation, Stop-Loss / Take-Profit tracking, and Telegram alerts.
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

# Initialize structured logging
logger = setup_logger("mexc_trader.main")

STATE_FILE = "bot_state.json"


class MexcSpotTradingBot:
    def __init__(self, config: TradingConfig):
        self.config = config
        self.is_running = True
        self.current_position = None

        # Load helpers
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
        """Registers OS signal handlers for graceful shutdown on Railway (SIGINT / SIGTERM)."""
        def handle_termination(signum, frame):
            sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
            logger.info("Termination signal (%s) received. Initiating graceful shutdown...", sig_name)
            self.is_running = False
            self.notifier.notify_shutdown(sig_name)

        signal.signal(signal.SIGINT, handle_termination)
        signal.signal(signal.SIGTERM, handle_termination)

    def _load_state(self):
        """Loads active trading position from local persistent JSON file."""
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    self.current_position = json.load(f)
                    if self.current_position and self.current_position.get("active"):
                        logger.info(
                            "Recovered active position from state file: Symbol=%s, Entry=$%.4f, Qty=%.6f",
                            self.current_position.get("symbol"),
                            self.current_position.get("entry_price"),
                            self.current_position.get("amount"),
                        )
                    else:
                        self.current_position = None
            except Exception as e:
                logger.error("Error reading state file '%s': %s", STATE_FILE, e)
                self.current_position = None
        else:
            self.current_position = None

    def _save_state(self):
        """Saves current position to disk."""
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.current_position, f, indent=2)
        except Exception as e:
            logger.error("Failed to save state to '%s': %s", STATE_FILE, e)

    def start(self):
        """Main entry method for the 24/7 background worker."""
        logger.info("Initializing MEXC Spot Trading Bot...")
        logger.info("Trading Pair: %s | Timeframe: %s | Amount: $%.2f USDT",
                    self.config.trade_symbol, self.config.timeframe, self.config.trade_amount_usdt)

        # Notify Telegram on startup
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

        # Connect to MEXC Spot
        try:
            self.client.initialize()
        except Exception as e:
            logger.error("Initial exchange connection failed: %s", e)
            self.notifier.notify_error("Exchange Initialization Failure", str(e))
            if not self.config.simulation_mode:
                logger.error("Exiting due to initialization failure in live trading mode.")
                sys.exit(1)

        # Main 24/7 Trading Loop
        consecutive_errors = 0
        last_heartbeat_time = time.time()

        while self.is_running:
            try:
                self._iteration()
                consecutive_errors = 0  # Reset error count on successful iteration

                # Periodic heartbeat log every 1 hour
                if time.time() - last_heartbeat_time > 3600:
                    logger.info("Heartbeat: Bot active and monitoring %s on MEXC Spot.", self.config.trade_symbol)
                    last_heartbeat_time = time.time()

            except (ccxt.NetworkError, ccxt.ExchangeError) as net_err:
                consecutive_errors += 1
                backoff = min(60 * 5, 10 * consecutive_errors)
                logger.warning(
                    "Network/Exchange anomaly: %s. Backing off for %d seconds (Attempt %d)...",
                    net_err,
                    backoff,
                    consecutive_errors,
                )
                if consecutive_errors == 3:
                    self.notifier.notify_error(
                        "Repeated MEXC API Glitch",
                        f"Failed 3 times in a row: {net_err}. Backing off {backoff}s.",
                    )
                time.sleep(backoff)
                continue

            except Exception as unhandled:
                consecutive_errors += 1
                tb = traceback.format_exc()
                logger.error("Unhandled exception in trading cycle: %s\n%s", unhandled, tb)
                self.notifier.notify_error("Unhandled Engine Exception", f"{unhandled}\n\n{tb[-500:]}")
                time.sleep(15)

            # Sleep between evaluation cycles
            for _ in range(self.config.poll_interval_seconds):
                if not self.is_running:
                    break
                time.sleep(1)

        logger.info("Shutdown sequence finished. Exiting worker process.")

    def _iteration(self):
        """Single loop execution: fetch candles -> calculate TA -> generate signal -> execute orders."""
        symbol = self.config.trade_symbol

        # 1. Fetch closed OHLCV candles
        df = self.client.fetch_closed_ohlcv(
            symbol=symbol,
            timeframe=self.config.timeframe,
            limit=100,
        )

        # 2. Calculate Indicators (RSI, 20 EMA)
        df_indicators = self.strategy.calculate_indicators(df)

        # 3. Evaluate Strategy Signal
        signal = self.strategy.evaluate_signals(
            df=df_indicators,
            current_position=self.current_position,
        )

        logger.info(
            "Cycle: %s | Close: $%.2f | RSI: %.1f | 20 EMA: $%.2f | Signal: %s (%s)",
            symbol,
            signal.price,
            signal.rsi_value,
            signal.ema_value,
            signal.action,
            signal.reason,
        )

        # 4. Handle Execution
        if signal.action == "BUY" and self.current_position is None:
            self._execute_entry(signal)
        elif signal.action == "SELL" and self.current_position is not None:
            self._execute_exit(signal)

    def _execute_entry(self, signal):
        """Places Market BUY order and stores position state."""
        symbol = self.config.trade_symbol
        amount_usdt = self.config.trade_amount_usdt
        logger.info("Executing BUY for %s: $%.2f USDT. Reason: %s", symbol, amount_usdt, signal.reason)

        order = self.client.execute_market_buy(
            symbol=symbol,
            usdt_amount=amount_usdt,
            max_slippage_pct=self.config.max_slippage_pct,
        )

        exec_price = order["price"]
        amount_tokens = order["amount"]
        cost_usdt = order["cost"]
        fee = order.get("fee")

        self.current_position = {
            "active": True,
            "symbol": symbol,
            "entry_price": exec_price,
            "amount": amount_tokens,
            "cost_usdt": cost_usdt,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "order_id": order["id"],
            "stop_loss": signal.suggested_sl or (exec_price * (1.0 - self.config.stop_loss_pct)),
            "take_profit": signal.suggested_tp or (exec_price * (1.0 + self.config.take_profit_pct)),
        }
        self._save_state()

        self.notifier.notify_trade(
            action="BUY",
            symbol=symbol,
            price=exec_price,
            amount=amount_tokens,
            cost_usdt=cost_usdt,
            reason=signal.reason,
            fee=fee,
        )

    def _execute_exit(self, signal):
        """Places Market SELL order, calculates realized PnL, and clears position state."""
        symbol = self.config.trade_symbol
        pos = self.current_position
        tokens_to_sell = pos["amount"]
        entry_price = pos["entry_price"]

        logger.info("Executing SELL for %s: %.6f tokens. Reason: %s", symbol, tokens_to_sell, signal.reason)

        order = self.client.execute_market_sell(
            symbol=symbol,
            token_amount=tokens_to_sell,
        )

        exit_price = order["price"]
        proceeds_usdt = order["cost"]
        fee = order.get("fee")

        # Compute realized PnL
        cost_usdt = pos["cost_usdt"]
        pnl_usdt = proceeds_usdt - cost_usdt
        pnl_pct = ((exit_price - entry_price) / entry_price) * 100.0

        logger.info("Position Closed. PnL: %+.2f USDT (%+.2f%%)", pnl_usdt, pnl_pct)

        # Clear state
        self.current_position = None
        self._save_state()

        self.notifier.notify_trade(
            action="SELL",
            symbol=symbol,
            price=exit_price,
            amount=tokens_to_sell,
            cost_usdt=proceeds_usdt,
            reason=signal.reason,
            fee=fee,
            pnl_pct=pnl_pct,
            pnl_usdt=pnl_usdt,
        )


if __name__ == "__main__":
    try:
        config = TradingConfig.load_from_env()
    except Exception as e:
        logger.error("Configuration Validation Error: %s", e)
        sys.exit(1)

    bot = MexcSpotTradingBot(config)
    bot.start()
