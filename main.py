"""
MEXC 24/7 Automated Spot Trading Bot - Multi-Pair Multi-Slot Compounding Engine.
Production-ready core execution engine for Railway (wife_2).

Key Features:
1. Multi-Pair Multi-Slot Scanner: Concurrently monitors configured trading pairs (e.g. SOL/USDT, DOGE/USDT)
   and allocates independent $4.00 USDT slots to whichever asset triggers a valid dip signal first.
2. Slot Decoupling: Persistent state in bot_state.json with independent order execution and symbol tracking per slot.
3. Auto-Compounding Scaling: Dynamically uncaps slot_3, slot_4, ... based on total portfolio equity.
4. Execution Precision & Minimums: Formats quantity via exchange.amount_to_precision(symbol, amount).
5. Anti-Clustering Decoupling: Prevents duplicate entries within MIN_SLOT_PRICE_DIFF_PCT for the same asset.
6. Continuous 24/7 Resilience: Signal handling (SIGTERM/SIGINT) and CCXT network/exchange error recovery.
"""

import os
import sys
import json
import time
import signal
import logging
import traceback
import math
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import ccxt

from config import TradingConfig, setup_logger
from notifier import TelegramNotifier
from strategy import SpotStrategy, SignalResult
from exchange_client import MexcSpotClient

logger = setup_logger("mexc_trader.main")
STATE_FILE = "bot_state.json"


def format_token_price(price: float) -> str:
    """Formats price string nicely, using more decimal places for sub-cent tokens."""
    if price >= 1.0:
        return f"${price:,.4f}"
    elif price >= 0.001:
        return f"${price:.6f}"
    else:
        return f"${price:.10f}".rstrip("0").rstrip(".")


