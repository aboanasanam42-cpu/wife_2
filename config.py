import os
import logging
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

def setup_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger

@dataclass(frozen=True)
class TradingConfig:
    mexc_api_key: str
    mexc_api_secret: str
    trade_symbol: str = "BTC/USDT"
    timeframe: str = "15m"
    poll_interval_seconds: int = 30
    log_level: str = "INFO"
    simulation_mode: bool = False
    max_slippage_pct: float = 0.005

    # Multi-Slot Execution & Dynamic Compounding
    slot_size_usdt: float = 4.0
    initial_max_slots: int = 2
    cash_reserve_usdt: float = 2.0
    min_slot_price_diff_pct: float = 1.0  # At least 1.0% separation between open slot entry prices

    # Strategy Parameters (Bollinger Bands %B + Fast RSI + ATR)
    bollinger_period: int = 20
    bollinger_std: float = 2.0
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    ema_period: int = 20
    atr_period: int = 14

    # Per-Slot Independent Exit & Dynamic Trailing Engine
    stop_loss_pct: float = 0.02                    # Hard Stop-Loss: -2.0%
    take_profit_pct: float = 0.03                  # Base Take-Profit target: +3.0%
    trailing_stop_activation_pct: float = 0.012    # Trailing activates at +1.2%
    trailing_stop_offset_pct: float = 0.005        # 0.5% distance from peak

    # Telegram Notification Alerts
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    @classmethod
    def load_from_env(cls) -> "TradingConfig":
        api_key = os.getenv("MEXC_API_KEY", "").strip()
        api_secret = os.getenv("MEXC_API_SECRET", "").strip()

        simulation_mode = os.getenv("SIMULATION_MODE", "false").strip().lower() in ("true", "1", "yes")

        if not simulation_mode and (not api_key or not api_secret):
            raise ValueError("MEXC_API_KEY and MEXC_API_SECRET must be set in environment variables.")

        # Parse Slot Size USDT (e.g. 4.0)
        raw_slot_size = os.getenv("SLOT_SIZE_USDT") or os.getenv("TRADE_AMOUNT_USDT", "4.0")
        raw_slot_size = str(raw_slot_size).replace("USDT", "").replace("usdt", "").strip()
        slot_size_usdt = float(raw_slot_size) if raw_slot_size else 4.0

        # Slot Scaling Config
        initial_max_slots = int(os.getenv("INITIAL_MAX_SLOTS", "2"))
        cash_reserve_usdt = float(os.getenv("CASH_RESERVE_USDT", "2.0"))
        min_slot_price_diff_pct = float(os.getenv("MIN_SLOT_PRICE_DIFF_PCT", "1.0"))

        # Trailing Stop & Risk Management Config
        trailing_act_raw = float(os.getenv("TRAILING_STOP_ACTIVATION_PCT", "1.2"))
        trailing_stop_activation_pct = trailing_act_raw / 100.0 if trailing_act_raw >= 0.05 else trailing_act_raw

        trailing_offset_raw = float(os.getenv("TRAILING_STOP_OFFSET_PCT", "0.5"))
        trailing_stop_offset_pct = trailing_offset_raw / 100.0 if trailing_offset_raw >= 0.05 else trailing_offset_raw

        sl_raw = float(os.getenv("STOP_LOSS_PERCENT", "2.0"))
        stop_loss_pct = sl_raw / 100.0 if sl_raw >= 0.05 else sl_raw

        tp_raw = float(os.getenv("TAKE_PROFIT_PERCENT", "3.0"))
        take_profit_pct = tp_raw / 100.0 if tp_raw >= 0.05 else tp_raw

        trade_symbol = os.getenv("TRADE_SYMBOL", "BTC/USDT").strip().upper()
        if "/" not in trade_symbol:
            trade_symbol = f"{trade_symbol}/USDT"

        check_sec = int(os.getenv("CHECK_INTERVAL_SECONDS") or os.getenv("POLL_INTERVAL_SECONDS", "30"))

        return cls(
            mexc_api_key=api_key,
            mexc_api_secret=api_secret,
            trade_symbol=trade_symbol,
            timeframe=os.getenv("TIMEFRAME", "15m").strip(),
            poll_interval_seconds=check_sec,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper().strip(),
            simulation_mode=simulation_mode,
            max_slippage_pct=float(os.getenv("MAX_SLIPPAGE_PCT", "0.005")),
            slot_size_usdt=slot_size_usdt,
            initial_max_slots=initial_max_slots,
            cash_reserve_usdt=cash_reserve_usdt,
            min_slot_price_diff_pct=min_slot_price_diff_pct,
            bollinger_period=int(os.getenv("BOLLINGER_PERIOD", "20")),
            bollinger_std=float(os.getenv("BOLLINGER_STD", "2.0")),
            rsi_period=int(os.getenv("RSI_PERIOD", "14")),
            rsi_oversold=float(os.getenv("RSI_OVERSOLD", "30.0")),
            rsi_overbought=float(os.getenv("RSI_OVERBOUGHT", "70.0")),
            ema_period=int(os.getenv("EMA_PERIOD", "20")),
            atr_period=int(os.getenv("ATR_PERIOD", "14")),
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            trailing_stop_activation_pct=trailing_stop_activation_pct,
            trailing_stop_offset_pct=trailing_stop_offset_pct,
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or None,
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip() or None,
        )
