"""
Configuration Module for MEXC Spot Trading Bot.
Handles environment variable loading, validation, and logging setup.
"""

import os
import sys
import logging
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()


def str_to_bool(val: str, default: bool = False) -> bool:
    if val is None:
        return default
    return str(val).strip().lower() in ("true", "1", "yes", "y", "t")


@dataclass(frozen=True)
class TradingConfig:
    # MEXC API Credentials
    mexc_api_key: str
    mexc_api_secret: str

    # Telegram Notification Credentials
    telegram_bot_token: Optional[str]
    telegram_chat_id: Optional[str]

    # Trading Engine Settings
    trade_symbol: str
    trade_amount_usdt: float
    timeframe: str
    poll_interval_seconds: int

    # Strategy Parameters (RSI + 20-period EMA)
    rsi_period: int
    rsi_oversold: float
    rsi_overbought: float
    ema_period: int

    # Risk Management
    stop_loss_pct: float
    take_profit_pct: float
    max_slippage_pct: float
    max_open_trades: int

    # Operational Mode & Logging
    simulation_mode: bool
    log_level: str

    @classmethod
    def load_from_env(cls) -> "TradingConfig":
        """Load and validate configuration from system environment variables."""
        api_key = os.getenv("MEXC_API_KEY", "").strip()
        api_secret = os.getenv("MEXC_API_SECRET", "").strip()

        # In live mode (not simulation), API key & secret are strictly required
        simulation_mode = str_to_bool(os.getenv("SIMULATION_MODE", "False"), default=False)
        if not simulation_mode and (not api_key or not api_secret):
            raise ValueError(
                "CRITICAL SECURITY ERROR: Missing 'MEXC_API_KEY' or 'MEXC_API_SECRET'. "
                "Ensure these environment variables are populated in Railway or your local .env file."
            )

        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or None
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip() or None

        trade_symbol = os.getenv("TRADE_SYMBOL", "BTC/USDT").strip().upper()
        if "/" not in trade_symbol:
            trade_symbol = f"{trade_symbol}/USDT"

        try:
            trade_amount_usdt = float(os.getenv("TRADE_AMOUNT_USDT", "15.0"))
            if trade_amount_usdt <= 0:
                raise ValueError("TRADE_AMOUNT_USDT must be greater than zero.")
        except ValueError as e:
            raise ValueError(f"Invalid TRADE_AMOUNT_USDT: {e}")

        timeframe = os.getenv("TIMEFRAME", "15m").strip()

        # Check interval: supports both CHECK_INTERVAL_SECONDS and POLL_INTERVAL_SECONDS
        poll_interval_str = os.getenv("CHECK_INTERVAL_SECONDS") or os.getenv("POLL_INTERVAL_SECONDS", "30")
        poll_interval = int(poll_interval_str)

        rsi_period = int(os.getenv("RSI_PERIOD", "14"))
        rsi_oversold = float(os.getenv("RSI_OVERSOLD", "30.0"))
        rsi_overbought = float(os.getenv("RSI_OVERBOUGHT", "70.0"))
        ema_period = int(os.getenv("EMA_PERIOD", "20"))

        # Risk Management: support STOP_LOSS_PERCENT (1.5 -> 0.015) or STOP_LOSS_PCT (0.015)
        if "STOP_LOSS_PERCENT" in os.environ:
            stop_loss_pct = float(os.environ["STOP_LOSS_PERCENT"]) / 100.0
        else:
            sl_val = float(os.getenv("STOP_LOSS_PCT", "0.015"))
            stop_loss_pct = sl_val / 100.0 if sl_val > 0.5 else sl_val

        # Take Profit: support TAKE_PROFIT_PERCENT (2.5 -> 0.025) or TAKE_PROFIT_PCT (0.025)
        if "TAKE_PROFIT_PERCENT" in os.environ:
            take_profit_pct = float(os.environ["TAKE_PROFIT_PERCENT"]) / 100.0
        else:
            tp_val = float(os.getenv("TAKE_PROFIT_PCT", "0.025"))
            take_profit_pct = tp_val / 100.0 if tp_val > 0.5 else tp_val

        max_slippage_pct = float(os.getenv("MAX_SLIPPAGE_PCT", "0.005"))
        max_open_trades = int(os.getenv("MAX_OPEN_TRADES", "1"))
        log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()

        return cls(
            mexc_api_key=api_key,
            mexc_api_secret=api_secret,
            telegram_bot_token=bot_token,
            telegram_chat_id=chat_id,
            trade_symbol=trade_symbol,
            trade_amount_usdt=trade_amount_usdt,
            timeframe=timeframe,
            poll_interval_seconds=poll_interval,
            rsi_period=rsi_period,
            rsi_oversold=rsi_oversold,
            rsi_overbought=rsi_overbought,
            ema_period=ema_period,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            max_slippage_pct=max_slippage_pct,
            max_open_trades=max_open_trades,
            simulation_mode=simulation_mode,
            log_level=log_level,
        )


def setup_logger(name: str = "mexc_trader", level: str = "INFO") -> logging.Logger:
    """Configures structured console logging with ISO-8601 timestamps."""
    logger = logging.getLogger(name)
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(numeric_level)
    if not logger.handlers:
        formatter = logging.Formatter(
            fmt="[%(asctime)s.%(msecs)03d] [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger
