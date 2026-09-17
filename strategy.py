from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


def calculate_take_profit_price(
    buy_price: float,
    buy_fee: float = 0.001,
    sell_fee: float = 0.001,
    net_profit_rate: float = 0.001,
) -> float:
    buy_price = float(buy_price)
    buy_fee = float(buy_fee)
    sell_fee = float(sell_fee)
    net_profit_rate = float(net_profit_rate)

    if buy_price <= 0:
        raise ValueError("buy_price must be greater than zero")
    if not 0.0 <= buy_fee < 1.0:
        raise ValueError("buy_fee must be in the range [0, 1)")
    if not 0.0 <= sell_fee < 1.0:
        raise ValueError("sell_fee must be in the range [0, 1)")
    if net_profit_rate < 0.0:
        raise ValueError("net_profit_rate must not be negative")

    return buy_price * (1.0 + net_profit_rate) / (
        (1.0 - buy_fee) * (1.0 - sell_fee)
    )


@dataclass
class TradeSignal:
    action: str
    reason: str
    price: float
    target_slot_id: Optional[str] = None
    suggested_sl: Optional[float] = None
    suggested_tp: Optional[float] = None
    rsi_value: float = 0.0
    percent_b: float = 0.0


class SpotStrategy:
    """
    Fast / low-conservatism MX/USDT Spot strategy.
    """

    def __init__(
        self,
        rsi_period: int = 14,
        stop_loss_pct: float = 0.004,
        trailing_stop_activation_pct: float = 0.0015,
        trailing_stop_offset_pct: float = 0.0005,
        min_slot_price_diff_pct: float = 0.0003,
        rsi_oversold: float = 38.0,
        rsi_overbought: float = 75.0,
        bollinger_b_entry: float = 0.60,
        buy_fee: float = 0.001,
        sell_fee: float = 0.001,
        net_profit_rate: float = 0.001,
        ema_period: int = 9,       # مضاف للتحكم الخارجي
        bb_period: int = 20        # مضاف للتحكم الخارجي
    ) -> None:

        self.rsi_period = max(2, int(rsi_period))
        self.stop_loss_pct = max(0.0, float(stop_loss_pct))
        self.trailing_stop_activation_pct = max(0.0, float(trailing_stop_activation_pct))
        self.trailing_stop_offset_pct = max(0.0, float(trailing_stop_offset_pct))
        self.min_slot_price_diff_pct = max(0.0, float(min_slot_price_diff_pct))
        self.rsi_oversold = float(rsi_oversold)
        self.rsi_overbought = float(rsi_overbought)
        self.bollinger_b_entry = float(bollinger_b_entry)
        
        self.buy_fee = float(buy_fee)
        self.sell_fee = float(sell_fee)
        self.net_profit_rate = float(net_profit_rate)
        
        self.ema_period = max(2, int(ema_period))
        self.bb_period = max(2, int(bb_period))

    def calculate_take_profit_price(self, buy_price: float) -> float:
        return calculate_take_profit_price(
            buy_price,
            buy_fee=self.buy_fee,
            sell_fee=self.sell_fee,
            net_profit_rate=self.net_profit_rate,
        )

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            value = float(value)
            if not np.isfinite(value):
                return default
            return value
        except (TypeError, ValueError):
            return default

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()

        result = df.copy()
        required_columns = ["open", "high", "low", "close", "volume"]

        for column in required_columns:
            if column not in result.columns:
                raise ValueError(f"Missing required OHLCV column: {column}")
            result[column] = pd.to_numeric(result[column], errors="coerce")

        result = result.dropna(subset=["open", "high", "low", "close"])
        if result.empty:
            return result

        close = result["close"]

        # RSI
        delta = close.diff()
        gain = delta.clip(lower=0.0)
        loss = -delta.clip(upper=0.0)

        avg_gain = gain.ewm(alpha=1.0 / self.rsi_period, adjust=False, min_periods=self.rsi_period).mean()
        avg_loss = loss.ewm(alpha=1.0 / self.rsi_period, adjust=False, min_periods=self.rsi_period).mean()

        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        rsi = rsi.where(avg_loss != 0, 100.0)
        
        both_zero = (avg_gain == 0) & (avg_loss == 0)
        rsi = rsi.where(~both_zero, 50.0)
        result["rsi"] = rsi.clip(0.0, 100.0)

        # EMA
        result["ema"] = close.ewm(span=self.ema_period, adjust=False, min_periods=self.ema_period).mean()
        result["ema_slope"] = result["ema"].pct_change()

        # BOLLINGER BANDS
        bb_middle = close.rolling(self.bb_period, min_periods=self.bb_period).mean()
        bb_std = close.rolling(self.bb_period, min_periods=self.bb_period).std(ddof=0)

        bb_upper = bb_middle + (2.0 * bb_std)
        bb_lower = bb_middle - (2.0 * bb_std)

        result["bb_middle"] = bb_middle
        result["bb_upper"] = bb_upper
        result["bb_lower"] = bb_lower

        band_width = bb_upper - bb_lower
        bb_percent_b = (close - bb_lower) / band_width.replace(0, np.nan)

        result["bb_percent_b"] = bb_percent_b
        result["percent_b"] = bb_percent_b

        # ATR
        previous_close = close.shift(1)
        tr1 = result["high"] - result["low"]
        tr2 = (result["high"] - previous_close).abs()
        tr3 = (result["low"] - previous_close).abs()

        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        result["atr"] = true_range.rolling(14, min_periods=5).mean()

        return result

    def _ensure_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        required = {"rsi", "bb_percent_b", "ema", "ema_slope", "atr"}
        if not required.issubset(df.columns):
            return self.calculate_indicators(df)

        result = df.copy()
        for column in required:
            result[column] = pd.to_numeric(result[column], errors="coerce")

        if "percent_b" not in result.columns:
            result["percent_b"] = result["bb_percent_b"]

        return result

    def evaluate_entry_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        positions: Dict[str, Any],
        available_slot_id: Optional[str],
    ) -> TradeSignal:

        if df is None or df.empty:
            return TradeSignal(action="HOLD", reason="NO_DATA", price=0.0)

        df = self._ensure_indicators(df)
        minimum_candles = max(25, self.rsi_period + 5, self.bb_period, self.ema_period)

        if len(df) < minimum_candles:
            price = 0.0
            if "close" in df.columns:
                price = self._safe_float(df["close"].iloc[-1])
            return TradeSignal(action="HOLD", reason="NOT_ENOUGH_DATA", price=price)

        current = df.iloc[-1]
        prev = df.iloc[-2]
        prev2 = df.iloc[-3]
        prev3 = df.iloc[-4]

        current_price = self._safe_float(current["close"])
        if current_price <= 0:
            return TradeSignal(action="HOLD", reason="INVALID_PRICE", price=current_price)

        # التحقق من أن المؤشرات ليست NaN لتجنب الإشارات الزائفة
        if pd.isna(current["ema"]) or pd.isna(current["rsi"]) or pd.isna(current["bb_percent_b"]):
            return TradeSignal(action="HOLD", reason="INDICATORS_NOT_READY", price=current_price)

        # INDICATORS
        rsi_current = self._safe_float(current["rsi"], 50.0)
        rsi_prev = self._safe_float(prev["rsi"], rsi_current)
        rsi_prev2 = self._safe_float(prev2["rsi"], rsi_prev)

        percent_b = self._safe_float(current["bb_percent_b"], 0.5)
        percent_b_prev = self._safe_float(prev["bb_percent_b"], percent_b)

        ema_current = self._safe_float(current["ema"], 0.0) # تعديل لمنع الالتباس مع السعر الحالي
        ema_prev = self._safe_float(prev["ema"], ema_current)
        ema_slope = self._safe_float(current["ema_slope"], 0.0)

        # PRICES
        current_open = self._safe_float(current["open"], current_price)
        current_high = self._safe_float(current["high"], current_price)
        current_low = self._safe_float(current["low"], current_price)
        prev_close = self._safe_float(prev["close"], current_price)
        prev2_close = self._safe_float(prev2["close"], prev_close)
        prev3_close = self._safe_float(prev3["close"], prev2_close)
        prev_low = self._safe_float(prev["low"], prev_close)
        prev2_low = self._safe_float(prev2["low"], prev2_close)
        prev3_low = self._safe_float(prev3["low"], prev3_close)
        prev_high = self._safe_float(prev["high"], prev_close)
        prev2_high = self._safe_float(prev2["high"], prev2_close)

        # 1. RECENT DIP
        decline_1 = prev_close < prev2_close
        decline_2 = prev2_close < prev3_close
        low_decline_1 = prev_low <= prev2_low
        low_decline_2 = prev2_low <= prev3_low

        recent_reference = max(prev3_close, prev2_close, prev2_high, prev_high)
        dip_pct = 0.0
        if recent_reference > 0:
            dip_pct = ((recent_reference - prev_close) / recent_reference) * 100.0

        small_dip = dip_pct >= 0.005
        dip_detected = decline_1 or decline_2 or low_decline_1 or low_decline_2 or small_dip

        # 2. FIRST RECOVERY
        price_rebound = current_price >= prev_close
        current_green = current_price >= current_open
        candle_recovery = prev_close >= prev2_close or prev_high >= prev2_high
        break_previous_high = current_price >= prev_high
        rebound = price_rebound or current_green or candle_recovery or break_previous_high

        rebound_pct = 0.0
        if prev_close > 0:
            rebound_pct = ((current_price - prev_close) / prev_close) * 100.0
        small_rebound_confirmed = rebound_pct >= -0.03

        # 3. RSI
        rsi_turning_up = rsi_current >= rsi_prev - 0.50
        rsi_stabilizing = rsi_current >= rsi_prev2 - 1.00
        rsi_recovery = rsi_turning_up or rsi_stabilizing
