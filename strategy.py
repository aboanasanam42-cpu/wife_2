"""
Strategy module for Independent Multi-Slot Execution with Anti-Clustering Decoupling.
Combines Bollinger Bands (%B), Fast RSI, and ATR for precision dip entries and peak exhaustion detection.
Evaluates entry conditions while verifying price distance from existing active slots.
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any, List
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger("mexc_trader.strategy")


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
    target_slot_id: Optional[str] = None


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
        min_slot_price_diff_pct: float = 1.0,
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
        self.min_slot_price_diff_pct = min_slot_price_diff_pct

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Computes Bollinger Bands, %B, RSI, EMA, and ATR on closed OHLCV candles."""
        df = df.copy()

        # 1. Bollinger Bands & %B
        sma = df["close"].rolling(window=self.bollinger_period).mean()
        std = df["close"].rolling(window=self.bollinger_period).std()
        df["bb_upper"] = sma + (std * self.bollinger_std)
        df["bb_lower"] = sma - (std * self.bollinger_std)
        df["bb_middle"] = sma

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

    def evaluate_slot_exit(self, slot_id: str, slot: Dict[str, Any], current_price: float, rsi: float, pct_b: float) -> Optional[SignalResult]:
        """
        Evaluates an individual active slot for independent exit triggers:
        1. Hard Stop-Loss (-2.0% from entry)
        2. Dynamic Trailing Take-Profit Floor
        3. Fixed Take-Profit target
        4. Overbought Peak Exhaustion
        """
        if not slot.get("active"):
            return None

        entry_price = float(slot["entry_price"])
        highest_price = float(slot.get("highest_price", entry_price))
        sl_price = float(slot.get("stop_loss", entry_price * (1.0 - self.stop_loss_pct)))
        tp_price = float(slot.get("take_profit", entry_price * (1.0 + self.take_profit_pct)))

        # 1. Hard Stop-Loss or Trailing Stop Hit
        if current_price <= sl_price:
            pnl_pct = ((current_price - entry_price) / entry_price) * 100.0
            is_trailing = highest_price > (entry_price * 1.01)
            label = "Trailing Stop Triggered" if is_trailing else "Hard Stop-Loss Triggered"
            reason = f"{label} on {slot_id} at ${current_price:,.2f} (Floor: ${sl_price:,.2f}, PnL: {pnl_pct:+.2f}%)"
            return SignalResult(
                action="SELL",
                price=current_price,
                rsi_value=rsi,
                percent_b=pct_b,
                atr_value=0.0,
                reason=reason,
                target_slot_id=slot_id,
            )

        # 2. Hard Take-Profit Hit
        if current_price >= tp_price:
            pnl_pct = ((current_price - entry_price) / entry_price) * 100.0
            reason = f"Take-Profit Target Reached on {slot_id} at ${current_price:,.2f} (TP: ${tp_price:,.2f}, PnL: {pnl_pct:+.2f}%)"
            return SignalResult(
                action="SELL",
                price=current_price,
                rsi_value=rsi,
                percent_b=pct_b,
                atr_value=0.0,
                reason=reason,
                target_slot_id=slot_id,
            )

        # 3. Overbought Peak Exhaustion Exit (if slot has captured net profit >= 1.0%)
        pnl_pct = ((current_price - entry_price) / entry_price) * 100.0
        if pnl_pct >= 1.0 and pct_b > 0.95 and rsi >= self.rsi_overbought:
            reason = f"Overbought Peak Exit on {slot_id} at ${current_price:,.2f} (%B: {pct_b:.2f}, RSI: {rsi:.1f}, PnL: {pnl_pct:+.2f}%)"
            return SignalResult(
                action="SELL",
                price=current_price,
                rsi_value=rsi,
                percent_b=pct_b,
                atr_value=0.0,
                reason=reason,
                target_slot_id=slot_id,
            )

        return None

    def evaluate_entry_decoupling(self, current_price: float, active_slots: List[Dict[str, Any]]) -> tuple[bool, str]:
        """
        Anti-Clustering Check:
        Prevents opening a new slot too close to existing active slots.
        Requires current_price to be separated by at least min_slot_price_diff_pct (e.g. >= 1.0% drop)
        from any other active slot's entry price.
        """
        if not active_slots:
            return True, "No active slots. Entry clear."

        for slot in active_slots:
            entry_p = float(slot.get("entry_price", 0.0))
            if entry_p <= 0:
                continue
            diff_pct = abs(current_price - entry_p) / entry_p * 100.0
            if diff_pct < self.min_slot_price_diff_pct:
                return False, (
                    f"Anti-Clustering block: Current price ${current_price:,.2f} is only {diff_pct:.2f}% "
                    f"from {slot.get('slot_id')} entry (${entry_p:,.2f}). Requires >={self.min_slot_price_diff_pct:.1f}% spacing."
                )

        return True, "Anti-clustering decoupling verified. Entry price spaced adequately."

    def evaluate_entry_signal(
        self,
        df: pd.DataFrame,
        active_slots: List[Dict[str, Any]],
        available_slot_id: Optional[str],
    ) -> SignalResult:
        """
        Evaluates whether technical conditions (Bollinger Dip + Oversold RSI) warrant opening
        a new isolated trade slot, adhering strictly to anti-clustering distance rules.
        """
        if available_slot_id is None:
            return SignalResult(
                action="HOLD",
                price=float(df.iloc[-1]["close"]),
                rsi_value=float(df.iloc[-1]["rsi"]),
                percent_b=float(df.iloc[-1]["bb_percent_b"]),
                atr_value=float(df.iloc[-1]["atr"]),
                reason="All permissible slots currently occupied. Waiting for an exit.",
            )

        last = df.iloc[-1]
        prev = df.iloc[-2]

        current_price = float(last["close"])
        rsi = float(last["rsi"])
        pct_b = float(last["bb_percent_b"])
        atr = float(last["atr"])
        prev_pct_b = float(prev["bb_percent_b"])

        # Dip Detection: %B < 0.10 (lower band touch) OR bouncing off extreme lows (<0.05)
        is_dip = (pct_b < 0.10) or (prev_pct_b <= 0.05 and pct_b > prev_pct_b)
        is_rsi_oversold = rsi <= (self.rsi_oversold + 5.0)  # <= 35.0 threshold for fast reactive entries

        if is_dip and is_rsi_oversold:
            # Check Anti-Clustering decoupling against all active slots
            can_enter, decouple_reason = self.evaluate_entry_decoupling(current_price, active_slots)
            if not can_enter:
                return SignalResult(
                    action="HOLD",
                    price=current_price,
                    rsi_value=rsi,
                    percent_b=pct_b,
                    atr_value=atr,
                    reason=decouple_reason,
                )

            # Calculate isolated initial SL and TP for this new slot
            dynamic_sl = current_price - max(1.5 * atr, current_price * self.stop_loss_pct)
            dynamic_tp = current_price + max(2.5 * atr, current_price * self.take_profit_pct)

            reason = (
                f"Multi-Slot Entry triggered for {available_slot_id}: %B={pct_b:.2f}, RSI={rsi:.1f}, ATR={atr:.2f}. "
                f"Spacing confirmed against {len(active_slots)} active slots."
            )
            return SignalResult(
                action="BUY",
                price=current_price,
                rsi_value=rsi,
                percent_b=pct_b,
                atr_value=atr,
                suggested_sl=dynamic_sl,
                suggested_tp=dynamic_tp,
                reason=reason,
                target_slot_id=available_slot_id,
            )

        return SignalResult(
            action="HOLD",
            price=current_price,
            rsi_value=rsi,
            percent_b=pct_b,
            atr_value=atr,
            reason=f"Scanning market for dip entry. %B: {pct_b:.2f}, RSI: {rsi:.1f}, ATR: {atr:.2f}",
        )
