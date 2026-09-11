"""
Configuration Module for MEXC Spot Multi-Slot Multi-Pair Scanner Engine.
Handles environment variables with robust legacy key fallbacks and strict defaults.
Supports comma-separated trading pairs (e.g., 'SOL/USDT,DOGE/USDT').
"""

import os
import logging
from dataclasses import dataclass, field
from typing import Optional, List
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


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
    trade_symbols: List[str] = field(default_factory=lambda: ["SOL/USDT", "DOGE/USDT"])
    timeframe: str = "1m"
    poll_interval_seconds: int = 15
    log_level: str = "INFO"
    simulation_mode: bool = False
    max_slippage_pct: float = 0.005

    # Multi-Slot Execution & Dynamic Compounding
    slot_size_usdt: float = 4.0
    initial_max_slots: int = 2
    cash_reserve_usdt: float = 2.0
    min_slot_price_diff_pct: float = 0.8  # Inter-slot price distance for the same asset (default: 0.8%)

    # Strategy Parameters (Bollinger Bands %B + Fast RSI + ATR)
    bollinger_period: int = 20
    bollinger_std: float = 2.0
    rsi_period: int = 14
    rsi_oversold: float = 36.0           # Widened to capture frequent local micro-dips
    rsi_overbought: float = 68.0          # Overbought exhaustion threshold
    ema_period: int = 20
    atr_period: int = 14

    # Per-Slot Independent Exit & Dynamic Trailing Engine
    stop_loss_pct: float = 0.02           # Hard Stop-Loss: -2.0%
    take_profit_pct: float = 0.03         # Base Take-Profit target: +3.0%
    trailing_stop_activation_pct: float = 0.008  # +0.8% triggers trailing (+0.8%)
    trailing_stop_offset_pct: float = 0.003      # 0.3% trailing distance

    # Telegram Notification Alerts
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    @property
    def trade_symbol(self) -> str:
        """Backward compatibility accessor returning primary pair."""
        return self.trade_symbols[0] if self.trade_symbols else "SOL/USDT"

    @classmethod
    def load_from_env(cls) -> "TradingConfig":
        api_key = os.getenv("MEXC_API_KEY", "").strip()
        api_secret = os.getenv("MEXC_API_SECRET", "").strip()

        simulation_mode = os.getenv("SIMULATION_MODE", "false").strip().lower() in ("true", "1", "yes")

        if not simulation_mode and (not api_key or not api_secret):
            raise ValueError("MEXC_API_KEY and MEXC_API_SECRET must be set in environment variables.")

        # 1. Multi-Pair Parsing: Read TRADE_SYMBOL first, fallback to PAIR (default: "SOL/USDT,DOGE/USDT")
        raw_pairs = os.getenv("TRADE_SYMBOL") or os.getenv("PAIR") or "SOL/USDT,DOGE/USDT"
        parsed_symbols: List[str] = []
        for item in raw_pairs.split(","):
            s = item.strip().upper()
            if not s:
                continue
            if "/" not in s:
                s = f"{s}/USDT"
            if s not in parsed_symbols:
                parsed_symbols.append(s)

        if not parsed_symbols:
            parsed_symbols = ["SOL/USDT", "DOGE/USDT"]

        # 2. Slot Sizing: Read SLOT_SIZE_USDT first, fallback to TRADE_AMOUNT_USDT (default: 4.0)
        raw_slot_size = os.getenv("SLOT_SIZE_USDT") or os.getenv("TRADE_AMOUNT_USDT", "4.0")
        raw_slot_size = str(raw_slot_size).replace("USDT", "").replace("usdt", "").strip()
        slot_size_usdt = float(raw_slot_size) if raw_slot_size else 4.0

        # 3. Slot Limits: Read INITIAL_MAX_SLOTS first, fallback to MAX_OPEN_TRADES (default: 2)
        raw_max_slots = os.getenv("INITIAL_MAX_SLOTS") or os.getenv("MAX_OPEN_TRADES", "2")
        initial_max_slots = int(raw_max_slots)

        # 4. Cash Shield: CASH_RESERVE_USDT (default: 2.0)
        cash_reserve_usdt = float(os.getenv("CASH_RESERVE_USDT", "2.0"))

        # 5. Inter-Slot Separation: MIN_SLOT_PRICE_DIFF_PCT (default: 0.8)
        min_slot_price_diff_pct = float(os.getenv("MIN_SLOT_PRICE_DIFF_PCT", "0.8"))

        # 6. Dynamic Execution:
        # TIMEFRAME: default "1m" (or "3m") for fast-paced scalping
        timeframe = os.getenv("TIMEFRAME", "1m").strip()

        # CHECK_INTERVAL_SECONDS: int, default 15
        check_sec_raw = os.getenv("CHECK_INTERVAL_SECONDS") or os.getenv("POLL_INTERVAL_SECONDS", "15")
        poll_interval_seconds = int(check_sec_raw)

        # TRAILING_STOP_ACTIVATION_PCT: float, default 0.8 (+0.8% profit triggers trailing)
        trailing_act_raw = float(os.getenv("TRAILING_STOP_ACTIVATION_PCT", "0.8"))
        trailing_stop_activation_pct = trailing_act_raw / 100.0 if trailing_act_raw >= 0.05 else trailing_act_raw

        # TRAILING_STOP_OFFSET_PCT: float, default 0.3 (locks profit on 0.3% retracement)
        trailing_offset_raw = float(os.getenv("TRAILING_STOP_OFFSET_PCT", "0.3"))
        trailing_stop_offset_pct = trailing_offset_raw / 100.0 if trailing_offset_raw >= 0.05 else trailing_offset_raw

        # RSI thresholds: RSI_OVERSOLD (default 36.0), RSI_OVERBOUGHT (default 68.0)
        rsi_oversold = float(os.getenv("RSI_OVERSOLD", "36.0"))
        rsi_overbought = float(os.getenv("RSI_OVERBOUGHT", "68.0"))

        # Stop-Loss and Take-Profit
        sl_raw = float(os.getenv("STOP_LOSS_PERCENT", "2.0"))
        stop_loss_pct = sl_raw / 100.0 if sl_raw >= 0.05 else sl_raw

        tp_raw = float(os.getenv("TAKE_PROFIT_PERCENT", "3.0"))
        take_profit_pct = tp_raw / 100.0 if tp_raw >= 0.05 else tp_raw

        return cls(
            mexc_api_key=api_key,
            mexc_api_secret=api_secret,
            trade_symbols=parsed_symbols,
            timeframe=timeframe,
            poll_interval_seconds=poll_interval_seconds,
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
            rsi_oversold=rsi_oversold,
            rsi_overbought=rsi_overbought,
            ema_period=int(os.getenv("EMA_PERIOD", "20")),
            atr_period=int(os.getenv("ATR_PERIOD", "14")),
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            trailing_stop_activation_pct=trailing_stop_activation_pct,
            trailing_stop_offset_pct=trailing_stop_offset_pct,
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or None,
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip() or None,
        )
