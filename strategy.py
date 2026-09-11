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
    """Short-term spot scalping: buy pullbacks only inside an uptrend."""
    def __init__(self, rsi_period=14, rsi_oversold=42.0, bollinger_period=20, bollinger_std=2.0, atr_period=14, stop_loss_pct=0.012, min_slot_price_diff_pct=0.006):
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
        out["ema9"] = close.ewm(span=9, adjust=False).mean()
        out["ema21"] = close.ewm(span=21, adjust=False).mean()
        out["ema50"] = close.ewm(span=50, adjust=False).mean()
        mid = close.rolling(self.bollinger_period).mean()
        std = close.rolling(self.bollinger_period).std(ddof=0)
        upper = mid + self.bollinger_std * std
        lower = mid - self.bollinger_std * std
        out["bb_mid"], out["bb_upper"], out["bb_lower"] = mid, upper, lower
        out["bb_percent_b"] = ((close - lower) / (upper - lower).replace(0, np.nan)).fillna(0.5)
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1/self.rsi_period, adjust=False, min_periods=self.rsi_period).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1/self.rsi_period, adjust=False, min_periods=self.rsi_period).mean()
        rs = gain / loss.replace(0, np.nan)
        out["rsi"] = (100 - 100 / (1 + rs)).fillna(50.0)
        prev_close = close.shift(1)
        tr = pd.concat([(out["high"]-out["low"]), (out["high"]-prev_close).abs(), (out["low"]-prev_close).abs()], axis=1).max(axis=1)
        out["atr"] = tr.rolling(self.atr_period).mean()
        out["atr_pct"] = (out["atr"] / close).replace([np.inf, -np.inf], np.nan)
        if "volume" in out.columns:
            out["volume_ma"] = out["volume"].rolling(20).mean()
            out["volume_ratio"] = (out["volume"] / out["volume_ma"]).replace([np.inf, -np.inf], np.nan)
        else:
            out["volume_ratio"] = 1.0
        return out.dropna(subset=["ema50", "bb_percent_b", "rsi", "atr", "atr_pct"]).reset_index(drop=True)

    def evaluate_entry(self, symbol: str, df: pd.DataFrame, active_slots: List[Dict[str, Any]], slot_id: Optional[str]) -> SignalResult:
        last = df.iloc[-1]
        price, rsi, pct_b, atr = map(float, [last["close"], last["rsi"], last["bb_percent_b"], last["atr"]])
        if slot_id is None:
            return SignalResult("HOLD", price, rsi, pct_b, atr, "No idle slot is currently available.", symbol)
        ema9, ema21, ema50 = float(last["ema9"]), float(last["ema21"]), float(last["ema50"])
        volume_ratio = float(last.get("volume_ratio", 1.0) or 1.0)
        trend_ok = price > ema50 and ema9 > ema21 > ema50
        pullback_ok = rsi <= self.rsi_oversold and pct_b <= 0.45
        volatility_ok = 0.001 <= float(last["atr_pct"]) <= 0.025
        volume_ok = volume_ratio >= 0.8
        if not (trend_ok and pullback_ok and volatility_ok and volume_ok):
            return SignalResult("HOLD", price, rsi, pct_b, atr, f"Waiting: trend={trend_ok}, pullback={pullback_ok}, volatility={volatility_ok}, volume={volume_ok}; RSI={rsi:.1f}, %B={pct_b:.2f}", symbol)
        for slot in active_slots:
            if slot.get("symbol") == symbol and slot.get("active"):
                entry = float(slot.get("entry_price", 0))
                if entry and abs(price-entry)/entry < self.min_slot_price_diff_pct:
                    return SignalResult("HOLD", price, rsi, pct_b, atr, f"Entry spacing blocked by {slot.get('slot_id')}.", symbol)
        suggested_stop = max(price * (1 - self.stop_loss_pct), price - 1.6 * atr)
        return SignalResult("BUY", price, rsi, pct_b, atr, f"Uptrend pullback: EMA9>EMA21>EMA50, RSI={rsi:.1f}, %B={pct_b:.2f}, volume={volume_ratio:.2f}x", symbol, suggested_stop, slot_id)

    def evaluate_exit(self, slot: Dict[str, Any], current_price: float, rsi: float, pct_b: float, atr: float = 0.0, ema9: float = 0.0, ema21: float = 0.0) -> Optional[SignalResult]:
        if not slot.get("active"):
            return None
        entry = float(slot["entry_price"])
        stop = float(slot.get("stop_loss") or entry*(1-self.stop_loss_pct))
        if current_price <= stop:
            return SignalResult("SELL", current_price, rsi, pct_b, atr, f"Adaptive stop hit at {current_price:.8f}; floor={stop:.8f}", slot.get("symbol"), target_slot_id=slot.get("slot_id"))
        gain = (current_price - entry) / entry if entry else 0.0
        if gain >= 0.006 and (rsi >= 68 or (ema9 and ema21 and ema9 < ema21)):
            return SignalResult("SELL", current_price, rsi, pct_b, atr, f"Scalp profit exit: gain={gain*100:.2f}%, RSI={rsi:.1f}", slot.get("symbol"), target_slot_id=slot.get("slot_id"))
        return None
