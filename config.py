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


def env_float(name: str, default: float = 0.0) -> float:
    value = os.getenv(name)

    if value is None or not value.strip():
        return default

    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(
            f"Invalid float value for {name}: {value!r}"
        ) from exc


def env_int(name: str, default: int = 0) -> int:
    value = os.getenv(name)

    if value is None or not value.strip():
        return default

    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(
            f"Invalid integer value for {name}: {value!r}"
        ) from exc


# ============================================================
# MEXC
# ============================================================

MEXC_API_KEY = os.getenv(
    "MEXC_API_KEY",
    "",
).strip()

MEXC_API_SECRET = os.getenv(
    "MEXC_API_SECRET",
    "",
).strip()


# ============================================================
# MARKET
# ============================================================

# IMPORTANT:
# This bot is Spot only.
SYMBOL = os.getenv(
    "SYMBOL",
    "MX/USDT",
).strip().upper()

TIMEFRAME = os.getenv(
    "TIMEFRAME",
    "1m",
).strip()


# ============================================================
# TRADING
# ============================================================

TRADE_AMOUNT_USDT = env_float(
    "TRADE_AMOUNT_USDT",
    2.5,
)

MEXC_BUY_FEE_RATE = env_float(
    "MEXC_BUY_FEE_RATE",
    0.001,
)

MEXC_SELL_FEE_RATE = env_float(
    "MEXC_SELL_FEE_RATE",
    0.001,
)

TARGET_NET_PROFIT_RATE = env_float(
    "TARGET_NET_PROFIT_RATE",
    MEXC_BUY_FEE_RATE,
)

MAX_POSITIONS = max(
    1,
    env_int(
        "MAX_POSITIONS",
        1,
    ),
)

CASH_RESERVE_USDT = max(
    0.0,
    env_float(
        "CASH_RESERVE_USDT",
        0.0,
    ),
)


# ============================================================
# LIVE MODE
# ============================================================

# FALSE = no real orders.
# TRUE = real MEXC Spot orders.
#
# Set this ONLY in Railway Variables after verifying
# the API key has Spot Trading permission and NO withdrawal
# permission.
LIVE_TRADING = env_bool(
    "LIVE_TRADING",
    False,
)


# ============================================================
# LOOP
# ============================================================

LOOP_INTERVAL_SECONDS = max(
    2,
    env_int(
        "LOOP_INTERVAL_SECONDS",
        env_int(
            "CHECK_INTERVAL_SECONDS",
            10,
        ),
    ),
)

BUY_COOLDOWN_SEC = max(
    0,
    env_int(
        "BUY_COOLDOWN_SEC",
        30,
    ),
)


# ============================================================
# STRATEGY
# ============================================================

RSI_PERIOD = max(
    2,
    env_int(
        "RSI_PERIOD",
        14,
    ),
)

STOP_LOSS_PCT = max(
    0.0,
    env_float(
        "STOP_LOSS_PCT",
        0.40,
    ),
)

TRAILING_STOP_ACTIVATION_PCT = max(
    0.0,
    env_float(
        "TRAILING_STOP_ACTIVATION_PCT",
        0.15,
    ),
)

TRAILING_STOP_OFFSET_PCT = max(
    0.0,
    env_float(
        "TRAILING_STOP_OFFSET_PCT",
        0.20,
    ),
)

TRAILING_CONFIRMATION_CANDLES = max(
    1,
    env_int(
        "TRAILING_CONFIRMATION_CANDLES",
        1,
    ),
)

MAX_HOLD_TIME_SEC = max(
    0,
    env_int(
        "MAX_HOLD_TIME_SEC",
        1200,
    ),
)

MIN_SLOT_PRICE_DIFF_PCT = max(
    0.0,
    env_float(
        "MIN_SLOT_PRICE_DIFF_PCT",
        0.0003,
    ),
)


# ============================================================
# STATE
# ============================================================

STATE_FILE = os.getenv(
    "STATE_FILE",
    "/app/data/positions.json",
).strip()


# ============================================================
# LOGGING
# ============================================================

LOG_LEVEL = os.getenv(
    "LOG_LEVEL",
    "INFO",
).strip().upper()