class HealthCheckHandler(BaseHTTPRequestHandler):
    """Responds to Railway HTTP health probes to confirm service vitality."""
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        payload = json.dumps({
            "status": "healthy",
            "service": "MAROAH MEXC Multi-Slot Bot",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self.wfile.write(payload.encode("utf-8"))

    def log_message(self, format, *args):
        pass  # Suppress repetitive access log entries


def start_health_check_server():
    """Starts background HTTP server if PORT environment variable is configured (Railway default)."""
    port_raw = os.getenv("PORT")
    if not port_raw:
        return
    try:
        port = int(port_raw)
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        logger.info("Railway HTTP health-check server listening on 0.0.0.0:%d", port)
        server.serve_forever()
    except Exception as exc:
        logger.warning("Could not launch HTTP health-check server on port %s: %s", port_raw, exc)


class MexcMultiSlotBot:
    def __init__(self, config: TradingConfig):
        self.config = config
        self.is_running = True

        logger.setLevel(getattr(logging, config.log_level, logging.INFO))

        # Core Multi-Slot State Structure:
        # {
        #   "slot_1": {"active": False, "slot_id": "slot_1", "symbol": "", "entry_price": 0.0, "amount": 0.0, ...},
        #   "slot_2": ...
        # }
        self.slots: Dict[str, Dict[str, Any]] = {}
        self.max_allowed_slots: int = config.initial_max_slots
        self.realized_pnl_usdt: float = 0.0
        self.total_trades_count: int = 0

        # Subsystems
        self.notifier = TelegramNotifier(
            bot_token=config.telegram_bot_token,
            chat_id=config.telegram_chat_id,
        )
        self.client = MexcSpotClient(
            api_key=config.mexc_api_key,
            api_secret=config.mexc_api_secret,
            simulation_mode=config.simulation_mode,
        )

        self.strategy = SpotStrategy(
            rsi_period=config.rsi_period,
            rsi_oversold=config.rsi_oversold,
            rsi_overbought=config.rsi_overbought,
            ema_period=config.ema_period,
            bollinger_period=config.bollinger_period,
            bollinger_std=config.bollinger_std,
            bollinger_b_entry=config.bollinger_b_entry,
            atr_period=config.atr_period,
            stop_loss_pct=config.stop_loss_pct,
            take_profit_pct=config.take_profit_pct,
            min_slot_price_diff_pct=config.min_slot_price_diff_pct,
        )

        self._initialize_default_slots()
        self._setup_signals()
        self._load_state()

    def _initialize_default_slots(self):
        """Initializes empty slot dictionary up to max_allowed_slots."""
        for i in range(1, self.max_allowed_slots + 1):
            slot_id = f"slot_{i}"
            if slot_id not in self.slots:
                self.slots[slot_id] = {
                    "active": False,
                    "slot_id": slot_id,
                    "symbol": "",
                    "entry_price": 0.0,
                    "amount": 0.0,
                    "cost_usdt": 0.0,
                    "highest_price": 0.0,
                    "stop_loss": 0.0,
                    "take_profit": 0.0,
                    "entry_time": "",
                    "order_id": "",
                }

    def _setup_signals(self):
        """Registers OS signal handlers for graceful shutdown on Railway (SIGINT / SIGTERM)."""
        def handle_termination(signum, frame):
            sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
            logger.info("Signal (%s) received. Saving multi-slot state and shutting down gracefully...", sig_name)
            self.is_running = False
            self._save_state()
            try:
                self.notifier.notify_shutdown(sig_name)
            except Exception as e:
                logger.warning("Failed to send Telegram shutdown notification: %s", e)

        signal.signal(signal.SIGINT, handle_termination)
        signal.signal(signal.SIGTERM, handle_termination)

    def _load_state(self):
        """Loads persistent multi-slot state from bot_state.json across restarts."""
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.max_allowed_slots = int(data.get("max_allowed_slots", self.config.initial_max_slots))
                    self.realized_pnl_usdt = float(data.get("realized_pnl_usdt", 0.0))
                    self.total_trades_count = int(data.get("total_trades_count", 0))

                    loaded_slots = data.get("slots", {})
                    for slot_id, slot_data in loaded_slots.items():
                        self.slots[slot_id] = slot_data
                        self.slots[slot_id]["slot_id"] = slot_id
                        self.slots[slot_id].setdefault("symbol", "")

                    # Ensure all slots up to max_allowed_slots exist
                    self._initialize_default_slots()

                    active_count = sum(1 for s in self.slots.values() if s.get("active"))
                    logger.info(
                        "Loaded persistent state: %d/%d slots active. Realized PnL: $%.2f USDT across %d trades.",
                        active_count,
                        self.max_allowed_slots,
                        self.realized_pnl_usdt,
                        self.total_trades_count,
                    )
            except Exception as e:
                logger.error("Error reading state file '%s': %s", STATE_FILE, e)

    def _save_state(self):
        """Persists multi-slot dictionary and lifetime metrics to bot_state.json."""
        try:
            state_data = {
                "symbols": self.config.trade_symbols,
                "slot_size_usdt": self.config.slot_size_usdt,
                "max_allowed_slots": self.max_allowed_slots,
                "realized_pnl_usdt": self.realized_pnl_usdt,
                "total_trades_count": self.total_trades_count,
                "slots": self.slots,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state_data, f, indent=2)
        except Exception as e:
            logger.error("Failed saving multi-slot state: %s", e)

    def _update_compounding_capacity(self, latest_prices: Dict[str, float]):
        """
        Auto-Compounding Scaling:
        Live portfolio equity = Free USDT + Total Active Slots Market Value across all traded assets.
        Uncap and dynamically create slot_3, slot_4, etc., whenever available equity exceeds:
        (Active Slots Count + 1) * SLOT_SIZE_USDT + CASH_RESERVE_USDT.
        """
        try:
            free_usdt = self.client.get_spot_balance("USDT")
        except Exception as e:
            logger.warning("Could not fetch free USDT balance for equity scaling: %s", e)
            return

        total_slot_market_value = 0.0
        active_slots_count = 0
        for slot in self.slots.values():
            if slot.get("active"):
                active_slots_count += 1
                token_qty = float(slot.get("amount", 0.0))
                sym = slot.get("symbol", "")
                sym_price = latest_prices.get(sym) or float(slot.get("entry_price", 0.0))
                total_slot_market_value += token_qty * sym_price

        total_portfolio_equity = free_usdt + total_slot_market_value

        # Calculate dynamic slot threshold
        available_equity = max(0.0, total_portfolio_equity - self.config.cash_reserve_usdt)
        calculated_slots = math.floor(available_equity / self.config.slot_size_usdt)

        new_capacity = max(self.config.initial_max_slots, active_slots_count, calculated_slots)

        if new_capacity > self.max_allowed_slots:
            old_capacity = self.max_allowed_slots
            self.max_allowed_slots = new_capacity
            self._initialize_default_slots()
            self._save_state()

            logger.info(
                "🚀 AUTO-COMPOUNDING SCALING: Max slots expanded from %d to %d! Equity: $%.2f USDT (Free: $%.2f, In Slots: $%.2f)",
                old_capacity,
                self.max_allowed_slots,
                total_portfolio_equity,
                free_usdt,
                total_slot_market_value,
            )
            try:
                self.notifier.notify_milestone_expansion(
                    new_max_slots=self.max_allowed_slots,
                    total_equity_usdt=total_portfolio_equity,
                    free_usdt=free_usdt,
                )
            except Exception as e:
                logger.warning("Telegram expansion notification failed: %s", e)

    def _update_trailing_stops_for_symbol(self, symbol: str, current_price: float):
        """
        Per-Slot Independent Trailing Take-Profit for a specific asset:
        - Tracks highest price seen for each open slot holding this symbol.
        - When profit >= TRAILING_STOP_ACTIVATION_PCT (default +0.8%), sets floor at
          highest_price * (1.0 - TRAILING_STOP_OFFSET_PCT).
        """
        for slot_id, slot in self.slots.items():
            if not slot.get("active") or slot.get("symbol") != symbol:
                continue

            entry_price = float(slot["entry_price"])
            highest_price = float(slot.get("highest_price", entry_price))

            if current_price > highest_price:
                highest_price = current_price
                slot["highest_price"] = highest_price

            gain_pct = (highest_price - entry_price) / entry_price if entry_price > 0 else 0.0

            # Activate trailing stop if peak gain reached activation threshold
            if gain_pct >= self.config.trailing_stop_activation_pct:
                new_trailing_floor = highest_price * (1.0 - self.config.trailing_stop_offset_pct)
                current_sl = float(slot.get("stop_loss", 0.0))

                if new_trailing_floor > current_sl:
                    slot["stop_loss"] = new_trailing_floor
                    logger.info(
                        "[%s (%s)] Trailing Floor Raised to $%.4f (Highest: $%.4f, Gain: +%.2f%%)",
                        slot_id,
                        symbol,
                        new_trailing_floor,
                        highest_price,
                        gain_pct * 100.0,
                    )
                    self._save_state()

    def get_active_slots(self) -> List[Dict[str, Any]]:
        """Returns list of all active slots."""
        return [slot for slot in self.slots.values() if slot.get("active")]

    def get_first_available_slot(self) -> Optional[str]:
        """Returns first idle slot ID within max_allowed_slots capacity."""
        for i in range(1, self.max_allowed_slots + 1):
            slot_id = f"slot_{i}"
            slot = self.slots.get(slot_id)
            if not slot or not slot.get("active"):
                return slot_id
        return None

    def start(self):
        """Main entry point for 24/7 background execution on Railway."""
        logger.info("Starting MEXC Multi-Pair Multi-Slot Scanner Engine (Railway 24/7)...")
        active_slots = self.get_active_slots()
        logger.info(
            "Pairs: %s | Slot Size: $%.2f USDT | Active Slots: %d/%d | Reserve: $%.2f USDT | Interval: %ds",
            ", ".join(self.config.trade_symbols),
            self.config.slot_size_usdt,
            len(active_slots),
            self.max_allowed_slots,
            self.config.cash_reserve_usdt,
            self.config.poll_interval_seconds,
        )

        try:
            self.notifier.notify_startup({
                "trade_symbols": self.config.trade_symbols,
                "slot_size_usdt": self.config.slot_size_usdt,
                "active_slots": len(active_slots),
                "max_slots": self.max_allowed_slots,
                "cash_reserve_usdt": self.config.cash_reserve_usdt,
                "min_slot_price_diff_pct": (self.config.min_slot_price_diff_pct * 100.0 if self.config.min_slot_price_diff_pct < 0.05 else self.config.min_slot_price_diff_pct),
                "trailing_activation": self.config.trailing_stop_activation_pct * 100.0,
                "trailing_offset": self.config.trailing_stop_offset_pct * 100.0,
                "stop_loss": self.config.stop_loss_pct * 100.0,
                "timeframe": self.config.timeframe,
                "simulation_mode": self.config.simulation_mode,
            })
        except Exception as e:
            logger.warning("Startup Telegram notification failed: %s", e)

        # Resilient exchange client initialization with backoff
        initialized = False
        retry_delay = 5
        while self.is_running and not initialized:
            try:
                self.client.initialize()
                initialized = True
            except Exception as e:
                logger.warning("MEXC client initialization attempt failed (%s). Retrying in %ds...", e, retry_delay)
                if self.config.simulation_mode:
                    break
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)

        while self.is_running:
            try:
                self._iteration()
            except (ccxt.NetworkError, ccxt.ExchangeError) as net_err:
                logger.warning("Exchange network or exchange glitch encountered: %s. Reconnecting in 10s...", net_err)
                time.sleep(10)
            except Exception as e:
                logger.error("Unexpected error in main loop: %s\n%s", e, traceback.format_exc())
                time.sleep(15)

            # Sleep in 1-second ticks for immediate signal responsiveness
            for _ in range(self.config.poll_interval_seconds):
                if not self.is_running:
                    break
                time.sleep(1)

        logger.info("Multi-slot worker shutdown complete.")

    def _iteration(self):
        """
        Single execution cycle:
        Iterates through all configured pairs (e.g. SOL/USDT, DOGE/USDT),
        updates trailing stops, tests exits for slots holding that pair,
        and triggers entry into any idle slot on valid dip signals.
        """
        latest_prices: Dict[str, float] = {}

        for symbol in self.config.trade_symbols:
            try:
                # 1. Fetch closed candles and compute vectorized indicators
                df = self.client.fetch_closed_ohlcv(symbol=symbol, timeframe=self.config.timeframe, limit=100)
                df_indicators = self.strategy.calculate_indicators(df)

                last_row = df_indicators.iloc[-1]
                current_price = float(last_row["close"])
                rsi = float(last_row["rsi"])
                pct_b = float(last_row["bb_percent_b"])
                atr = float(last_row["atr"])

                latest_prices[symbol] = current_price

                # 2. Update dynamic trailing stops for slots holding this specific symbol
                self._update_trailing_stops_for_symbol(symbol, current_price)

                # 3. Independent Exit Checks for slots holding this specific symbol
                for slot in list(self.get_active_slots()):
                    if slot.get("symbol") == symbol:
                        slot_id = slot["slot_id"]
                        exit_signal = self.strategy.evaluate_slot_exit(
                            slot_id=slot_id,
                            slot=slot,
                            current_price=current_price,
                            rsi=rsi,
                            pct_b=pct_b,
                            symbol=symbol,
                        )
                        if exit_signal and exit_signal.action == "SELL":
                            self._execute_slot_exit(slot_id, exit_signal)

                # 4. Entry Evaluation for Available Slots on this symbol
                available_slot_id = self.get_first_available_slot()
                active_slots = self.get_active_slots()

                entry_signal = self.strategy.evaluate_entry_signal(
                    symbol=symbol,
                    df=df_indicators,
                    active_slots=active_slots,
                    available_slot_id=available_slot_id,
                )

                logger.info(
                    "[%s] Close: $%.4f | %%B: %.2f | RSI: %.1f | ATR: %.4f | Action: %s",
                    symbol,
                    current_price,
                    pct_b,
                    rsi,
                    atr,
                    entry_signal.action,
                )

                if entry_signal.action == "BUY" and entry_signal.target_slot_id:
                    self._execute_slot_entry(entry_signal)

            except (ccxt.NetworkError, ccxt.ExchangeError) as e:
                logger.warning("[%s] Exchange error during poll: %s", symbol, e)
            except Exception as e:
                logger.error("[%s] Unexpected error during pair evaluation: %s", symbol, e)

        # 5. Dynamic Equity Tracking & Compounding Scaling across all held assets
        self._update_compounding_capacity(latest_prices)

        # 6. Heartbeat status logging across all slots
        active_slots_list = self.get_active_slots()
        slot_status_str = " | ".join(
            f"{s_id} ({s.get('symbol', 'N/A')}): ${round(s['entry_price'], 4)}" if s.get("active")
            else f"{s_id}: IDLE"
            for s_id, s in self.slots.items()
        )
        logger.info(
            "Cycle scan complete. Active Slots [%d/%d]: %s",
            len(active_slots_list),
            self.max_allowed_slots,
            slot_status_str,
        )

    def _execute_slot_entry(self, signal_res: SignalResult):
        """Executes a Spot Market BUY order strictly allocated to the designated slot."""
        slot_id = signal_res.target_slot_id
        symbol = signal_res.symbol or self.config.trade_symbol
        amount_usdt = self.config.slot_size_usdt

        # Check cash reserve in live mode
        if not self.config.simulation_mode:
            try:
                free_usdt = self.client.get_spot_balance("USDT")
                if free_usdt < (amount_usdt + self.config.cash_reserve_usdt):
                    logger.warning(
                        "Skipping entry for %s (%s): Free USDT ($%.2f) below required ($%.2f + $%.2f reserve)",
                        slot_id, symbol, free_usdt, amount_usdt, self.config.cash_reserve_usdt
                    )
                    return
            except Exception as e:
                logger.error("Failed to check wallet balance before entry: %s", e)
                return

        logger.info("Opening %s: Placing Market BUY for %s ($%.2f USDT)", slot_id, symbol, amount_usdt)

        try:
            order = self.client.execute_market_buy(
                symbol=symbol,
                usdt_amount=amount_usdt,
                max_slippage_pct=self.config.max_slippage_pct,
            )
            exec_price = float(order["price"])
            filled_amount = float(order["amount"])
            cost = float(order["cost"])

            # Set isolated slot state
            self.slots[slot_id] = {
                "active": True,
                "slot_id": slot_id,
                "symbol": symbol,
                "entry_price": exec_price,
                "highest_price": exec_price,
                "amount": filled_amount,
                "cost_usdt": cost,
                "entry_time": datetime.now(timezone.utc).isoformat(),
                "order_id": order.get("id", ""),
                "stop_loss": signal_res.suggested_sl or (exec_price * (1.0 - self.config.stop_loss_pct)),
                "take_profit": signal_res.suggested_tp or (exec_price * (1.0 + self.config.take_profit_pct)),
            }
            self._save_state()

            active_count = len(self.get_active_slots())
            logger.info(
                "🟢 %s FILLED: %.6f %s at $%.4f (Cost: $%.2f USDT). Active Slots: %d/%d",
                slot_id.upper(),
                filled_amount,
                symbol,
                exec_price,
                cost,
                active_count,
                self.max_allowed_slots,
            )

            try:
                self.notifier.notify_slot_buy(
                    slot_id=slot_id,
                    symbol=symbol,
                    price=exec_price,
                    amount=filled_amount,
                    cost_usdt=cost,
                    active_count=active_count,
                    max_slots=self.max_allowed_slots,
                    reason=signal_res.reason,
                )
            except Exception as e:
                logger.warning("Telegram slot buy notification failed: %s", e)

        except Exception as e:
            logger.error("Failed to execute slot buy for %s on %s: %s", slot_id, symbol, e)
            try:
                self.notifier.notify_error(f"Slot Buy Failed ({slot_id} - {symbol})", str(e))
            except Exception:
                pass

    def _execute_slot_exit(self, slot_id: str, signal_res: SignalResult):
        """
        Executes a Spot Market SELL for ONLY the specified slot's base asset tokens.
        Resets ONLY this slot to idle; all other slots continue unaffected.
        Formats token quantity strictly via exchange amount precision constraints.
        """
        slot = self.slots.get(slot_id)
        if not slot or not slot.get("active"):
            return

        symbol = slot.get("symbol") or signal_res.symbol or self.config.trade_symbol
        raw_token_qty = float(slot.get("amount", 0.0))
        entry_cost = float(slot.get("cost_usdt", 0.0))
        entry_price = float(slot.get("entry_price", 0.0))

        # Precision handling for MEXC Spot orders
        token_qty = raw_token_qty
        if hasattr(self.client, "exchange") and hasattr(self.client.exchange, "amount_to_precision"):
            try:
                precision_str = self.client.exchange.amount_to_precision(symbol, raw_token_qty)
                token_qty = float(precision_str)
            except Exception as prec_err:
                logger.debug("Precision formatting fallback: %s", prec_err)

        logger.info(
            "Closing %s: Placing Market SELL for %.6f %s. Reason: %s",
            slot_id, token_qty, symbol, signal_res.reason
        )

        try:
            order = self.client.execute_market_sell(
                symbol=symbol,
                token_amount=token_qty,
            )
            exit_price = float(order["price"])
            gross_proceeds = float(order["cost"])

            pnl_usdt = gross_proceeds - entry_cost
            pnl_pct = ((exit_price - entry_price) / entry_price) * 100.0 if entry_price > 0 else 0.0

            # Accumulate lifetime metrics
            self.realized_pnl_usdt += pnl_usdt
            self.total_trades_count += 1

            # Reset ONLY this specific slot
            self.slots[slot_id] = {
                "active": False,
                "slot_id": slot_id,
                "symbol": "",
                "entry_price": 0.0,
                "amount": 0.0,
                "cost_usdt": 0.0,
                "highest_price": 0.0,
                "stop_loss": 0.0,
                "take_profit": 0.0,
                "entry_time": "",
                "order_id": "",
            }
            self._save_state()

            active_count = len(self.get_active_slots())
            logger.info(
                "💰 %s (%s) CLOSED: PnL: %+.2f USDT (%+.2f%%). Remaining Active Slots: %d/%d. Lifetime PnL: $%.2f USDT",
                slot_id.upper(),
                symbol,
                pnl_usdt,
                pnl_pct,
                active_count,
                self.max_allowed_slots,
                self.realized_pnl_usdt,
            )

            try:
                self.notifier.notify_slot_sell(
                    slot_id=slot_id,
                    symbol=symbol,
                    price=exit_price,
                    amount=token_qty,
                    cost_usdt=gross_proceeds,
                    pnl_pct=pnl_pct,
                    pnl_usdt=pnl_usdt,
                    active_count=active_count,
                    max_slots=self.max_allowed_slots,
                    total_pnl_usdt=self.realized_pnl_usdt,
                    reason=signal_res.reason,
                )
            except Exception as e:
                logger.warning("Telegram slot sell notification failed: %s", e)

        except Exception as e:
            logger.error("Failed to execute slot exit for %s on %s: %s", slot_id, symbol, e)
            try:
                self.notifier.notify_error(f"Slot Exit Failed ({slot_id} - {symbol})", str(e))
            except Exception:
                pass


if __name__ == "__main__":
    # Launch background HTTP health-check server immediately if running on Railway/cloud container
    if os.getenv("PORT"):
        health_thread = threading.Thread(
            target=start_health_check_server,
            daemon=True,
            name="RailwayHealthCheckServer",
        )
        health_thread.start()

    try:
        cfg = TradingConfig.load_from_env()
    except Exception as e:
        logger.error("Configuration Error: %s", e)
        # If running under a container orchestrator with PORT, keep health server alive so deployment doesn't flap
        if os.getenv("PORT"):
            logger.warning("Waiting for valid environment configuration while serving health-checks...")
            while True:
                time.sleep(30)
                try:
                    cfg = TradingConfig.load_from_env()
                    logger.info("Configuration reloaded successfully!")
                    break
                except Exception:
                    pass
        else:
            sys.exit(1)

    bot = MexcMultiSlotBot(cfg)
    bot.start()
