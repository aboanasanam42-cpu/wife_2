"""
Telegram Notification Dispatcher for MEXC Spot Trading Bot.
Uses threaded, non-blocking requests to ensure network latency never slows the trading engine.
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
        """Dispatched when the bot boots or restarts on Railway."""
        msg = (
            "🤖 <b>MEXC 24/7 Spot Trading Bot Started</b>\n\n"
            f"• <b>Symbol:</b> <code>{config_summary.get('trade_symbol')}</code>\n"
            f"• <b>Timeframe:</b> <code>{config_summary.get('timeframe')}</code>\n"
            f"• <b>Trade Size:</b> <code>{config_summary.get('trade_amount_usdt')} USDT</code>\n"
            f"• <b>Strategy:</b> RSI({config_summary.get('rsi_period')}) + {config_summary.get('ema_period')} EMA\n"
            f"• <b>Risk:</b> SL {config_summary.get('stop_loss_pct')*100:.1f}% | TP {config_summary.get('take_profit_pct')*100:.1f}%\n"
            f"• <b>Mode:</b> {'🟢 SIMULATION' if config_summary.get('simulation_mode') else '🔴 LIVE SPOT EXECUTION'}\n\n"
            "<i>Continuous 24/7 background worker running...</i>"
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
        """Dispatched on order fills (Buy / Sell)."""
        action_icon = "🟢 <b>BUY ORDER FILLED</b>" if action.upper() == "BUY" else "🔴 <b>SELL ORDER FILLED</b>"
        pnl_text = ""
        if pnl_pct is not None and pnl_usdt is not None:
            pnl_icon = "📈" if pnl_pct >= 0 else "📉"
            pnl_text = (
                f"\n{pnl_icon} <b>Realized PnL:</b> "
                f"{'+' if pnl_pct >= 0 else ''}{pnl_pct:.2f}% "
                f"({'+' if pnl_usdt >= 0 else ''}{pnl_usdt:.2f} USDT)"
            )

        fee_text = f"\n• <b>Estimated Fee:</b> {fee:.4f}" if fee else ""

        msg = (
            f"{action_icon}\n\n"
            f"• <b>Pair:</b> <code>{symbol}</code>\n"
            f"• <b>Exec Price:</b> <code>${price:,.4f}</code>\n"
            f"• <b>Token Qty:</b> <code>{amount:.6f}</code>\n"
            f"• <b>Total Cost:</b> <code>${cost_usdt:,.2f} USDT</code>\n"
            f"• <b>Trigger Reason:</b> <i>{reason}</i>"
            f"{fee_text}"
            f"{pnl_text}"
        )
        self.send_message(msg)

    def notify_error(self, error_title: str, details: str):
        """Dispatched on unhandled exceptions or critical API disconnects."""
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
            "State saved safely. Background worker exiting."
        )
        self.send_message(msg)
