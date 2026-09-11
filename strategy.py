from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd


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
    target_slot_id: Optional[str] = None


class SpotStrategy:
    def __init__(self, rsi_period=14, rsi_oversold=38.0, bollinger_period=20, bollinger_std=2.0, atr_period=14, stop_loss_pct=0.02, min_slot_price_diff_pct=0.006):
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.bollinger_period = bollinger_period
        self.bollinger_std = bollinger_std
        self.atr_period = atr_period
        self.stop_loss_pct = stop_loss_pct
        self.min_slot_price_diff_pct = min_slot_price_diff_pct

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        close = out["close"]
        mid = close.rolling(self.bollinger_period).mean()
        std = close.rolling(self.bollinger_period).std(ddof=0)
        upper = mid + self.bollinger_std * std
        lower = mid - self.bollinger_std * std
        out["bb_upper"] = upper
        out["bb_lower"] = lower
        width = upper - lower
        out["bb_percent_b"] = ((close - lower) / width.replace(0, np.nan)).fillna(0.5)
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1/self.rsi_period, adjust=False, min_periods=self.rsi_period).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1/self.rsi_period, adjust=False, min_periods=self.rsi_period).mean()
        rs = gain / loss.replace(0, np.nan)
        out["rsi"] = (100 - 100 / (1 + rs)).fillna(50.0)
        prev_close = close.shift(1)
        tr = pd.concat([(out["high"]-out["low"]), (out["high"]-prev_close).abs(), (out["low"]-prev_close).abs()], axis=1).max(axis=1)
        out["atr"] = tr.rolling(self.atr_period).mean().bfill()
        return out.dropna(subset=["bb_percent_b", "rsi", "atr"]).reset_index(drop=True)

    def evaluate_entry(self, symbol: str, df: pd.DataFrame, active_slots: List[Dict[str, Any]], slot_id: Optional[str]) -> SignalResult:
        last = df.iloc[-1]
        price, rsi, pct_b, atr = map(float, [last["close"], last["rsi"], last["bb_percent_b"], last["atr"]])
        if slot_id is None:
            return SignalResult("HOLD", price, rsi, pct_b, atr, "No idle slot is currently available.", symbol)
        if not (rsi <= self.rsi_oversold and pct_b <= 0.15):
            return SignalResult("HOLD", price, rsi, pct_b, atr, f"Waiting: RSI={rsi:.2f}, %B={pct_b:.3f}", symbol)
        for slot in active_slots:
            if slot.get("symbol") == symbol and slot.get("active"):
                entry = float(slot.get("entry_price", 0))
                if entry and abs(price-entry)/entry < self.min_slot_price_diff_pct:
                    return SignalResult("HOLD", price, rsi, pct_b, atr, f"Entry spacing blocked by {slot.get('slot_id')}.", symbol)
        return SignalResult("BUY", price, rsi, pct_b, atr, f"Dip trigger: RSI={rsi:.2f} <= {self.rsi_oversold:.2f}, %B={pct_b:.3f} <= 0.15", symbol, price*(1-self.stop_loss_pct), slot_id)

    def evaluate_exit(self, slot: Dict[str, Any], current_price: float, rsi: float, pct_b: float) -> Optional[SignalResult]:
        if not slot.get("active"):
            return None
        entry = float(slot["entry_price"])
        stop = float(slot.get("stop_loss") or entry*(1-self.stop_loss_pct))
        if current_price <= stop:
            return SignalResult("SELL", current_price, rsi, pct_b, 0.0, f"Stop/trailing floor hit at {current_price:.8f}; floor={stop:.8f}", slot.get("symbol"), target_slot_id=slot.get("slot_id"))
        return None
