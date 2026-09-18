import os
from dotenv import load_dotenv

load_dotenv()


def _float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default)).strip()

    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"{name} must be a valid number. Received: {raw!r}"
        ) from exc

    if value < 0:
        raise ValueError(f"{name} cannot be negative.")

    return value


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()

    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"{name} must be a valid integer. Received: {raw!r}"
        ) from exc

    if value < 0:
        raise ValueError(f"{name} cannot be negative.")

    return value


class Config:
    """
    Central configuration for the MEXC Spot trading worker.

    SYMBOLS=ALL_USDT:
        Dynamically uses all enabled MEXC Spot markets quoted in USDT.

    LIVE_TRADING=false:
        No real orders are sent. This is the safe default.
    """

    API_KEY = os.getenv("MEXC_API_KEY", "").strip()
    API_SECRET = os.getenv("MEXC_API_SECRET", "").strip()

    BASE_URL = (
        os.getenv(
            "MEXC_BASE_URL",
            "https://api.mexc.com",
        )
        .strip()
        .rstrip("/")
    )

    # ---------------------------------------------------------
    # SYMBOL UNIVERSE
    # ---------------------------------------------------------

    SYMBOLS = os.getenv(
        "SYMBOLS",
        os.getenv("SYMBOL", "ALL_USDT"),
    ).strip().upper()

    # ---------------------------------------------------------
    # TRADING
    # ---------------------------------------------------------

    TRADE_AMOUNT_USDT = _float(
        "TRADE_AMOUNT_USDT",
        6.0,
    )

    MAX_OPEN_POSITIONS = _int(
        "MAX_OPEN_POSITIONS",
        3,
    )

    # ---------------------------------------------------------
    # RISK
    # ---------------------------------------------------------

    TAKE_PROFIT_PCT = _float(
        "TAKE_PROFIT_PCT",
        1.5,
    )

    STOP_LOSS_PCT = _float(
        "STOP_LOSS_PCT",
        2.0,
    )

    # ---------------------------------------------------------
    # STRATEGY
    # ---------------------------------------------------------

    TIMEFRAME = os.getenv(
        "TIMEFRAME",
        "1m",
    ).strip()

    CANDLE_LIMIT = _int(
        "CANDLE_LIMIT",
        100,
    )

    RSI_PERIOD = _int(
        "RSI_PERIOD",
        14,
    )

    BUY_RSI_MAX = _float(
        "BUY_RSI_MAX",
        38.0,
    )

    BUY_REBOUND_PCT = _float(
        "BUY_REBOUND_PCT",
        0.05,
    )

    # ---------------------------------------------------------
    # ALL_USDT SCANNING
    # ---------------------------------------------------------

    MAX_SCAN_SYMBOLS = _int(
        "MAX_SCAN_SYMBOLS",
        60,
    )

    SYMBOLS_REFRESH_MINUTES = _int(
        "SYMBOLS_REFRESH_MINUTES",
        30,
    )

    REQUEST_DELAY = _float(
        "REQUEST_DELAY",
        0.15,
    )

    POLL_INTERVAL = _float(
        "POLL_INTERVAL",
        10.0,
    )

    # ---------------------------------------------------------
    # LIVE MODE
    # ---------------------------------------------------------

    LIVE_TRADING = (
        os.getenv(
            "LIVE_TRADING",
            "true",
        )
        .strip()
        .lower()
        in {
            "1",
            "true",
            "yes",
            "on",
        }
    )

    # ---------------------------------------------------------
    # VALIDATION
    # ---------------------------------------------------------

    @classmethod
    def validate(cls) -> None:
        if not cls.API_KEY:
            raise ValueError(
                "MEXC_API_KEY is not configured."
            )

        if not cls.API_SECRET:
            raise ValueError(
                "MEXC_API_SECRET is not configured."
            )

        if not cls.BASE_URL.startswith("http"):
            raise ValueError(
                "MEXC_BASE_URL must be a valid HTTP/HTTPS URL."
            )

        if cls.TRADE_AMOUNT_USDT <= 0:
            raise ValueError(
                "TRADE_AMOUNT_USDT must be greater than 0."
            )

        if cls.MAX_OPEN_POSITIONS < 1:
            raise ValueError(
                "MAX_OPEN_POSITIONS must be at least 1."
            )

        if cls.TAKE_PROFIT_PCT <= 0:
            raise ValueError(
                "TAKE_PROFIT_PCT must be greater than 0."
            )

        if cls.STOP_LOSS_PCT <= 0:
            raise ValueError(
                "STOP_LOSS_PCT must be greater than 0."
            )

        if cls.CANDLE_LIMIT < cls.RSI_PERIOD + 5:
            raise ValueError(
                "CANDLE_LIMIT is too small for RSI calculation."
            )

        if cls.MAX_SCAN_SYMBOLS < 1:
            raise ValueError(
                "MAX_SCAN_SYMBOLS must be at least 1."
            )

        if cls.SYMBOLS_REFRESH_MINUTES < 1:
            raise ValueError(
                "SYMBOLS_REFRESH_MINUTES must be at least 1."
            )

        if cls.POLL_INTERVAL < 0:
            raise ValueError(
                "POLL_INTERVAL cannot be negative."
            )
