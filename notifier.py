import html
import logging
import threading
from typing import Any, Dict, Optional
import requests

logger = logging.getLogger("mexc_trader.notifier")


class TelegramNotifier:
    def __init__(self, bot_token: Optional[str], chat_id: Optional[str]):
        self.token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)

    def send_message(self, text: str) -> None:
        if not self.enabled:
            return
        threading.Thread(target=self._send, args=(text,), daemon=True).start()

    def _send(self, text: str) -> None:
        try:
            requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage", json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}, timeout=8).raise_for_status()
        except Exception as exc:
            logger.warning("Telegram notification failed: %s", exc)

    def notify_startup(self, c: Dict[str, Any]) -> None:
        self.send_message("🚀 <b>MEXC Spot Scalper started</b>\n" + "\n".join(f"• {html.escape(str(k))}: <code>{html.escape(str(v))}</code>" for k,v in c.items()))

    def notify_trade(self, action: str, symbol: str, slot_id: str, price: float, amount: float, pnl_usdt: Optional[float]=None, reason: str="") -> None:
        pnl = "" if pnl_usdt is None else f"\n• PnL: <code>{pnl_usdt:+.4f} USDT</code>"
        self.send_message(f"{'🟢' if action=='BUY' else '🔴'} <b>{action}</b> <code>{slot_id}</code> <code>{symbol}</code>\n• Price: <code>{price:.10f}</code>\n• Amount: <code>{amount:.10f}</code>{pnl}\n• {html.escape(reason)}")

    def notify_error(self, title: str, details: str) -> None:
        self.send_message(f"⚠️ <b>{html.escape(title)}</b>\n<code>{html.escape(details[:1500])}</code>")

    def notify_shutdown(self) -> None:
        self.send_message("🛑 <b>MEXC bot stopped gracefully.</b>")
