"""
MEXC 24/7 Automated Spot Trading Bot - Main Execution Engine.
Runs continuous background worker loop with signal handling, state persistence,
Dynamic Volatility Scalping (Bollinger Bands + RSI + ATR), Trailing Take-Profit engine,
and Telegram notifications.
"""

import logging
import os
import sys
import json
import time
import signal
import logging
import traceback
from datetime import datetime, timezone
import ccxt

from config import TradingConfig, setup_logger
from notifier import TelegramNotifier
from strategy import SpotStrategy, TradeSignal
from exchange_client import MexcSpotClient

# Initialize structured logging
logger = setup_logger("mexc_trader.main")

STATE_FILE = "bot_state.json"


class MexcSpotTradingBot:
    def __init__(self, config: TradingConfig):
        self.config = config
        self.is_running = True
        self.current_position = None

        # Configure logging level dynamically
        logger.setLevel(getattr(logging, config.log_level, logging.INFO))

        # Helpers
        self.notifier = TelegramNotifier(
            bot_token=config.telegram_bot_token,
            chat_id=config.telegram_chat_id,
        )
        self.strategy = SpotStrategy(
            bollinger_period=config.bollinger_period,
            bollinger_std=config.bollinger_std,
            rsi_period=config.rsi_period,
            rsi_oversold=config.rsi_oversold,
            rsi_overbought=config.rsi_overbought,
            atr_period=config.atr_period,
            atr_multiplier_sl=config.atr_multiplier_sl,
            stop_loss_pct=config.stop_loss_pct,
            take_profit_pct=config.take_profit_pct,
            trailing_stop_activation_pct=config.trailing_stop_activation_pct,
            trailing_stop_offset_pct=config.trailing_stop_offset_pct,
            ema_period=config.ema_period,
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
                            "Recovered active position from state file: Symbol=%s, Entry=$%.4f, Qty=%.6f, Highest=$%.4f",
                            self.current_position.get("symbol"),
                            self.current_position.get("entry_price"),
                            self.current_position.get("amount"),
                            self.current_position.get("highest_price_seen", self.current_position.get("entry_price")),
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
        logger.info("Initializing MEXC Spot Trading Bot (Dynamic Volatility Scalping)...")
        logger.info(
            "Trading Pair: %s | Timeframe: %s | Amount: $%.2f USDT | Bollinger: (%d, %.1f) | ATR: %d",
            self.config.trade_symbol,
            self.config.timeframe,
            self.config.trade_amount_usdt,
            self.config.bollinger_period,
            self.config.bollinger_std,
            self.config.atr_period,
        )

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
                consecutive_errors = 0

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
                        "Exchange Connectivity Warning",
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

    def _update_trailing_stop(self, current_price: float, atr_value: float):
        """
        Updates the trailing take-profit engine for an active position:
        1. Tracks highest_price_seen.
        2. Once price reaches +activation_pct (e.g. +1.0%), lock stop-loss at Break-Even (Entry + 0.2% fees).
        3. If price climbs higher, trail the SL floor behind highest price by offset_pct or ATR.
        """
        if not self.current_position or not self.current_position.get("active"):
            return

        entry_price = float(self.current_position["entry_price"])
        highest_price = float(self.current_position.get("highest_price_seen", entry_price))
        current_sl = float(self.current_position.get("stop_loss", entry_price * (1.0 - self.config.stop_loss_pct)))
        trailing_active = bool(self.current_position.get("trailing_active", False))

        # Check if new high was reached
        if current_price > highest_price:
            highest_price = current_price
            self.current_position["highest_price_seen"] = highest_price

        # Check activation threshold (+1.0% above entry by default)
        activation_price = entry_price * (1.0 + self.config.trailing_stop_activation_pct)

        if not trailing_active and highest_price >= activation_price:
            # Activate trailing stop! Move SL to break-even + 0.2% buffer for fees
            break_even_sl = entry_price * 1.002
            if break_even_sl > current_sl:
                current_sl = break_even_sl
                self.current_position["trailing_active"] = True
                self.current_position["stop_loss"] = current_sl
                logger.info(
                    "🎯 Trailing Stop Activated! Price reached $%.2f (+%.2f%%). SL locked at Break-Even: $%.2f",
                    highest_price,
                    ((highest_price - entry_price) / entry_price) * 100.0,
                    current_sl,
                )
                self._save_state()

        if self.current_position.get("trailing_active", False):
            # Dynamic trailing floor: highest_price - (offset_pct * highest_price)
            # or 1 * ATR behind peak
            offset_price = highest_price * (1.0 - self.config.trailing_stop_offset_pct)
            atr_trailing = highest_price - atr_value

            new_trail_floor = max(offset_price, atr_trailing)

            # Trailing stop only moves UPWARD, never downward
            if new_trail_floor > current_sl:
                old_sl = current_sl
                current_sl = new_trail_floor
                self.current_position["stop_loss"] = current_sl
                logger.info(
                    "📈 Trailing SL Ratchet UP: $%.2f -> $%.2f (Peak: $%.2f, Lock Profit: +%.2f%%)",
                    old_sl,
                    current_sl,
                    highest_price,
                    ((current_sl - entry_price) / entry_price) * 100.0,
                )
                self._save_state()

    def _iteration(self):
        """Single loop execution: fetch candles -> calculate TA -> update trailing -> evaluate signal -> execute."""
        symbol = self.config.trade_symbol

        # 1. Fetch closed OHLCV candles
        df = self.client.fetch_closed_ohlcv(
            symbol=symbol,
            timeframe=self.config.timeframe,
            limit=100,
        )

        # 2. Calculate Technical Indicators (Bollinger Bands, %B, RSI, ATR)
        df_indicators = self.strategy.calculate_indicators(df)

        latest_close = float(df_indicators["close"].iloc[-1])
        latest_atr = float(df_indicators["ATR"].iloc[-1]) if not pd.isna(df_indicators["ATR"].iloc[-1]) else (latest_close * 0.01)

        # 3. Dynamic Trailing Stop Engine Update
        if self.current_position and self.current_position.get("active"):
            self._update_trailing_stop(latest_close, latest_atr)

        # 4. Strategy Signal Evaluation
        signal = self.strategy.evaluate_signals(
            df=df_indicators,
            current_position=self.current_position,
        )

        logger.info(
            "Cycle: %s | Close: $%.2f | %%B: %.2f | RSI: %.1f | ATR: $%.2f | Signal: %s (%s)",
            symbol,
            signal.price,
            signal.bb_pct_b,
            signal.rsi_value,
            signal.atr_value,
            signal.action,
            signal.reason,
        )

        # 5. Order Execution
        if signal.action == "BUY" and self.current_position is None:
            self._execute_entry(signal)
        elif signal.action == "SELL" and self.current_position is not None:
            self._execute_exit(signal)

    def _execute_entry(self, signal: TradeSignal):
        """Places Market BUY order on dynamic dip and initializes position state with ATR SL."""
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

        initial_sl = signal.suggested_sl or (exec_price - (1.5 * signal.atr_value))
        initial_tp = signal.suggested_tp or (exec_price * (1.0 + self.config.take_profit_pct))

        self.current_position = {
            "active": True,
            "symbol": symbol,
            "entry_price": exec_price,
            "highest_price_seen": exec_price,
            "amount": amount_tokens,
            "cost_usdt": cost_usdt,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "order_id": order["id"],
            "stop_loss": initial_sl,
            "take_profit": initial_tp,
            "trailing_active": False,
            "atr_at_entry": signal.atr_value,
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

    def _execute_exit(self, signal: TradeSignal):
        """Places Market SELL order, calculates realized PnL, and clears persistent position state."""
        symbol = self.config.trade_symbol
        pos = self.current_position
        tokens_to_sell = pos["amount"]
        entry_price = float(pos["entry_price"])
        cost_usdt = float(pos["cost_usdt"])

        logger.info("Executing SELL for %s: %.6f tokens. Reason: %s", symbol, tokens_to_sell, signal.reason)

        order = self.client.execute_market_sell(
            symbol=symbol,
            token_amount=tokens_to_sell,
            max_slippage_pct=self.config.max_slippage_pct,
        )

        exit_price = order["price"]
        proceeds_usdt = order["cost"]
        fee = order.get("fee")

        pnl_usdt = proceeds_usdt - cost_usdt
        pnl_pct = (pnl_usdt / cost_usdt) * 100.0 if cost_usdt > 0 else 0.0

        logger.info(
            "Position Closed: Entry=$%.4f, Exit=$%.4f, PnL=%+.2f USDT (%+.2f%%)",
            entry_price,
            exit_price,
            pnl_usdt,
            pnl_pct,
        )

        # Clear state file
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
