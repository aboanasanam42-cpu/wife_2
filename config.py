import os
import logging
from dataclasses import dataclass

def setup_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger

@dataclass
class TradingConfig:
    mexc_api_key: str
    mexc_api_secret: str
    trade_symbol: str = "BTC/USDT"
    trade_amount_usdt: float = 10.0
    timeframe: str = "15m"
    poll_interval_seconds: int = 30
    log_level: str = "INFO"
    simulation_mode: bool = False
    max_slippage_pct: float = 0.005

    # Strategy Parameters (Bollinger Bands + RSI + ATR)
    bollinger_period: int = 20
    bollinger_std: float = 2.0
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    ema_period: int = 20
    atr_period: int = 14

    # Trailing Stop & Risk Management
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.03
    trailing_stop_activation_pct: float = 0.01  # +1.0% triggers trailing
    trailing_stop_offset_pct: float = 0.005     # 0.5% trail offset

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @classmethod
    def load_from_env(cls):
        api_key = os.getenv("MEXC_API_KEY")
        api_secret = os.getenv("MEXC_API_SECRET")

        if not api_key or not api_secret:
            raise ValueError("MEXC_API_KEY and MEXC_API_SECRET must be set in environment variables.")

        raw_amount = os.getenv("TRADE_AMOUNT_USDT", "10").replace("USDT", "").replace("usdt", "").strip()

        return cls(
            mexc_api_key=api_key,
            mexc_api_secret=api_secret,
            trade_symbol=os.getenv("TRADE_SYMBOL", "BTC/USDT").strip(),
            trade_amount_usdt=float(raw_amount),
            timeframe=os.getenv("TIMEFRAME", "15m").strip(),
            poll_interval_seconds=int(os.getenv("CHECK_INTERVAL_SECONDS", "30")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper().strip(),
            simulation_mode=os.getenv("SIMULATION_MODE", "false").lower() == "true",
            bollinger_period=int(os.getenv("BOLLINGER_PERIOD", "20")),
            bollinger_std=float(os.getenv("BOLLINGER_STD", "2.0")),
            rsi_period=int(os.getenv("RSI_PERIOD", "14")),
            rsi_oversold=float(os.getenv("RSI_OVERSOLD", "30.0")),
            rsi_overbought=float(os.getenv("RSI_OVERBOUGHT", "70.0")),
            ema_period=int(os.getenv("EMA_PERIOD", "20")),
            atr_period=int(os.getenv("ATR_PERIOD", "14")),
            stop_loss_pct=float(os.getenv("STOP_LOSS_PERCENT", "2.0")) / 100.0,
            take_profit_pct=float(os.getenv("TAKE_PROFIT_PERCENT", "3.0")) / 100.0,
            trailing_stop_activation_pct=float(os.getenv("TRAILING_STOP_ACTIVATION_PCT", "1.0")) / 100.0,
            trailing_stop_offset_pct=float(os.getenv("TRAILING_STOP_OFFSET_PCT", "0.5")) / 100.0,
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        )
