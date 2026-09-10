"""
Technical Analysis and Signal Generation Strategy for MEXC Spot Trading.
Computes RSI, EMA indicators, evaluates trend filters, and manages Stop-Loss / Take-Profit logic.
"""

import logging
from dataclasses import dataclass
from typing import Optional, Tuple
import pandas as pd
import numpy as np

try:
    import pandas_ta as ta
    HAS_PANDAS_TA = True
except ImportError:
    HAS_PANDAS_TA = False

logger = logging.getLogger("mexc_trader.strategy")


@dataclass
class TradeSignal:
    action: str  # "BUY", "SELL", or "HOLD"
    price: float
    reason: str
    rsi_value: float
    ema_value: float
    suggested_sl: Optional[float] = None
    suggested_tp: Optional[float] = None


class SpotStrategy:
    def __init__(
        self,
        rsi_period: int = 14,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        ema_period: int = 20,
        stop_loss_pct: float = 0.02,
        take_profit_pct: float = 0.04,
    ):
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.ema_period = ema_period
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculates EMA and RSI on closed OHLCV data.
        Ensures high reliability with fallback if pandas_ta has native binding issues.
        """
        df = df.copy()

        # Calculate Exponential Moving Average (EMA)
        df[f"EMA_{self.ema_period}"] = df["close"].ewm(span=self.ema_period, adjust=False).mean()

        # Calculate Relative Strength Index (RSI)
        if HAS_PANDAS_TA:
            try:
                rsi_series = df.ta.rsi(close="close", length=self.rsi_period)
                if rsi_series is not None and not rsi_series.empty:
                    df[f"RSI_{self.rsi_period}"] = rsi_series
                else:
                    df[f"RSI_{self.rsi_period}"] = self._fallback_rsi(df["close"], self.rsi_period)
            except Exception as e:
                logger.debug("pandas_ta rsi error (%s), using native numpy fallback", e)
                df[f"RSI_{self.rsi_period}"] = self._fallback_rsi(df["close"], self.rsi_period)
        else:
            df[f"RSI_{self.rsi_period}"] = self._fallback_rsi(df["close"], self.rsi_period)

        return df

    @staticmethod
    def _fallback_rsi(series: pd.Series, period: int = 14) -> pd.Series:
        """Native pandas implementation of Wilder's RSI."""
        delta = series.diff()
        gain = (delta.where(delta > 0, 0.0)).copy()
        loss = (-delta.where(delta < 0, 0.0)).copy()

        # Exponential smoothing (Wilder's moving average)
        avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return rsi.fillna(50.0)

    def evaluate_signals(
        self,
        df: pd.DataFrame,
        current_position: Optional[dict] = None,
    ) -> TradeSignal:
        """
        Evaluates trading signals on the latest closed candle.
        
        Rules:
        1. BUY (Entry):
           - In cash (current_position is None).
           - Closed price is above EMA(20) [Trend confirmation].
           - RSI(14) was recently oversold (<= rsi_oversold) and turning up, or current RSI <= rsi_oversold.
        2. SELL (Exit):
           - In active position.
           - Stop-Loss hit: Current price <= entry_price * (1 - SL_PCT)
           - Take-Profit hit: Current price >= entry_price * (1 + TP_PCT)
           - Overbought Exit: RSI(14) >= rsi_overbought.
        3. HOLD:
           - No entry or exit conditions satisfied.
        """
        if len(df) < max(self.rsi_period, self.ema_period) + 2:
            return TradeSignal(
                action="HOLD",
                price=float(df["close"].iloc[-1]) if not df.empty else 0.0,
                reason="Insufficient historical candles to calculate indicators",
                rsi_value=50.0,
                ema_value=0.0,
            )

        # Work on the last COMPLETED candle (index -1)
        latest = df.iloc[-1]
        previous = df.iloc[-2]

        current_close = float(latest["close"])
        rsi_col = f"RSI_{self.rsi_period}"
        ema_col = f"EMA_{self.ema_period}"

        current_rsi = float(latest[rsi_col])
        prev_rsi = float(previous[rsi_col])
        current_ema = float(latest[ema_col])

        # If currently holding a Spot Position, evaluate Risk Management (SL/TP) and Overbought exits
        if current_position and current_position.get("active", False):
            entry_price = float(current_position["entry_price"])
            sl_price = entry_price * (1.0 - self.stop_loss_pct)
            tp_price = entry_price * (1.0 + self.take_profit_pct)

            # 1. Stop Loss Check
            if current_close <= sl_price:
                pct_loss = ((current_close - entry_price) / entry_price) * 100.0
                return TradeSignal(
                    action="SELL",
                    price=current_close,
                    reason=f"Stop-Loss triggered at ${current_close:,.2f} ({pct_loss:.2f}% vs Entry ${entry_price:,.2f})",
                    rsi_value=current_rsi,
                    ema_value=current_ema,
                )

            # 2. Take Profit Check
            if current_close >= tp_price:
                pct_gain = ((current_close - entry_price) / entry_price) * 100.0
                return TradeSignal(
                    action="SELL",
                    price=current_close,
                    reason=f"Take-Profit target achieved at ${current_close:,.2f} (+{pct_gain:.2f}% vs Entry ${entry_price:,.2f})",
                    rsi_value=current_rsi,
                    ema_value=current_ema,
                )

            # 3. Technical Indicator Overbought Exit
            if current_rsi >= self.rsi_overbought:
                return TradeSignal(
                    action="SELL",
                    price=current_close,
                    reason=f"RSI Overbought ({current_rsi:.1f} >= {self.rsi_overbought:.1f})",
                    rsi_value=current_rsi,
                    ema_value=current_ema,
                )

            # Keep holding
            return TradeSignal(
                action="HOLD",
                price=current_close,
                reason=f"In active position (Entry: ${entry_price:,.2f}, RSI: {current_rsi:.1f}, SL: ${sl_price:,.2f}, TP: ${tp_price:,.2f})",
                rsi_value=current_rsi,
                ema_value=current_ema,
            )

        # If in Cash (no active position), check for ENTRY (BUY) signal
        is_uptrend = current_close >= current_ema
        is_oversold_reversal = (prev_rsi <= self.rsi_oversold and current_rsi > prev_rsi) or (current_rsi <= self.rsi_oversold)

        if is_uptrend and is_oversold_reversal:
            suggested_sl = current_close * (1.0 - self.stop_loss_pct)
            suggested_tp = current_close * (1.0 + self.take_profit_pct)
            return TradeSignal(
                action="BUY",
                price=current_close,
                reason=f"Bullish Setup: RSI oversold ({current_rsi:.1f} <= {self.rsi_oversold}) & Price (${current_close:,.2f}) above 20 EMA (${current_ema:,.2f})",
                rsi_value=current_rsi,
                ema_value=current_ema,
                suggested_sl=suggested_sl,
                suggested_tp=suggested_tp,
            )

        return TradeSignal(
            action="HOLD",
            price=current_close,
            reason=f"Awaiting signal. Price: ${current_close:,.2f}, RSI: {current_rsi:.1f}, 20 EMA: ${current_ema:,.2f}",
            rsi_value=current_rsi,
            ema_value=current_ema,
        )
