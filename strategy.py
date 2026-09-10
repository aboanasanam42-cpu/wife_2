from dataclasses import dataclass
from typing import Optional, Dict, Any
import pandas as pd
import numpy as np

@dataclass
class SignalResult:
    action: str  # "BUY", "SELL", "HOLD"
    price: float
    rsi_value: float
    percent_b: float
    atr_value: float
    reason: str
    suggested_sl: Optional[float] = None
    suggested_tp: Optional[float] = None

class SpotStrategy:
    def __init__(
        self,
        rsi_period: int = 14,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        ema_period: int = 20,
        bollinger_period: int = 20,
        bollinger_std: float = 2.0,
        atr_period: int = 14,
        stop_loss_pct: float = 0.02,
        take_profit_pct: float = 0.03,
    ):
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.ema_period = ema_period
        self.bollinger_period = bollinger_period
        self.bollinger_std = bollinger_std
        self.atr_period = atr_period
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        # 1. Bollinger Bands
        sma = df["close"].rolling(window=self.bollinger_period).mean()
        std = df["close"].rolling(window=self.bollinger_period).std()
        df["bb_upper"] = sma + (std * self.bollinger_std)
        df["bb_lower"] = sma - (std * self.bollinger_std)
        df["bb_middle"] = sma
        
        # Bollinger %B: (Price - Lower) / (Upper - Lower)
        band_diff = df["bb_upper"] - df["bb_lower"]
        df["bb_percent_b"] = np.where(band_diff > 0, (df["close"] - df["bb_lower"]) / band_diff, 0.5)

        # 2. Fast RSI
        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.rsi_period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.rsi_period).mean()
        rs = gain / loss.replace(0, np.nan)
        df["rsi"] = 100 - (100 / (1 + rs))
        df["rsi"] = df["rsi"].fillna(50.0)

        # 3. EMA
        df["ema"] = df["close"].ewm(span=self.ema_period, adjust=False).mean()

        # 4. ATR (Average True Range)
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df["atr"] = true_range.rolling(window=self.atr_period).mean()
        df["atr"] = df["atr"].bfill()

        return df

    def evaluate_signals(self, df: pd.DataFrame, current_position: Optional[Dict[str, Any]] = None) -> SignalResult:
        last = df.iloc[-1]
        prev = df.iloc[-2]

        current_price = float(last["close"])
        rsi = float(last["rsi"])
        pct_b = float(last["bb_percent_b"])
        atr = float(last["atr"])
        prev_pct_b = float(prev["bb_percent_b"])

        # Check Active Position (Exit Evaluation)
        if current_position is not None and current_position.get("active"):
            entry_price = float(current_position["entry_price"])
            sl = float(current_position.get("stop_loss", entry_price * (1 - self.stop_loss_pct)))
            tp = float(current_position.get("take_profit", entry_price * (1 + self.take_profit_pct)))

            if current_price <= sl:
                return SignalResult(
                    action="SELL",
                    price=current_price,
                    rsi_value=rsi,
                    percent_b=pct_b,
                    atr_value=atr,
                    reason=f"Stop-Loss triggered at ${current_price:.2f} (SL: ${sl:.2f})"
                )

            if current_price >= tp:
                return SignalResult(
                    action="SELL",
                    price=current_price,
                    rsi_value=rsi,
                    percent_b=pct_b,
                    atr_value=atr,
                    reason=f"Take-Profit triggered at ${current_price:.2f} (TP: ${tp:.2f})"
                )

            # Reversal Exhaustion Exit (Overbought peak)
            if pct_b > 0.95 and rsi >= self.rsi_overbought:
                return SignalResult(
                    action="SELL",
                    price=current_price,
                    rsi_value=rsi,
                    percent_b=pct_b,
                    atr_value=atr,
                    reason=f"Exhaustion Peak Exit (%B: {pct_b:.2f}, RSI: {rsi:.1f})"
                )

            return SignalResult(
                action="HOLD",
                price=current_price,
                rsi_value=rsi,
                percent_b=pct_b,
                atr_value=atr,
                reason=f"Holding position. SL: ${sl:.2f} | TP: ${tp:.2f}"
            )

        # Entry Evaluation (Catching Local Bottoms)
        # Entry: %B bounces back above oversold (<0.08) or price touches lower band with low RSI
        is_dip = (pct_b < 0.10) or (prev_pct_b <= 0.05 and pct_b > prev_pct_b)
        is_rsi_oversold = rsi <= (self.rsi_oversold + 5)

        if is_dip and is_rsi_oversold:
            dynamic_sl = current_price - max(1.5 * atr, current_price * self.stop_loss_pct)
            dynamic_tp = current_price + max(2.5 * atr, current_price * self.take_profit_pct)

            return SignalResult(
                action="BUY",
                price=current_price,
                rsi_value=rsi,
                percent_b=pct_b,
                atr_value=atr,
                suggested_sl=dynamic_sl,
                suggested_tp=dynamic_tp,
                reason=f"Dynamic Dip Catch: %B={pct_b:.2f}, RSI={rsi:.1f}, ATR={atr:.2f}"
            )

        return SignalResult(
            action="HOLD",
            price=current_price,
            rsi_value=rsi,
            percent_b=pct_b,
            atr_value=atr,
            reason=f"Scanning market. %B: {pct_b:.2f}, RSI: {rsi:.1f}, ATR: {atr:.2f}"
        )
