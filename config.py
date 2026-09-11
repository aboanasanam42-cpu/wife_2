import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def setup_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"))
        logger.addHandler(handler)
    logger.propagate = False
    return logger


def _float_env(*names: str, default: float, percent_names=()) -> float:
    for name in names:
        raw = os.getenv(name)
        if raw not in (None, ""):
            value = float(raw)
            if name in percent_names or name.endswith("_PCT"):
                if abs(value) >= 0.05:
                    value /= 100.0
            return value
    return default


@dataclass(frozen=True)
class TradingConfig:
    mexc_api_key: str
    mexc_api_secret: str
    trade_symbols: List[str] = field(default_factory=lambda: ["SOL/USDT", "DOGE/USDT"])
    timeframe: str = "1m"
    poll_interval_seconds: int = 10
    slot_size_usdt: float = 4.0
    initial_max_slots: int = 2
    cash_reserve_usdt: float = 2.0
    min_slot_price_diff_pct: float = 0.006
    bollinger_period: int = 20
    bollinger_std: float = 2.0
    rsi_period: int = 14
    rsi_oversold: float = 38.0
    atr_period: int = 14
    stop_loss_pct: float = 0.02
    trailing_stop_activation_pct: float = 0.008
    trailing_stop_offset_pct: float = 0.003
    log_level: str = "INFO"
    simulation_mode: bool = False
    max_slippage_pct: float = 0.005
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    @property
    def trade_symbol(self) -> str:
        return self.trade_symbols[0]

    @classmethod
    def load_from_env(cls) -> "TradingConfig":
        simulation = os.getenv("SIMULATION_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}
        key = os.getenv("MEXC_API_KEY", "").strip()
        secret = os.getenv("MEXC_API_SECRET", "").strip()
        if not simulation and (not key or not secret):
            raise ValueError("MEXC_API_KEY and MEXC_API_SECRET are required when SIMULATION_MODE is false.")

        raw_pairs = os.getenv("TRADE_SYMBOLS") or os.getenv("TRADE_SYMBOL") or os.getenv("PAIR") or "SOL/USDT,DOGE/USDT"
        symbols = []
        for raw in raw_pairs.split(","):
            symbol = raw.strip().upper()
            if symbol and "/" not in symbol:
                symbol += "/USDT"
            if symbol and symbol not in symbols:
                symbols.append(symbol)
        symbols = symbols or ["SOL/USDT", "DOGE/USDT"]

        slot_raw = os.getenv("SLOT_SIZE_USDT") or os.getenv("TRADE_AMOUNT_USDT") or "4"
        slot_size = float(str(slot_raw).replace("USDT", "").strip())
        max_slots = int(os.getenv("INITIAL_MAX_SLOTS") or os.getenv("MAX_OPEN_TRADES") or "2")
        reserve = float(os.getenv("CASH_RESERVE_USDT", "2"))
        poll = int(os.getenv("CHECK_INTERVAL_SECONDS") or os.getenv("POLL_INTERVAL_SECONDS") or "10")

        return cls(
            mexc_api_key=key,
            mexc_api_secret=secret,
            trade_symbols=symbols,
            timeframe=os.getenv("TIMEFRAME", "1m").strip(),
            poll_interval_seconds=max(1, poll),
            slot_size_usdt=slot_size,
            initial_max_slots=max(1, max_slots),
            cash_reserve_usdt=max(0.0, reserve),
            min_slot_price_diff_pct=_float_env("MIN_SLOT_PRICE_DIFF_PCT", default=0.006),
            bollinger_period=int(os.getenv("BOLLINGER_PERIOD", "20")),
            bollinger_std=float(os.getenv("BOLLINGER_STD", "2")),
            rsi_period=int(os.getenv("RSI_PERIOD", "14")),
            rsi_oversold=float(os.getenv("RSI_OVERSOLD", "38")),
            atr_period=int(os.getenv("ATR_PERIOD", "14")),
            stop_loss_pct=_float_env("STOP_LOSS_PCT", "STOP_LOSS_PERCENT", default=0.02, percent_names=("STOP_LOSS_PERCENT",)),
            trailing_stop_activation_pct=_float_env("TRAILING_STOP_ACTIVATION_PCT", default=0.008),
            trailing_stop_offset_pct=_float_env("TRAILING_STOP_OFFSET_PCT", default=0.003),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            simulation_mode=simulation,
            max_slippage_pct=_float_env("MAX_SLIPPAGE_PCT", default=0.005),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or None,
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip() or None,
        )
