import os


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_float(name: str, default: float = 0.0) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return float(value)


def env_int(name: str, default: int = 0) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return int(value)


MEXC_API_KEY = os.getenv("MEXC_API_KEY", "").strip()
MEXC_API_SECRET = os.getenv("MEXC_API_SECRET", "").strip()

SYMBOL = "MX/USDT"
TIMEFRAME = "1m"

TRADE_AMOUNT_USDT = env_float("TRADE_AMOUNT_USDT", 2.5)

# Railway name first, old name as fallback.
LOOP_INTERVAL_SECONDS = env_int(
    "CHECK_INTERVAL_SECONDS",
    env_int("LOOP_INTERVAL_SECONDS", 10),
)

MAX_POSITIONS = env_int(
    "MAX_OPEN_TRADES",
    env_int("MAX_POSITIONS", 1),
)

CASH_RESERVE_USDT = env_float("CASH_RESERVE_USDT", 0.0)

RSI_PERIOD = env_int("RSI_PERIOD", 14)

STOP_LOSS_PCT = env_float(
    "STOP_LOSS_PCT",
    env_float("STOP_LOSS_PERCENT", 0.40),
)

TRAILING_STOP_ACTIVATION_PCT = env_float(
    "TRAILING_STOP_ACTIVATION_PCT",
    0.15,
)

TRAILING_STOP_OFFSET_PCT = env_float(
    "TRAILING_STOP_OFFSET_PCT",
    0.20,
)

MIN_SLOT_PRICE_DIFF_PCT = env_float(
    "MIN_SLOT_PRICE_DIFF_PCT",
    0.60,
)

BUY_COOLDOWN_SEC = env_int("BUY_COOLDOWN_SEC", 30)

TRAILING_CONFIRMATION_CANDLES = max(
    1,
    env_int("TRAILING_CONFIRMATION_CANDLES", 1),
)

MAX_HOLD_TIME_SEC = env_int("MAX_HOLD_TIME_SEC", 1200)

LIVE_TRADING = env_bool("LIVE_TRADING", False)

STATE_FILE = os.getenv(
    "STATE_FILE",
    "/app/data/positions.json",
).strip()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
