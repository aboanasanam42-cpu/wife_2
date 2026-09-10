"""
Dynamic Volatility Scalping Strategy Module for MEXC Spot Trading.
Combines Bollinger Bands (%B), Relative Strength Index (RSI), Average True Range (ATR),
and dynamic trailing stop logic to detect local bottoms (dips) and tops (peaks).
"""

import logging
from dataclasses import dataclass
from typing import Optional
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
    bb_upper: float
    bb_middle: float
    bb_lower: float
    bb_pct_b: float
    atr_value: float
    suggested_sl: Optional[float] = None
    suggested_tp: Optional[float] = None
    trailing_activation: Optional[float] = None


class SpotStrategy:
    def __init__(
        self,
        bollinger_period: int = 20,
        bollinger_std: float = 2.0,
        rsi_period: int = 14,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        atr_period: int = 14,
        atr_multiplier_sl: float = 1.5,
        stop_loss_pct: float = 0.015,
        take_profit_pct: float = 0.025,
        trailing_stop_activation_pct: float = 0.01,
        trailing_stop_offset_pct: float = 0.005,
        ema_period: int = 20,
    ):
        self.bollinger_period = bollinger_period
        self.bollinger_std = bollinger_std
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.atr_period = atr_period
        self.atr_multiplier_sl = atr_multiplier_sl
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.trailing_stop_activation_pct = trailing_stop_activation_pct
        self.trailing_stop_offset_pct = trailing_stop_offset_pct
        self.ema_period = ema_period

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculates Bollinger Bands (Upper, Middle, Lower, %B), RSI, and ATR.
        Fully vectorized with native fallback if external libraries fail.
        """
        df = df.copy()

        # 1. Bollinger Bands (20, 2.0)
        close = df["close"]
        bb_mid = close.rolling(window=self.bollinger_period).mean()
        bb_std = close.rolling(window=self.bollinger_period).std(ddof=0)
        bb_upper = bb_mid + (self.bollinger_std * bb_std)
        bb_lower = bb_mid - (self.bollinger_std * bb_std)

        # Avoid zero division in %B
        bandwidth = (bb_upper - bb_lower).replace(0, np.nan)
        bb_pct_b = (close - bb_lower) / bandwidth

        df["BBM"] = bb_mid
        df["BBU"] = bb_upper
        df["BBL"] = bb_lower
        df["BBP"] = bb_pct_b.fillna(0.5)

        # 2. RSI Calculation
        if HAS_PANDAS_TA:
            try:
                rsi_series = df.ta.rsi(close="close", length=self.rsi_period)
                if rsi_series is not None and not rsi_series.empty:
                    df["RSI"] = rsi_series
                else:
                    df["RSI"] = self._calculate_rsi(close, self.rsi_period)
            except Exception as e:
                logger.debug("pandas_ta rsi error: %s, using fallback", e)
                df["RSI"] = self._calculate_rsi(close, self.rsi_period)
        else:
            df["RSI"] = self._calculate_rsi(close, self.rsi_period)

        # 3. ATR Calculation (14)
        high = df["high"]
        low = df["low"]
        prev_close = close.shift(1)

        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # Wilder's Smoothing for ATR
        df["ATR"] = tr.ewm(alpha=1.0 / self.atr_period, min_periods=self.atr_period, adjust=False).mean()

        # Optional 20 EMA for directional context
        df["EMA"] = close.ewm(span=self.ema_period, adjust=False).mean()

        return df

    @staticmethod
    def _calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
        """Native vectorized pandas implementation of Wilder's RSI."""
        delta = series.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)

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
        Evaluates dynamic scalp entries and exits.

        Dynamic Dip Entry (BUY):
        - In Cash (no position).
        - Price dips below Lower Bollinger Band (%B < 0.05 or crossed back up > 0.0).
        - RSI is oversold (<= rsi_oversold) or rebounding from oversold.
        - Calculated dynamic ATR-based Stop-Loss: Entry Price - (1.5 * ATR).
        - Calculated Trailing Stop Activation price.

        Dynamic Peak Exit (SELL):
        - In Position:
          1. Stop-Loss hit (either initial ATR SL or trailed profit floor).
          2. Fixed Take-Profit ceiling hit (Entry * (1 + take_profit_pct)).
          3. Dynamic Peak exhaustion: Price pierces Upper Bollinger Band (%B > 0.95)
             AND RSI hits overbought exhaustion (>= rsi_overbought).
        """
        min_required = max(self.bollinger_period, self.rsi_period, self.atr_period) + 2
        if len(df) < min_required:
            last_close = float(df["close"].iloc[-1]) if not df.empty else 0.0
            return TradeSignal(
                action="HOLD",
                price=last_close,
                reason="Accumulating historical candles for Bollinger and ATR calculations",
                rsi_value=50.0,
                bb_upper=last_close,
                bb_middle=last_close,
                bb_lower=last_close,
                bb_pct_b=0.5,
                atr_value=0.0,
            )

        latest = df.iloc[-1]
        previous = df.iloc[-2]

        current_close = float(latest["close"])
        current_rsi = float(latest["RSI"]) if not pd.isna(latest["RSI"]) else 50.0
        prev_rsi = float(previous["RSI"]) if not pd.isna(previous["RSI"]) else 50.0

        bbu = float(latest["BBU"])
        bbm = float(latest["BBM"])
        bbl = float(latest["BBL"])
        bb_pct_b = float(latest["BBP"])
        prev_bb_pct_b = float(previous["BBP"])

        atr = float(latest["ATR"]) if not pd.isna(latest["ATR"]) else (current_close * 0.01)

        # -----------------------------------------------------------------
        # EVALUATE ACTIVE POSITION EXITS (SL / TP / Trailing / BB Exhaustion)
        # -----------------------------------------------------------------
        if current_position and current_position.get("active", False):
            entry_price = float(current_position["entry_price"])
            current_sl = float(current_position.get("stop_loss", entry_price * (1.0 - self.stop_loss_pct)))
            current_tp = float(current_position.get("take_profit", entry_price * (1.0 + self.take_profit_pct)))
            highest_seen = float(current_position.get("highest_price_seen", entry_price))
            trailing_active = bool(current_position.get("trailing_active", False))

            # 1. Stop-Loss Trigger (Active SL or Trailed Floor)
            if current_close <= current_sl:
                pct = ((current_close - entry_price) / entry_price) * 100.0
                trigger_type = "Trailed Stop-Loss Floor" if trailing_active else "Dynamic ATR Stop-Loss"
                return TradeSignal(
                    action="SELL",
                    price=current_close,
                    reason=f"{trigger_type} triggered at ${current_close:,.2f} ({pct:+.2f}% vs Entry ${entry_price:,.2f})",
                    rsi_value=current_rsi,
                    bb_upper=bbu,
                    bb_middle=bbm,
                    bb_lower=bbl,
                    bb_pct_b=bb_pct_b,
                    atr_value=atr,
                    suggested_sl=current_sl,
                    suggested_tp=current_tp,
                )

            # 2. Maximum Take-Profit Ceiling Trigger
            if current_close >= current_tp:
                pct = ((current_close - entry_price) / entry_price) * 100.0
                return TradeSignal(
                    action="SELL",
                    price=current_close,
                    reason=f"Take-Profit target hit at ${current_close:,.2f} (+{pct:.2f}% vs Entry ${entry_price:,.2f})",
                    rsi_value=current_rsi,
                    bb_upper=bbu,
                    bb_middle=bbm,
                    bb_lower=bbl,
                    bb_pct_b=bb_pct_b,
                    atr_value=atr,
                    suggested_sl=current_sl,
                    suggested_tp=current_tp,
                )

            # 3. Dynamic Peak Exit: Bollinger Piercing + RSI Overbought
            if (bb_pct_b >= 0.95 or current_close >= bbu) and (current_rsi >= self.rsi_overbought):
                pct = ((current_close - entry_price) / entry_price) * 100.0
                if pct > 0:  # Lock profits on top peak exhaustion
                    return TradeSignal(
                        action="SELL",
                        price=current_close,
                        reason=f"Peak Exhaustion: %B={bb_pct_b:.2f} >= 0.95 & RSI={current_rsi:.1f} >= {self.rsi_overbought:.1f} (+{pct:.2f}%)",
                        rsi_value=current_rsi,
                        bb_upper=bbu,
                        bb_middle=bbm,
                        bb_lower=bbl,
                        bb_pct_b=bb_pct_b,
                        atr_value=atr,
                        suggested_sl=current_sl,
                        suggested_tp=current_tp,
                    )

            # Otherwise, keep position active
            pnl_current = ((current_close - entry_price) / entry_price) * 100.0
            trail_status = f"Trailing Active (Floor: ${current_sl:,.2f})" if trailing_active else f"SL: ${current_sl:,.2f}"
            return TradeSignal(
                action="HOLD",
                price=current_close,
                reason=f"Holding Spot: Current PnL {pnl_current:+.2f}% | %B: {bb_pct_b:.2f} | RSI: {current_rsi:.1f} | {trail_status}",
                rsi_value=current_rsi,
                bb_upper=bbu,
                bb_middle=bbm,
                bb_lower=bbl,
                bb_pct_b=bb_pct_b,
                atr_value=atr,
                suggested_sl=current_sl,
                suggested_tp=current_tp,
            )

        # -----------------------------------------------------------------
        # EVALUATE ENTRY SIGNALS (BUY DIPS)
        # -----------------------------------------------------------------
        # Dip Condition: Price touching/piercing Lower Bollinger Band (%B < 0.05 or rebounding from dip)
        is_bb_dip = (bb_pct_b <= 0.05) or (prev_bb_pct_b <= 0.05 and bb_pct_b > prev_bb_pct_b)
        is_rsi_oversold = (current_rsi <= self.rsi_oversold) or (prev_rsi <= self.rsi_oversold and current_rsi > prev_rsi)

        if is_bb_dip and is_rsi_oversold:
            # Dynamic Stop Loss: Max of (Entry - 1.5 * ATR) or percentage floor
            atr_sl_delta = self.atr_multiplier_sl * atr
            dynamic_sl = max(current_close - atr_sl_delta, current_close * (1.0 - (self.stop_loss_pct * 1.5)))
            dynamic_tp = current_close * (1.0 + self.take_profit_pct)
            trailing_activation_price = current_close * (1.0 + self.trailing_stop_activation_pct)

            reason_str = (
                f"Dynamic Dip Detected: %B={bb_pct_b:.2f} <= 0.05 & RSI={current_rsi:.1f} <= {self.rsi_oversold:.1f} "
                f"(ATR={atr:,.2f} -> SL: ${dynamic_sl:,.2f})"
            )

            return TradeSignal(
                action="BUY",
                price=current_close,
                reason=reason_str,
                rsi_value=current_rsi,
                bb_upper=bbu,
                bb_middle=bbm,
                bb_lower=bbl,
                bb_pct_b=bb_pct_b,
                atr_value=atr,
                suggested_sl=dynamic_sl,
                suggested_tp=dynamic_tp,
                trailing_activation=trailing_activation_price,
            )

        # Neutral state
        return TradeSignal(
            action="HOLD",
            price=current_close,
            reason=f"Scanning: %B={bb_pct_b:.2f}, RSI={current_rsi:.1f}, ATR=${atr:,.2f}",
            rsi_value=current_rsi,
            bb_upper=bbu,
            bb_middle=bbm,
            bb_lower=bbl,
            bb_pct_b=bb_pct_b,
            atr_value=atr,
        )
