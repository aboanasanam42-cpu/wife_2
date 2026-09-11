"""
Telegram Notification Dispatcher for MEXC Spot Multi-Slot Trading Bot.
Uses threaded, non-blocking requests to ensure network latency never slows the trading engine.
Supports independent slot tracking, dynamic compounding milestones, and isolated exits.
"""

import threading
import logging
from typing import Optional, Dict, Any
import requests

logger = logging.getLogger("mexc_trader.notifier")


class TelegramNotifier:
    def __init__(self, bot_token: Optional[str], chat_id: Optional[str]):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)
        if self.enabled:
            logger.info("Telegram Notifier active. Alerts will be dispatched to Chat ID: %s", self.chat_id)
        else:
            logger.warning("Telegram credentials not supplied. Push notifications are disabled.")

    def _send_sync(self, text: str):
        """Worker method to execute the HTTP request to Telegram Bot API."""
        if not self.enabled:
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            response = requests.post(url, json=payload, timeout=8)
            if response.status_code != 200:
                logger.error(
                    "Telegram API responded with HTTP %d: %s",
                    response.status_code,
                    response.text,
                )
        except Exception as e:
            logger.error("Failed to send Telegram alert: %s", e)

    def send_message(self, text: str):
        """Dispatches notification in a separate daemon thread to avoid blocking trading logic."""
        if not self.enabled:
            return
        thread = threading.Thread(target=self._send_sync, args=(text,), daemon=True)
        thread.start()

    def notify_startup(self, config_summary: Dict[str, Any]):
        """Dispatched when the multi-slot bot boots or restarts on Railway."""
        symbols = config_summary.get("trade_symbols")
        if isinstance(symbols, list):
            symbol_str = ", ".join(symbols)
        else:
            symbol_str = str(symbols or config_summary.get("trade_symbol", "SOL/USDT, DOGE/USDT"))

        msg = (
            "🚀 <b>MEXC Multi-Slot Compounding Scalper Started</b>\n\n"
            f"• <b>Pairs:</b> <code>{symbol_str}</code>\n"
            f"• <b>Slot Size:</b> <code>${config_summary.get('slot_size_usdt', 4.0):.2f} USDT</code>\n"
            f"• <b>Active Slots:</b> <code>{config_summary.get('active_slots', 0)} / {config_summary.get('max_slots', 2)}</code>\n"
            f"• <b>Cash Reserve:</b> <code>${config_summary.get('cash_reserve_usdt', 2.0):.2f} USDT</code>\n"
            f"• <b>Anti-Clustering Spacing:</b> <code>>={config_summary.get('min_slot_price_diff_pct', 1.0)}%</code>\n"
            f"• <b>Trailing TP:</b> <code>+{config_summary.get('trailing_activation', 1.2)}% (Trail: {config_summary.get('trailing_offset', 0.5)}%)</code>\n"
            f"• <b>Stop-Loss:</b> <code>-{config_summary.get('stop_loss', 2.0)}%</code>\n"
            f"• <b>Timeframe:</b> <code>{config_summary.get('timeframe', '15m')}</code>\n"
            f"• <b>Mode:</b> {'🟢 SIMULATION' if config_summary.get('simulation_mode') else '🔴 LIVE MEXC SPOT'}\n\n"
            "<i>Independent Multi-Slot engine running with dynamic equity scaling...</i>"
        )
        self.send_message(msg)

    def notify_slot_buy(
        self,
        slot_id: str,
        symbol: str,
        price: float,
        amount: float,
        cost_usdt: float,
        active_count: int,
        max_slots: int,
        reason: str,
    ):
        """Dispatched when an individual slot opens."""
        msg = (
            f"🟢 <b>MULTI-SLOT BUY: {slot_id.upper()} FILLED</b>\n\n"
            f"• <b>Slot ID:</b> <code>{slot_id}</code>\n"
            f"• <b>Pair:</b> <code>{symbol}</code>\n"
            f"• <b>Entry Price:</b> <code>${price:,.4f}</code>\n"
            f"• <b>Quantity:</b> <code>{amount:.6f}</code>\n"
            f"• <b>Cost:</b> <code>${cost_usdt:,.2f} USDT</code>\n"
            f"• <b>Slot Utilization:</b> <code>{active_count} / {max_slots} Active</code>\n"
            f"• <b>Trigger:</b> <i>{reason}</i>\n\n"
            "<i>Trailing take-profit armed (+1.2% activation floor).</i>"
        )
        self.send_message(msg)

    def notify_slot_sell(
        self,
        slot_id: str,
        symbol: str,
        price: float,
        amount: float,
        cost_usdt: float,
        pnl_pct: float,
        pnl_usdt: float,
        active_count: int,
        max_slots: int,
        total_pnl_usdt: float,
        reason: str,
    ):
        """Dispatched when an individual slot hits TP, SL, or peak exhaustion."""
        is_profit = pnl_usdt >= 0
        icon = "💰" if is_profit else "🛑"
        tag = "TAKE-PROFIT" if is_profit else "STOP-LOSS"

        msg = (
            f"{icon} <b>MULTI-SLOT EXIT: {slot_id.upper()} ({tag})</b>\n\n"
            f"• <b>Slot ID:</b> <code>{slot_id}</code>\n"
            f"• <b>Pair:</b> <code>{symbol}</code>\n"
            f"• <b>Exit Price:</b> <code>${price:,.4f}</code>\n"
            f"• <b>Slot Realized PnL:</b> <code>{'+' if is_profit else ''}{pnl_pct:.2f}% ({'+' if is_profit else ''}${pnl_usdt:.2f} USDT)</code>\n"
            f"• <b>Total Lifetime PnL:</b> <code>+${total_pnl_usdt:,.2f} USDT</code>\n"
            f"• <b>Remaining Active Slots:</b> <code>{active_count} / {max_slots}</code>\n"
            f"• <b>Trigger Reason:</b> <i>{reason}</i>\n\n"
            f"<i>{slot_id} has reset to idle and is ready for the next dip entry.</i>"
        )
        self.send_message(msg)

    def notify_milestone_expansion(self, new_max_slots: int, total_equity_usdt: float, free_usdt: float):
        """Dispatched when wallet equity crosses a milestone and unlocks an additional slot."""
        msg = (
            "🎉 <b>COMPOUNDING MILESTONE UNLOCKED</b>\n\n"
            f"• <b>New Capacity:</b> <code>{new_max_slots} Parallel Slots</code>\n"
            f"• <b>Total Equity:</b> <code>${total_equity_usdt:,.2f} USDT</code>\n"
            f"• <b>Available Cash:</b> <code>${free_usdt:,.2f} USDT</code>\n\n"
            "<i>Dynamic scaling activated: Additional $4.0 USDT slot unlocked automatically!</i>"
        )
        self.send_message(msg)

    def notify_trade(
        self,
        action: str,
        symbol: str,
        price: float,
        amount: float,
        cost_usdt: float,
        reason: str,
        fee: Optional[float] = None,
        pnl_pct: Optional[float] = None,
        pnl_usdt: Optional[float] = None,
    ):
        """Standard compatibility wrapper."""
        action_icon = "🟢 <b>BUY FILLED</b>" if action.upper() == "BUY" else "🔴 <b>SELL FILLED</b>"
        pnl_str = f"\n• <b>PnL:</b> {pnl_pct:+.2f}% (${pnl_usdt:+.2f} USDT)" if pnl_pct is not None else ""
        msg = (
            f"{action_icon}\n\n"
            f"• <b>Pair:</b> <code>{symbol}</code>\n"
            f"• <b>Price:</b> <code>${price:,.4f}</code>\n"
            f"• <b>Volume:</b> <code>${cost_usdt:,.2f} USDT</code>\n"
            f"• <b>Reason:</b> <i>{reason}</i>"
            f"{pnl_str}"
        )
        self.send_message(msg)

    def notify_error(self, error_title: str, details: str):
        """Dispatched on unhandled exceptions or critical API errors."""
        msg = (
            f"⚠️ <b>CRITICAL BOT ALERT</b>\n\n"
            f"<b>Issue:</b> {error_title}\n"
            f"<b>Details:</b> <code>{details[:1200]}</code>\n\n"
            "<i>Automated recovery in progress. Worker retrying with backoff...</i>"
        )
        self.send_message(msg)

    def notify_shutdown(self, signal_name: str):
        """Dispatched when Railway stops or redeploys the worker."""
        msg = (
            f"🛑 <b>Bot Shutting Down Gracefully</b>\n\n"
            f"Received system signal: <code>{signal_name}</code>.\n"
            "Multi-slot state saved safely in bot_state.json. Background worker exiting."
        )
        self.send_message(msg)
