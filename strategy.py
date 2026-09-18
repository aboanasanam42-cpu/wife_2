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
    if not 0.0 <= buy_fee < 1.0 or not 0.0 <= sell_fee < 1.0:
        raise ValueError("fee rates must be in the range [0, 1)")
    if net_profit_rate < 0.0:
        raise ValueError("net_profit_rate must not be negative")
    return buy_price * (1.0 + net_profit_rate) / ((1.0 - buy_fee) * (1.0 - sell_fee))


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
    """Short-horizon MEXC spot entry/exit signal engine."""

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
        ema_period: int = 9,
        bb_period: int = 20,
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
            result = float(value)
            return result if np.isfinite(result) else default
        except (TypeError, ValueError):
            return default

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()

        result = df.copy()
        required = ["open", "high", "low", "close", "volume"]
        for column in required:
            if column not in result.columns:
                raise ValueError(f"Missing required OHLCV column: {column}")
            result[column] = pd.to_numeric(result[column], errors="coerce")
        result = result.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
        if result.empty:
            return result

        close = result["close"]
        delta = close.diff()
        gain = delta.clip(lower=0.0)
        loss = -delta.clip(upper=0.0)
        avg_gain = gain.ewm(alpha=1.0 / self.rsi_period, adjust=False, min_periods=self.rsi_period).mean()
        avg_loss = loss.ewm(alpha=1.0 / self.rsi_period, adjust=False, min_periods=self.rsi_period).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        rsi = rsi.where(avg_loss != 0, 100.0)
        rsi = rsi.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)
        result["rsi"] = rsi.clip(0.0, 100.0)

        result["ema"] = close.ewm(span=self.ema_period, adjust=False, min_periods=self.ema_period).mean()
        result["ema_slope"] = result["ema"].pct_change()

        middle = close.rolling(self.bb_period, min_periods=self.bb_period).mean()
        std = close.rolling(self.bb_period, min_periods=self.bb_period).std(ddof=0)
        upper = middle + 2.0 * std
        lower = middle - 2.0 * std
        result["bb_middle"] = middle
        result["bb_upper"] = upper
        result["bb_lower"] = lower
        result["bb_percent_b"] = (close - lower) / (upper - lower).replace(0, np.nan)
        result["percent_b"] = result["bb_percent_b"]

        previous_close = close.shift(1)
        true_range = pd.concat(
            [
                result["high"] - result["low"],
                (result["high"] - previous_close).abs(),
                (result["low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
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
        """Always returns a TradeSignal; never leaks None into the trading loop."""
        if df is None or df.empty:
            return TradeSignal("HOLD", "NO_DATA", 0.0, available_slot_id)

        df = self._ensure_indicators(df)
        if df.empty or "close" not in df.columns:
            return TradeSignal("HOLD", "INVALID_DATA", 0.0, available_slot_id)

        price = self._safe_float(df["close"].iloc[-1])
        if price <= 0:
            return TradeSignal("HOLD", "INVALID_PRICE", price, available_slot_id)

        minimum_candles = max(25, self.rsi_period + 5, self.bb_period, self.ema_period)
        if len(df) < minimum_candles:
            return TradeSignal("HOLD", "NOT_ENOUGH_DATA", price, available_slot_id)

        current = df.iloc[-1]
        prev = df.iloc[-2]
        prev2 = df.iloc[-3]
        prev3 = df.iloc[-4]

        rsi = self._safe_float(current.get("rsi"), 50.0)
        rsi_prev = self._safe_float(prev.get("rsi"), rsi)
        rsi_prev2 = self._safe_float(prev2.get("rsi"), rsi_prev)
        percent_b = self._safe_float(current.get("bb_percent_b"), 0.5)
        percent_b_prev = self._safe_float(prev.get("bb_percent_b"), percent_b)
        ema = self._safe_float(current.get("ema"), price)
        ema_prev = self._safe_float(prev.get("ema"), ema)
        ema_slope = self._safe_float(current.get("ema_slope"), 0.0)

        if not all(np.isfinite(v) for v in (rsi, percent_b, ema, ema_prev)):
            return TradeSignal("HOLD", "INDICATORS_NOT_READY", price, available_slot_id)

        prev_close = self._safe_float(prev.get("close"), price)
        prev2_close = self._safe_float(prev2.get("close"), prev_close)
        prev3_close = self._safe_float(prev3.get("close"), prev2_close)
        prev_low = self._safe_float(prev.get("low"), prev_close)
        prev2_low = self._safe_float(prev2.get("low"), prev2_close)
        prev_high = self._safe_float(prev.get("high"), prev_close)
        current_open = self._safe_float(current.get("open"), price)

        dip_detected = (
            prev_close < prev2_close
            or prev2_close < prev3_close
            or prev_low <= prev2_low
            or percent_b_prev <= self.bollinger_b_entry
        )
        rebound = price >= prev_close or price >= current_open or price >= prev_high
        rsi_recovery = (
            rsi > rsi_prev
            and (rsi_prev <= self.rsi_oversold or rsi_prev2 <= self.rsi_oversold)
        )
        rsi_not_extreme = rsi <= self.rsi_overbought
        ema_ok = price >= ema * 0.997
        trend_recovering = ema_slope >= -0.001
        slot_available = available_slot_id is not None

        if slot_available and dip_detected and rebound and rsi_recovery and rsi_not_extreme and ema_ok and trend_recovering:
            stop_loss = price * (1.0 - self.stop_loss_pct) if self.stop_loss_pct > 0 else None
            take_profit = self.calculate_take_profit_price(price)
            return TradeSignal(
                action="BUY",
                reason="RECOVERY_SETUP",
                price=price,
                target_slot_id=available_slot_id,
                suggested_sl=stop_loss,
                suggested_tp=take_profit,
                rsi_value=rsi,
                percent_b=percent_b,
            )

        reasons = []
        if not slot_available:
            reasons.append("NO_SLOT")
        if not dip_detected:
            reasons.append("NO_DIP")
        if not rebound:
            reasons.append("NO_REBOUND")
        if not rsi_recovery:
            reasons.append("RSI_NOT_RECOVERING")
        if not rsi_not_extreme:
            reasons.append("RSI_OVERBOUGHT")
        if not ema_ok:
            reasons.append("BELOW_EMA")
        if not trend_recovering:
            reasons.append("EMA_WEAK")
        return TradeSignal(
            action="HOLD",
            reason=";".join(reasons) or "NO_ENTRY",
            price=price,
            target_slot_id=available_slot_id,
            rsi_value=rsi,
            percent_b=percent_b,
        )
