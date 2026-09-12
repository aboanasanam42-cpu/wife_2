"""
Scalping Strategy Engine for MEXC Spot Independent Multi-Slot Multi-Pair Execution.
Computes vectorized Bollinger Bands (%B), Fast RSI (14), EMA, and ATR (14).
Evaluates precision dip triggers and individual slot trailing/hard exits across multiple assets.
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger("mexc_trader.strategy")


def format_token_price(price: float) -> str:
    """Formats price string nicely, supporting sub-cent tokens like PEPE/SHIB without scientific notation."""
    if price >= 1.0:
        return f"${price:,.4f}"
    elif price >= 0.001:
        return f"${price:.6f}"
    else:
        return f"${price:.10f}".rstrip("0").rstrip(".")


@dataclass
class SignalResult:
    action: str  # "BUY", "SELL", "HOLD"
    price: float
    rsi_value: float
    percent_b: float
    atr_value: float
    reason: str
    symbol: Optional[str] = None
    suggested_sl: Optional[float] = None
    suggested_tp: Optional[float] = None
    target_slot_id: Optional[str] = None


class SpotStrategy:
    def __init__(
        self,
        rsi_period: int = 14,
        rsi_oversold: float = 38.0,
        rsi_overbought: float = 68.0,
        ema_period: int = 20,
        bollinger_period: int = 20,
        bollinger_std: float = 2.0,
        bollinger_b_entry: float = 0.20,
        atr_period: int = 14,
        stop_loss_pct: float = 0.02,
        take_profit_pct: float = 0.03,
        trailing_stop_activation_pct: float = 0.005,
        trailing_stop_offset_pct: float = 0.002,
        min_slot_price_diff_pct: float = 0.006,
    ):
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.ema_period = ema_period
        self.bollinger_period = bollinger_period
        self.bollinger_std = bollinger_std
        self.bollinger_b_entry = bollinger_b_entry
        self.atr_period = atr_period
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.trailing_stop_activation_pct = trailing_stop_activation_pct
        self.trailing_stop_offset_pct = trailing_stop_offset_pct
        self.min_slot_price_diff_pct = min_slot_price_diff_pct

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Computes vectorized Bollinger Bands, %B, Fast RSI, EMA, and ATR on closed OHLCV candles.
        """
        df = df.copy()

        # 1. Bollinger Bands & %B
        sma = df["close"].rolling(window=self.bollinger_period).mean()
        std = df["close"].rolling(window=self.bollinger_period).std()
        df["bb_upper"] = sma + (std * self.bollinger_std)
        df["bb_lower"] = sma - (std * self.bollinger_std)
        df["bb_middle"] = sma

        band_diff = df["bb_upper"] - df["bb_lower"]
        df["bb_percent_b"] = np.where(band_diff > 0, (df["close"] - df["bb_lower"]) / band_diff, 0.5)

        # 2. Fast RSI (14)
        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0.0)).rolling(window=self.rsi_period).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(window=self.rsi_period).mean()
        rs = gain / loss.replace(0, np.nan)
        df["rsi"] = 100.0 - (100.0 / (1.0 + rs))
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

    def evaluate_slot_exit(
        self,
        slot_id: str,
        slot: Dict[str, Any],
        current_price: float,
        rsi: float,
        pct_b: float,
        symbol: Optional[str] = None,
        prev_rsi: Optional[float] = None,
        df: Optional[pd.DataFrame] = None,
    ) -> Optional[SignalResult]:
        """
        Evaluates an individual active slot for immediate exit triggers:
        1. Trailing stop floor breached or Hard Stop-Loss (-2.0%) breached
        2. Base Take-Profit reached (+3.0%)
        3. Secondary Momentum Protection: RSI reaches a peak and decisively hooks downward (rsi[-1] < rsi[-2]) while in profit
        4. Overbought exhaustion: %B >= 0.95 and RSI >= RSI_OVERBOUGHT while in profit
        """
        if not slot.get("active"):
            return None

        slot_symbol = slot.get("symbol") or symbol or "UNKNOWN"
        # If symbol parameter is supplied and does not match the slot's traded symbol, skip
        if symbol and slot.get("symbol") and slot.get("symbol") != symbol:
            return None

        entry_price = float(slot["entry_price"])
        highest_price = float(slot.get("highest_price", entry_price))
        sl_price = float(slot.get("stop_loss", entry_price * (1.0 - self.stop_loss_pct)))
        tp_price = float(slot.get("take_profit", entry_price * (1.0 + self.take_profit_pct)))

        pnl_pct = ((current_price - entry_price) / entry_price) * 100.0 if entry_price > 0 else 0.0

        # 1. Dynamic Trailing Stop Floor OR Hard Stop-Loss Hit
        if current_price <= sl_price:
            is_trailing = highest_price > (entry_price * (1.0 + self.trailing_stop_activation_pct))
            label = "Trailing Stop Floor Hit" if is_trailing else f"Hard Stop-Loss (-{self.stop_loss_pct * 100.0:.1f}%) Hit"
            reason = f"{label} on {slot_id} ({slot_symbol}) at {format_token_price(current_price)} (Floor: {format_token_price(sl_price)}, PnL: {pnl_pct:+.2f}%)"
            return SignalResult(
                action="SELL",
                price=current_price,
                rsi_value=rsi,
                percent_b=pct_b,
                atr_value=0.0,
                reason=reason,
                symbol=slot_symbol,
                target_slot_id=slot_id,
            )

        # 2. Hard Take-Profit Target Reached
        if current_price >= tp_price:
            reason = f"Take-Profit Target Hit on {slot_id} ({slot_symbol}) at {format_token_price(current_price)} (Target: {format_token_price(tp_price)}, PnL: {pnl_pct:+.2f}%)"
            return SignalResult(
                action="SELL",
                price=current_price,
                rsi_value=rsi,
                percent_b=pct_b,
                atr_value=0.0,
                reason=reason,
                symbol=slot_symbol,
                target_slot_id=slot_id,
            )

        # 3. Secondary Momentum Protection:
        # If RSI reaches a peak and decisively hooks downward (rsi.iloc[-1] < rsi.iloc[-2]) while in profit, trigger immediate exit
        rsi_prev_val = prev_rsi
        rsi_prev2_val = None
        if df is not None and "rsi" in df.columns and len(df) >= 3:
            rsi_prev_val = float(df["rsi"].iloc[-2])
            rsi_prev2_val = float(df["rsi"].iloc[-3])

        if pnl_pct > 0.0 and rsi_prev_val is not None:
            # Check downward hook: rsi.iloc[-1] < rsi.iloc[-2]
            # Peak condition: previous candle was ascending or elevated momentum
            is_peak_hook = (rsi < rsi_prev_val) and (rsi_prev2_val is None or rsi_prev_val >= rsi_prev2_val or rsi_prev_val >= 50.0)
            if is_peak_hook:
                reason = (
                    f"RSI Peak Hookdown Momentum Exit on {slot_id} ({slot_symbol}) at {format_token_price(current_price)} "
                    f"(RSI hooked downward {rsi_prev_val:.1f} -> {rsi:.1f} while in profit: {pnl_pct:+.2f}%)"
                )
                return SignalResult(
                    action="SELL",
                    price=current_price,
                    rsi_value=rsi,
                    percent_b=pct_b,
                    atr_value=0.0,
                    reason=reason,
                    symbol=slot_symbol,
                    target_slot_id=slot_id,
                )

        # 4. Overbought Peak Exhaustion: %B >= 0.95 and RSI >= RSI_OVERBOUGHT while in profit
        if pct_b >= 0.95 and rsi >= self.rsi_overbought and pnl_pct > 0.0:
            reason = (
                f"Overbought Exhaustion Exit on {slot_id} ({slot_symbol}) at {format_token_price(current_price)} "
                f"(%B: {pct_b:.2f} >= 0.95, RSI: {rsi:.1f} >= {self.rsi_overbought:.1f}, PnL: {pnl_pct:+.2f}%)"
            )
            return SignalResult(
                action="SELL",
                price=current_price,
                rsi_value=rsi,
                percent_b=pct_b,
                atr_value=0.0,
                reason=reason,
                symbol=slot_symbol,
                target_slot_id=slot_id,
            )

        return None

    def evaluate_entry_decoupling(
        self,
        current_price: float,
        active_slots: List[Dict[str, Any]],
        symbol: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Anti-Clustering Check:
        Prevents opening a new slot for the SAME symbol at the same candle or price if
        another slot entered that asset within MIN_SLOT_PRICE_DIFF_PCT.
        Does not block entries for different symbols (e.g., SOL slot does not block DOGE slot).
        """
        if not active_slots:
            return True, "No active slots. Entry clear."

        # Filter active slots that hold this exact symbol
        same_symbol_slots = [
            s for s in active_slots
            if s.get("active") and (not symbol or s.get("symbol") == symbol)
        ]

        if not same_symbol_slots:
            return True, f"No active slots holding {symbol or 'this asset'}. Entry clear."

        # Normalize threshold to percentage (e.g., 0.006 or 0.6 both represent 0.6%)
        threshold_pct = self.min_slot_price_diff_pct * 100.0 if self.min_slot_price_diff_pct < 0.05 else self.min_slot_price_diff_pct

        for slot in same_symbol_slots:
            entry_p = float(slot.get("entry_price", 0.0))
            if entry_p <= 0:
                continue
            diff_pct = abs(current_price - entry_p) / entry_p * 100.0
            if diff_pct < threshold_pct:
                return False, (
                    f"Anti-Clustering block: Current price {format_token_price(current_price)} is {diff_pct:.2f}% "
                    f"from {slot.get('slot_id')} entry ({format_token_price(entry_p)}) for {symbol}. "
                    f"Requires >={threshold_pct:.2f}% spacing."
                )

        return True, f"Anti-clustering decoupling verified for {symbol}. Entry price separated adequately."

    def evaluate_entry_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        active_slots: List[Dict[str, Any]],
        available_slot_id: Optional[str],
    ) -> SignalResult:
        """
        Dynamic RSI Trough-Hook & Slope-Reversal Strategy:
        1. Calculate 14-period RSI as normal.
        2. Entry Trigger (Dynamic Momentum Reversal):
           Detect when RSI forms an inflection trough / bullish hook:
           rsi.iloc[-2] < rsi.iloc[-3] AND rsi.iloc[-1] > rsi.iloc[-2] (Momentum turning upward).
        3. Combine this inflection with Bollinger Band lower band proximity (%B <= 0.20 or price bouncing off lower band)
           to ensure entry occurs at local dips rather than mid-air.
        4. Completely removes reliance on rigid fixed-number limits for RSI entries.
        """
        if available_slot_id is None:
            return SignalResult(
                action="HOLD",
                price=float(df.iloc[-1]["close"]) if not df.empty else 0.0,
                rsi_value=float(df.iloc[-1]["rsi"]) if not df.empty and "rsi" in df.columns else 50.0,
                percent_b=float(df.iloc[-1]["bb_percent_b"]) if not df.empty and "bb_percent_b" in df.columns else 0.5,
                atr_value=float(df.iloc[-1]["atr"]) if not df.empty and "atr" in df.columns else 0.0,
                symbol=symbol,
                reason="All permissible slots currently occupied. Waiting for an exit.",
            )

        if len(df) < 4:
            return SignalResult(
                action="HOLD",
                price=float(df.iloc[-1]["close"]) if not df.empty else 0.0,
                rsi_value=float(df.iloc[-1]["rsi"]) if not df.empty and "rsi" in df.columns else 50.0,
                percent_b=float(df.iloc[-1]["bb_percent_b"]) if not df.empty and "bb_percent_b" in df.columns else 0.5,
                atr_value=float(df.iloc[-1]["atr"]) if not df.empty and "atr" in df.columns else 0.0,
                symbol=symbol,
                reason=f"Insufficient candles ({len(df)} < 4) to evaluate RSI trough hook.",
            )

        last = df.iloc[-1]
        prev = df.iloc[-2]
        prev2 = df.iloc[-3]

        current_price = float(last["close"])
        rsi_current = float(last["rsi"])
        pct_b = float(last["bb_percent_b"])
        atr = float(last["atr"])

        rsi_prev = float(prev["rsi"])
        rsi_prev2 = float(prev2["rsi"])
        prev_pct_b = float(prev["bb_percent_b"])
        bb_lower = float(last.get("bb_lower", 0.0))
        prev_bb_lower = float(prev.get("bb_lower", 0.0))

        # 1. Dynamic Momentum Reversal: RSI forms an inflection trough / bullish hook
        # rsi.iloc[-2] < rsi.iloc[-3] AND rsi.iloc[-1] > rsi.iloc[-2] (Momentum turning upward)
        rsi_trough_hook = (rsi_prev < rsi_prev2) and (rsi_current > rsi_prev)

        # 2. Bollinger Band lower band proximity (%B <= 0.20 or price bouncing off lower band)
        # Ensures entry occurs at local dips rather than mid-air
        entry_bb_threshold = max(self.bollinger_b_entry, 0.20)
        is_bb_dip = pct_b <= entry_bb_threshold
        is_bb_bounce = (prev_pct_b <= entry_bb_threshold and pct_b > prev_pct_b) or (
            float(last.get("low", current_price)) <= bb_lower and current_price >= bb_lower
        )
        is_lower_band_proximity = is_bb_dip or is_bb_bounce

        if rsi_trough_hook and is_lower_band_proximity:
            # Verify Anti-Clustering spacing against open slots holding this symbol
            can_enter, decouple_reason = self.evaluate_entry_decoupling(current_price, active_slots, symbol=symbol)
            if not can_enter:
                return SignalResult(
                    action="HOLD",
                    price=current_price,
                    rsi_value=rsi_current,
                    percent_b=pct_b,
                    atr_value=atr,
                    symbol=symbol,
                    reason=decouple_reason,
                )

            # Calculate isolated initial SL and TP for this slot
            dynamic_sl = current_price - max(1.5 * atr, current_price * self.stop_loss_pct)
            dynamic_tp = current_price + max(2.5 * atr, current_price * self.take_profit_pct)

            reason = (
                f"Dynamic RSI Trough-Hook & Slope-Reversal for {available_slot_id} on {symbol}: "
                f"RSI Hook [{rsi_prev2:.1f} -> {rsi_prev:.1f} -> {rsi_current:.1f}], "
                f"%B={pct_b:.2f} (lower proximity <= {entry_bb_threshold:.2f}), ATR={atr:.4f}. "
                f"Decoupled from existing slots."
            )
            return SignalResult(
                action="BUY",
                price=current_price,
                rsi_value=rsi_current,
                percent_b=pct_b,
                atr_value=atr,
                symbol=symbol,
                suggested_sl=dynamic_sl,
                suggested_tp=dynamic_tp,
                reason=reason,
                target_slot_id=available_slot_id,
            )

        return SignalResult(
            action="HOLD",
            price=current_price,
            rsi_value=rsi_current,
            percent_b=pct_b,
            atr_value=atr,
            symbol=symbol,
            reason=(
                f"Scanning {symbol} for Dynamic RSI Trough-Hook & %B dip "
                f"(RSI: {rsi_prev2:.1f} -> {rsi_prev:.1f} -> {rsi_current:.1f}, %B: {pct_b:.2f}, ATR: {atr:.4f})"
            ),
        )
