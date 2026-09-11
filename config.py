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

def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

@dataclass(frozen=True)
class TradingConfig:
    mexc_api_key: str
    mexc_api_secret: str
    trade_symbols: List[str] = field(default_factory=list)
    auto_select_symbols: bool = True
    auto_select_count: int = 4
    timeframe: str = "1m"
    poll_interval_seconds: int = 10
    slot_size_usdt: float = 2.0
    initial_max_slots: int = 4
    cash_reserve_usdt: float = 2.0
    min_slot_price_diff_pct: float = 0.006
    bollinger_period: int = 20
    bollinger_std: float = 2.0
    rsi_period: int = 14
    rsi_oversold: float = 42.0
    atr_period: int = 14
    stop_loss_pct: float = 0.012
    trailing_stop_activation_pct: float = 0.004
    trailing_stop_offset_pct: float = 0.002
    log_level: str = "INFO"
    simulation_mode: bool = False
    max_slippage_pct: float = 0.005
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    @property
    def trade_symbol(self) -> str:
        return self.trade_symbols[0] if self.trade_symbols else ""

    @classmethod
    def load_from_env(cls) -> "TradingConfig":
        simulation = _bool_env("SIMULATION_MODE", False)
        key = os.getenv("MEXC_API_KEY", "").strip()
        secret = os.getenv("MEXC_API_SECRET", "").strip()
        if not simulation and (not key or not secret):
            raise ValueError("MEXC_API_KEY and MEXC_API_SECRET are required when SIMULATION_MODE is false.")
        raw_pairs = os.getenv("TRADE_SYMBOLS") or os.getenv("TRADE_SYMBOL") or os.getenv("PAIR") or ""
        symbols = []
        for raw in raw_pairs.split(","):
            symbol = raw.strip().upper()
            if symbol and "/" not in symbol:
                symbol += "/USDT"
            if symbol and symbol not in symbols:
                symbols.append(symbol)
        auto_select = _bool_env("AUTO_SELECT_SYMBOLS", True)
        select_count = max(1, int(os.getenv("AUTO_SELECT_COUNT", "4")))
        if symbols and os.getenv("AUTO_SELECT_SYMBOLS") is None:
            auto_select = False
        slot_raw = os.getenv("SLOT_SIZE_USDT") or os.getenv("TRADE_AMOUNT_USDT") or "2"
        slot_size = float(str(slot_raw).replace("USDT", "").strip())
        max_slots = int(os.getenv("INITIAL_MAX_SLOTS") or os.getenv("MAX_OPEN_TRADES") or str(select_count))
        reserve = float(os.getenv("CASH_RESERVE_USDT", "2"))
        poll = int(os.getenv("CHECK_INTERVAL_SECONDS") or os.getenv("POLL_INTERVAL_SECONDS") or "10")
        return cls(
            mexc_api_key=key, mexc_api_secret=secret, trade_symbols=symbols,
            auto_select_symbols=auto_select, auto_select_count=select_count,
            timeframe=os.getenv("TIMEFRAME", "1m").strip(), poll_interval_seconds=max(1, poll),
            slot_size_usdt=slot_size, initial_max_slots=max(1, max_slots), cash_reserve_usdt=max(0.0, reserve),
            min_slot_price_diff_pct=_float_env("MIN_SLOT_PRICE_DIFF_PCT", default=0.006),
            bollinger_period=int(os.getenv("BOLLINGER_PERIOD", "20")), bollinger_std=float(os.getenv("BOLLINGER_STD", "2")),
            rsi_period=int(os.getenv("RSI_PERIOD", "14")), rsi_oversold=float(os.getenv("RSI_OVERSOLD", "42")),
            atr_period=int(os.getenv("ATR_PERIOD", "14")), stop_loss_pct=_float_env("STOP_LOSS_PCT", "STOP_LOSS_PERCENT", default=0.012, percent_names=("STOP_LOSS_PERCENT",)),
            trailing_stop_activation_pct=_float_env("TRAILING_STOP_ACTIVATION_PCT", default=0.004),
            trailing_stop_offset_pct=_float_env("TRAILING_STOP_OFFSET_PCT", default=0.002),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(), simulation_mode=simulation,
            max_slippage_pct=_float_env("MAX_SLIPPAGE_PCT", default=0.005),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or None,
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip() or None,
        )
