"""
MEXC 24/7 Automated Spot Trading Bot - Independent Multi-Slot Engine with Dynamic Compounding Expansion.
Maintains isolated parallel trading slots with fixed allocation (SLOT_SIZE_USDT = 4.0).
Scales maximum permissible slots automatically based on total wallet equity.
Implements anti-clustering decoupling and dynamic trailing profit protection per slot.
"""

import os
import sys
import json
import time
import signal
import logging
import traceback
import math
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
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

        logger.setLevel(getattr(logging, config.log_level, logging.INFO))

        # Core Multi-Slot State Structure:
        # {
        #   "slot_1": {"active": False, "entry_price": 0.0, "amount": 0.0, "cost_usdt": 0.0, "highest_price": 0.0, "stop_loss": 0.0, "take_profit": 0.0, "entry_time": ""},
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
            logger.info("Signal (%s) received. Saving multi-slot state and shutting down...", sig_name)
            self.is_running = False
            self._save_state()
            self.notifier.notify_shutdown(sig_name)

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

                    # Ensure all slots up to max_allowed_slots are present
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
                "symbol": self.config.trade_symbol,
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

    def _update_compounding_capacity(self, current_price: float):
        """
        Dynamic Slot Scaling (Auto-Compounding):
        Calculates Total Portfolio Equity = Free USDT + Sum(Active Slot Current Values).
        Allowed Slots = floor((Equity - Cash Reserve) / SLOT_SIZE_USDT).
        Safety buffer: Maintains a minimum cash reserve (default 2.0 USDT).
        Unlocks new slots dynamically as profits cross 4.0 USDT milestones.
        """
        try:
            free_usdt = self.client.get_spot_balance("USDT")
        except Exception as e:
            logger.warning("Could not fetch free USDT balance for equity scaling: %s", e)
            return

        # Calculate sum of active slot values
        total_slot_value = 0.0
        for slot in self.slots.values():
            if slot.get("active"):
                total_slot_value += float(slot.get("amount", 0.0)) * current_price

        total_equity = free_usdt + total_slot_value
        available_for_slots = max(0.0, total_equity - self.config.cash_reserve_usdt)
        calculated_slots = math.floor(available_for_slots / self.config.slot_size_usdt)

        # Ensure we never drop below initial minimum or currently open active slots
        active_count = sum(1 for s in self.slots.values() if s.get("active"))
        new_capacity = max(self.config.initial_max_slots, active_count, calculated_slots)

        if new_capacity > self.max_allowed_slots:
            old_capacity = self.max_allowed_slots
            self.max_allowed_slots = new_capacity
            self._initialize_default_slots()
            self._save_state()

            logger.info(
                "🚀 COMPOUNDING EXPANSION: Max slots scaled from %d to %d! Equity: $%.2f USDT (Free: $%.2f, In Slots: $%.2f)",
                old_capacity,
                self.max_allowed_slots,
                total_equity,
                free_usdt,
                total_slot_value,
            )
            self.notifier.notify_milestone_expansion(
                new_max_slots=self.max_allowed_slots,
                total_equity_usdt=total_equity,
                free_usdt=free_usdt,
            )

    def _update_trailing_stops_per_slot(self, current_price: float):
        """
        Per-Slot Independent Trailing Take-Profit:
        - Activates when that specific slot reaches +1.2% profit.
        - Trails behind that slot's peak with 0.5% distance.
        """
        for slot_id, slot in self.slots.items():
            if not slot.get("active"):
                continue

            entry_price = float(slot["entry_price"])
            highest_price = float(slot.get("highest_price", entry_price))

            if current_price > highest_price:
                highest_price = current_price
                slot["highest_price"] = highest_price

            gain_pct = (highest_price - entry_price) / entry_price

            # Check if trailing activation threshold reached (+1.2%)
            if gain_pct >= self.config.trailing_stop_activation_pct:
                new_trailing_floor = highest_price * (1.0 - self.config.trailing_stop_offset_pct)
                current_sl = float(slot.get("stop_loss", 0.0))

                if new_trailing_floor > current_sl:
                    slot["stop_loss"] = new_trailing_floor
                    logger.info(
                        "[%s] Trailing Stop Raised to $%.2f (Highest: $%.2f, Gain: +%.2f%%)",
                        slot_id,
                        new_trailing_floor,
                        highest_price,
                        gain_pct * 100.0,
                    )
                    self._save_state()

    def get_active_slots(self) -> List[Dict[str, Any]]:
        """Returns list of all currently active slots."""
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
        """Main entry method for the 24/7 Multi-Slot background worker."""
        logger.info("Initializing MEXC Multi-Slot Compounding Engine...")
        active_slots = self.get_active_slots()
        logger.info(
            "Pair: %s | Slot Size: $%.2f USDT | Active Slots: %d/%d | Reserve: $%.2f USDT",
            self.config.trade_symbol,
            self.config.slot_size_usdt,
            len(active_slots),
            self.max_allowed_slots,
            self.config.cash_reserve_usdt,
        )

        self.notifier.notify_startup({
            "trade_symbol": self.config.trade_symbol,
            "slot_size_usdt": self.config.slot_size_usdt,
            "active_slots": len(active_slots),
            "max_slots": self.max_allowed_slots,
            "cash_reserve_usdt": self.config.cash_reserve_usdt,
            "min_slot_price_diff_pct": self.config.min_slot_price_diff_pct,
            "trailing_activation": self.config.trailing_stop_activation_pct * 100.0,
            "trailing_offset": self.config.trailing_stop_offset_pct * 100.0,
            "stop_loss": self.config.stop_loss_pct * 100.0,
            "timeframe": self.config.timeframe,
            "simulation_mode": self.config.simulation_mode,
        })

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
                logger.warning("Exchange network glitch: %s. Retrying in 10s...", net_err)
                time.sleep(10)
            except Exception as e:
                logger.error("Unexpected error in main iteration: %s\n%s", e, traceback.format_exc())
                time.sleep(15)

            for _ in range(self.config.poll_interval_seconds):
                if not self.is_running:
                    break
                time.sleep(1)

        logger.info("Multi-slot worker shutdown complete.")

    def _iteration(self):
        """Single execution cycle: OHLCV analysis -> trailing stops -> slot exits -> slot entries."""
        symbol = self.config.trade_symbol

        # 1. Fetch closed candles and compute technical indicators
        df = self.client.fetch_closed_ohlcv(symbol=symbol, timeframe=self.config.timeframe, limit=100)
        df_indicators = self.strategy.calculate_indicators(df)

        last_row = df_indicators.iloc[-1]
        current_price = float(last_row["close"])
        rsi = float(last_row["rsi"])
        pct_b = float(last_row["bb_percent_b"])
        atr = float(last_row["atr"])

        # 2. Dynamic Equity Tracking & Compounding Scaling
        self._update_compounding_capacity(current_price)

        # 3. Update Dynamic Trailing Stops for each open slot
        self._update_trailing_stops_per_slot(current_price)

        # 4. Independent Exit Checks for all active slots
        # Note: A list copy is used because executing an exit modifies the slot
        active_slots = self.get_active_slots()
        for slot in list(active_slots):
            slot_id = slot["slot_id"]
            exit_signal = self.strategy.evaluate_slot_exit(slot_id, slot, current_price, rsi, pct_b)
            if exit_signal and exit_signal.action == "SELL":
                self._execute_slot_exit(slot_id, exit_signal)

        # 5. Entry Evaluation for Available Slots
        available_slot_id = self.get_first_available_slot()
        remaining_active_slots = self.get_active_slots()

        # Log cycle heartbeat
        slot_status_str = " | ".join(
            f"{s_id}: {'$'+str(round(s['entry_price'], 1)) if s.get('active') else 'IDLE'}"
            for s_id, s in self.slots.items()
        )
        logger.info(
            "[%s] Close: $%.2f | %%B: %.2f | RSI: %.1f | ATR: %.2f | Slots [%d/%d]: %s",
            symbol,
            current_price,
            pct_b,
            rsi,
            atr,
            len(remaining_active_slots),
            self.max_allowed_slots,
            slot_status_str,
        )

        if available_slot_id is not None:
            entry_signal = self.strategy.evaluate_entry_signal(
                df=df_indicators,
                active_slots=remaining_active_slots,
                available_slot_id=available_slot_id,
            )
            if entry_signal.action == "BUY" and entry_signal.target_slot_id:
                self._execute_slot_entry(entry_signal)

    def _execute_slot_entry(self, signal_res: SignalResult):
        """Executes a Spot Market BUY order strictly allocated to the designated slot."""
        slot_id = signal_res.target_slot_id
        symbol = self.config.trade_symbol
        amount_usdt = self.config.slot_size_usdt

        # Check cash reserve in live mode
        if not self.config.simulation_mode:
            try:
                free_usdt = self.client.get_spot_balance("USDT")
                if free_usdt < (amount_usdt + self.config.cash_reserve_usdt):
                    logger.warning(
                        "Skipping entry for %s: Free USDT ($%.2f) below required ($%.2f + $%.2f reserve)",
                        slot_id, free_usdt, amount_usdt, self.config.cash_reserve_usdt
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
                "🟢 %s FILLED: %.6f tokens at $%.2f (Cost: $%.2f USDT). Active Slots: %d/%d",
                slot_id.upper(),
                filled_amount,
                exec_price,
                cost,
                active_count,
                self.max_allowed_slots,
            )

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
            logger.error("Failed to execute slot buy for %s: %s", slot_id, e)
            self.notifier.notify_error(f"Slot Buy Failed ({slot_id})", str(e))

    def _execute_slot_exit(self, slot_id: str, signal_res: SignalResult):
        """
        Executes a Spot Market SELL for ONLY the specified slot's base asset tokens.
        Resets ONLY this slot to idle; all other slots continue unaffected.
        """
        slot = self.slots.get(slot_id)
        if not slot or not slot.get("active"):
            return

        symbol = self.config.trade_symbol
        token_qty = float(slot.get("amount", 0.0))
        entry_cost = float(slot.get("cost_usdt", 0.0))
        entry_price = float(slot.get("entry_price", 0.0))

        logger.info("Closing %s: Placing Market SELL for %.6f tokens. Reason: %s", slot_id, token_qty, signal_res.reason)

        try:
            order = self.client.execute_market_sell(
                symbol=symbol,
                token_amount=token_qty,
            )
            exit_price = float(order["price"])
            gross_proceeds = float(order["cost"])

            pnl_usdt = gross_proceeds - entry_cost
            pnl_pct = ((exit_price - entry_price) / entry_price) * 100.0 if entry_price > 0 else 0.0

            # Accumulate lifetime performance
            self.realized_pnl_usdt += pnl_usdt
            self.total_trades_count += 1

            # Reset ONLY this specific slot
            self.slots[slot_id] = {
                "active": False,
                "slot_id": slot_id,
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
                "💰 %s CLOSED: PnL: %+.2f USDT (%+.2f%%). Remaining Active Slots: %d/%d. Lifetime PnL: $%.2f USDT",
                slot_id.upper(),
                pnl_usdt,
                pnl_pct,
                active_count,
                self.max_allowed_slots,
                self.realized_pnl_usdt,
            )

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
            logger.error("Failed to execute slot exit for %s: %s", slot_id, e)
            self.notifier.notify_error(f"Slot Exit Failed ({slot_id})", str(e))


if __name__ == "__main__":
    try:
        cfg = TradingConfig.load_from_env()
    except Exception as e:
        logger.error("Configuration Error: %s", e)
        sys.exit(1)

    bot = MexcMultiSlotBot(cfg)
    bot.start()
