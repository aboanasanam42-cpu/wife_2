"""
MEXC Spot Strategy Engine.
Confirmed trend + pullback/reversal logic for short-term spot trading.
Signals are generated only from closed candles.
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger("mexc_trader.strategy")


def format_token_price(price: float) -> str:
    if price >= 1.0:
        return f"${price:,.4f}"
    if price >= 0.001:
        return f"${price:.6f}"
    return f"${price:.10f}".rstrip("0").rstrip(".")


@dataclass
class SignalResult:
    action: str
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
    REQUIRED_INDICATOR_COLUMNS = (
        "rsi",
        "bb_percent_b",
        "atr",
        "ema",
        "ema_slope",
        "bb_upper",
        "bb_lower",
    )

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
        required_price_columns = {"high", "low", "close"}
        missing_price_columns = required_price_columns.difference(df.columns)
        if missing_price_columns:
            raise RuntimeError(
                "Cannot calculate indicators; missing price columns: "
                + ", ".join(sorted(missing_price_columns))
            )

        df = df.copy()
        for column in ("high", "low", "close"):
            df[column] = pd.to_numeric(df[column], errors="coerce")

        df = df.dropna(subset=["high", "low", "close"])
        if df.empty:
            return df

        close = df["close"]
        sma = close.rolling(window=self.bollinger_period, min_periods=1).mean()
        std = close.rolling(window=self.bollinger_period, min_periods=1).std(ddof=0).fillna(0.0)
        df["bb_upper"] = sma + (std * self.bollinger_std)
        df["bb_lower"] = sma - (std * self.bollinger_std)
        df["bb_middle"] = sma
        band_diff = df["bb_upper"] - df["bb_lower"]
        df["bb_percent_b"] = np.where(
            band_diff > 0,
            (close - df["bb_lower"]) / band_diff,
            0.5,
        )

        delta = close.diff().fillna(0.0)
        gain = delta.clip(lower=0).rolling(window=self.rsi_period, min_periods=1).mean()
        loss = (-delta.clip(upper=0)).rolling(window=self.rsi_period, min_periods=1).mean()
        rs = gain.div(loss.replace(0, np.nan))
        rsi = 100.0 - (100.0 / (1.0 + rs))
        df["rsi"] = rsi.where(loss > 0, 100.0).where(gain > 0, 50.0).fillna(50.0)

        df["ema"] = close.ewm(span=self.ema_period, adjust=False).mean()
        df["ema_slope"] = df["ema"].diff().fillna(0.0)

        high_low = df["high"] - df["low"]
        high_close = (df["high"] - close.shift()).abs()
        low_close = (df["low"] - close.shift()).abs()
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df["atr"] = true_range.rolling(window=self.atr_period, min_periods=1).mean().fillna(0.0)

        for column in self.REQUIRED_INDICATOR_COLUMNS:
            df[column] = pd.to_numeric(df[column], errors="coerce").replace([np.inf, -np.inf], np.nan)
            df[column] = df[column].ffill().bfill().fillna(0.0)

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
        if not slot.get("active"):
            return None
        slot_symbol = slot.get("symbol") or symbol or "UNKNOWN"
        if symbol and slot.get("symbol") and slot.get("symbol") != symbol:
            return None

        entry_price = float(slot["entry_price"])
        highest_price = float(slot.get("highest_price", entry_price))
        sl_price = float(slot.get("stop_loss", entry_price * (1.0 - self.stop_loss_pct)))
        tp_price = float(slot.get("take_profit", entry_price * (1.0 + self.take_profit_pct)))
        pnl_pct = ((current_price - entry_price) / entry_price) * 100.0 if entry_price > 0 else 0.0

        if current_price <= sl_price:
            trailing = highest_price > entry_price * (1.0 + self.trailing_stop_activation_pct)
            label = "Trailing Stop Floor Hit" if trailing else f"Hard Stop-Loss (-{self.stop_loss_pct * 100.0:.1f}%) Hit"
            return SignalResult("SELL", current_price, rsi, pct_b, 0.0,
                                f"{label} on {slot_id} ({slot_symbol}) at {format_token_price(current_price)}; PnL {pnl_pct:+.2f}%",
                                slot_symbol, target_slot_id=slot_id)

        if current_price >= tp_price:
            return SignalResult("SELL", current_price, rsi, pct_b, 0.0,
                                f"Take-Profit Target Hit on {slot_id} ({slot_symbol}) at {format_token_price(current_price)}; PnL {pnl_pct:+.2f}%",
                                slot_symbol, target_slot_id=slot_id)

        # Do not sell on a tiny one-candle RSI wobble. Require meaningful profit and
        # a confirmed momentum turn or overbought exhaustion.
        rsi_prev_val = prev_rsi
        rsi_prev2_val = None
        if df is not None and "rsi" in df.columns and len(df) >= 3:
            rsi_prev_val = float(df["rsi"].iloc[-2])
            rsi_prev2_val = float(df["rsi"].iloc[-3])

        if pnl_pct >= 0.40 and rsi_prev_val is not None:
            confirmed_hook = (
                rsi < rsi_prev_val
                and rsi_prev_val >= 60.0
                and (rsi_prev2_val is None or rsi_prev_val >= rsi_prev2_val)
            )
            if confirmed_hook:
                return SignalResult("SELL", current_price, rsi, pct_b, 0.0,
                                    f"Confirmed RSI momentum exit on {slot_id} ({slot_symbol}); RSI {rsi_prev_val:.1f}->{rsi:.1f}; PnL {pnl_pct:+.2f}%",
                                    slot_symbol, target_slot_id=slot_id)

        if pnl_pct >= 0.40 and pct_b >= 0.95 and rsi >= self.rsi_overbought:
            return SignalResult("SELL", current_price, rsi, pct_b, 0.0,
                                f"Overbought exhaustion exit on {slot_id} ({slot_symbol}); %B {pct_b:.2f}, RSI {rsi:.1f}, PnL {pnl_pct:+.2f}%",
                                slot_symbol, target_slot_id=slot_id)
        return None

    def evaluate_entry_decoupling(
        self,
        current_price: float,
        active_slots: List[Dict[str, Any]],
        symbol: Optional[str] = None,
    ) -> Tuple[bool, str]:
        if not active_slots:
            return True, "No active slots. Entry clear."
        same_symbol_slots = [
            s for s in active_slots
            if s.get("active") and (not symbol or s.get("symbol") == symbol)
        ]
        if not same_symbol_slots:
            return True, f"No active slots holding {symbol or 'this asset'}. Entry clear."

        threshold_pct = self.min_slot_price_diff_pct * 100.0 if self.min_slot_price_diff_pct < 0.05 else self.min_slot_price_diff_pct
        for slot in same_symbol_slots:
            entry_p = float(slot.get("entry_price", 0.0))
            if entry_p <= 0:
                continue
            diff_pct = abs(current_price - entry_p) / entry_p * 100.0
            if diff_pct < threshold_pct:
                return False, f"Anti-clustering block: {diff_pct:.2f}% from {slot.get('slot_id')} entry; requires >= {threshold_pct:.2f}%."
        return True, f"Anti-clustering verified for {symbol}."

    def evaluate_entry_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        active_slots: List[Dict[str, Any]],
        available_slot_id: Optional[str],
    ) -> SignalResult:
        """
        Trading plan translated to executable rules:
        1) Trade only finalized candles.
        2) Trade WITH the short-term trend: price above EMA20 and EMA20 rising.
        3) Wait for a pullback into the lower Bollinger area.
        4) Require an RSI trough-hook reversal, so the bot does not buy a falling knife.
        5) Require the current candle to recover above the previous close.
        6) Apply anti-clustering before opening another slot on the same symbol.
        7) Size each slot from configuration; never increase order size because of a signal.
        """
        if df.empty:
            return SignalResult("HOLD", 0.0, 50.0, 0.5, 0.0, "No market data.", symbol=symbol)

        indicators_ready = all(
            column in df.columns
            and pd.to_numeric(df[column], errors="coerce").replace([np.inf, -np.inf], np.nan).notna().all()
            for column in self.REQUIRED_INDICATOR_COLUMNS
        )
        missing_columns = [
            column
            for column in self.REQUIRED_INDICATOR_COLUMNS
            if column not in df.columns
        ]
        if missing_columns or not indicators_ready:
            df = self.calculate_indicators(df)
            missing_columns = [
                column
                for column in self.REQUIRED_INDICATOR_COLUMNS
                if column not in df.columns
            ]
            if missing_columns:
                raise RuntimeError(
                    "Strategy indicators are missing after calculation: "
                    + ", ".join(missing_columns)
                )

        if df.empty:
            return SignalResult("HOLD", 0.0, 50.0, 0.5, 0.0, "No valid market data after indicator calculation.", symbol=symbol)

        last = df.iloc[-1]
        current_price = float(last["close"])
        rsi_current = float(last["rsi"])
        pct_b = float(last["bb_percent_b"])
        atr = float(last["atr"])

        if available_slot_id is None:
            return SignalResult("HOLD", current_price, rsi_current, pct_b, atr, "All slots occupied; waiting for an exit.", symbol=symbol)
        if len(df) < max(4, self.bollinger_period, self.rsi_period):
            return SignalResult("HOLD", current_price, rsi_current, pct_b, atr, f"Insufficient closed candles ({len(df)}).", symbol=symbol)

        prev = df.iloc[-2]
        prev2 = df.iloc[-3]
        rsi_prev = float(prev["rsi"])
        rsi_prev2 = float(prev2["rsi"])
        prev_pct_b = float(prev["bb_percent_b"])
        ema = float(last["ema"])
        prev_ema = float(prev["ema"])
        prev_close = float(prev["close"])
        bb_lower = float(last["bb_lower"])

        trend_up = current_price > ema and ema >= prev_ema
        rsi_trough_hook = rsi_prev < rsi_prev2 and rsi_current > rsi_prev
        lower_zone = pct_b <= max(self.bollinger_b_entry, 0.20)
        bounce = (prev_pct_b <= max(self.bollinger_b_entry, 0.20) and pct_b > prev_pct_b) or (
            float(last.get("low", current_price)) <= bb_lower and current_price > prev_close
        )
        price_recovery = current_price > prev_close

        if trend_up and rsi_trough_hook and (lower_zone or bounce) and price_recovery:
            can_enter, decouple_reason = self.evaluate_entry_decoupling(current_price, active_slots, symbol)
            if not can_enter:
                return SignalResult("HOLD", current_price, rsi_current, pct_b, atr, decouple_reason, symbol=symbol)

            # Volatility-aware protective levels, with configured hard limits as floors.
            dynamic_sl = current_price - max(1.5 * atr, current_price * self.stop_loss_pct)
            dynamic_tp = current_price + max(2.0 * atr, current_price * self.take_profit_pct)
            reason = (
                f"BUY confirmation {symbol}: trend UP (price {current_price:.6f} > EMA20 {ema:.6f}), "
                f"RSI hook {rsi_prev2:.1f}->{rsi_prev:.1f}->{rsi_current:.1f}, "
                f"%B={pct_b:.2f}, recovery={price_recovery}, {decouple_reason}"
            )
            return SignalResult("BUY", current_price, rsi_current, pct_b, atr, reason,
                                symbol=symbol, suggested_sl=dynamic_sl, suggested_tp=dynamic_tp,
                                target_slot_id=available_slot_id)

        return SignalResult(
            "HOLD", current_price, rsi_current, pct_b, atr,
            f"No confirmed setup: trend_up={trend_up}, rsi_hook={rsi_trough_hook}, pullback={lower_zone or bounce}, recovery={price_recovery}",
            symbol=symbol,
        )
