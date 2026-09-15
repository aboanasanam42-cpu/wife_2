import os


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)

    if value is None or value.strip() == "":
        return default

    return float(value)


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)

    if value is None or value.strip() == "":
        return default

    return int(value)


MEXC_API_KEY = os.getenv("MEXC_API_KEY", "").strip()
MEXC_API_SECRET = os.getenv("MEXC_API_SECRET", "").strip()

SYMBOL = os.getenv("SYMBOL", "BTC/USDT").strip()

TIMEFRAME = os.getenv("TIMEFRAME", "1m").strip()

TRADE_AMOUNT_USDT = env_float(
    "TRADE_AMOUNT_USDT",
    5.5,
)

LOOP_INTERVAL_SECONDS = env_int(
    "LOOP_INTERVAL_SECONDS",
    10,
)

BUY_DIP_MIN_PCT = env_float(
    "BUY_DIP_MIN_PCT",
    0.25,
)

BUY_REBOUND_CONFIRM_PCT = env_float(
    "BUY_REBOUND_CONFIRM_PCT",
    0.08,
)

BUY_RSI_MAX = env_float(
    "BUY_RSI_MAX",
    48.0,
)

MIN_VOLATILITY_PCT = env_float(
    "MIN_VOLATILITY_PCT",
    0.20,
)

MAX_SPREAD_PCT = env_float(
    "MAX_SPREAD_PCT",
    0.15,
)

TRAILING_STOP_ENABLED = env_bool(
    "TRAILING_STOP_ENABLED",
    True,
)

TRAILING_ACTIVATION_PCT = env_float(
    "TRAILING_ACTIVATION_PCT",
    0.15,
)

TRAILING_CALLBACK_PCT = env_float(
    "TRAILING_CALLBACK_PCT",
    0.20,
)

STOP_LOSS_PCT = env_float(
    "STOP_LOSS_PCT",
    0.40,
)

MAX_HOLD_TIME_SEC = env_int(
    "MAX_HOLD_TIME_SEC",
    1200,
)

STAGNANT_EXIT_PCT = env_float(
    "STAGNANT_EXIT_PCT",
    0.0,
)

BUY_COOLDOWN_SEC = env_int(
    "BUY_COOLDOWN_SEC",
    30,
)

MAX_POSITIONS = env_int(
    "MAX_POSITIONS",
    1,
)

LIVE_TRADING = env_bool(
    "LIVE_TRADING",
    False,
)

STATE_FILE = os.getenv(
    "STATE_FILE",
    "/app/data/positions.json",
).strip()
