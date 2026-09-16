from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


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
    Fast MX/USDT Spot strategy.

    Entry:
    - Detect a recent small dip.
    - Detect the first meaningful recovery.
    - RSI must be stable or turning upward.
    - EMA slope is used as a confirmation, not as a requirement
      for a large trend.
    - Avoid chasing an already extended candle.

    Exit:
    - Exit management is handled by main.py.
    - main.py tracks highest_price and trailing stop.
    - main.py handles stop loss and maximum holding time.
    """

    def __init__(
        self,
        rsi_period: int = 14,
        stop_loss_pct: float = 0.004,
        trailing_stop_activation_pct: float = 0.0015,
        trailing_stop_offset_pct: float = 0.0005,
        min_slot_price_diff_pct: float = 0.0005,
        rsi_oversold: float = 35.0,
        rsi_overbought: float = 70.0,
        bollinger_b_entry: float = 0.45,
    ) -> None:

        self.rsi_period = max(
            2,
            int(rsi_period),
        )

        self.stop_loss_pct = max(
            0.0,
            float(stop_loss_pct),
        )

        self.trailing_stop_activation_pct = max(
            0.0,
            float(trailing_stop_activation_pct),
        )

        self.trailing_stop_offset_pct = max(
            0.0,
            float(trailing_stop_offset_pct),
        )

        self.min_slot_price_diff_pct = max(
            0.0,
            float(min_slot_price_diff_pct),
        )

        self.rsi_oversold = float(
            rsi_oversold
        )

        self.rsi_overbought = float(
            rsi_overbought
        )

        self.bollinger_b_entry = float(
            bollinger_b_entry
        )

    # ================================================================
    # SAFE FLOAT
    # ================================================================

    @staticmethod
    def _safe_float(
        value: Any,
        default: float = 0.0,
    ) -> float:

        try:
            value = float(value)

            if not np.isfinite(value):
                return default

            return value

        except (TypeError, ValueError):
            return default

    # ================================================================
    # INDICATORS
    # ================================================================

    def calculate_indicators(
        self,
        df: pd.DataFrame,
    ) -> pd.DataFrame:

        if df is None or df.empty:
            return pd.DataFrame()

        result = df.copy()

        required_columns = [
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]

        for column in required_columns:

            if column not in result.columns:
                raise ValueError(
                    f"Missing required OHLCV column: {column}"
                )

            result[column] = pd.to_numeric(
                result[column],
                errors="coerce",
            )

        result = result.dropna(
            subset=[
                "open",
                "high",
                "low",
                "close",
            ]
        )

        if result.empty:
            return result

        close = result["close"]

        # ============================================================
        # RSI
        # ============================================================

        delta = close.diff()

        gain = delta.clip(
            lower=0.0
        )

        loss = -delta.clip(
            upper=0.0
        )

        avg_gain = gain.ewm(
            alpha=1.0 / self.rsi_period,
            adjust=False,
            min_periods=self.rsi_period,
        ).mean()

        avg_loss = loss.ewm(
            alpha=1.0 / self.rsi_period,
            adjust=False,
            min_periods=self.rsi_period,
        ).mean()

        rs = avg_gain / avg_loss.replace(
            0,
            np.nan,
        )

        rsi = 100.0 - (
            100.0 / (1.0 + rs)
        )

        rsi = rsi.where(
            avg_loss != 0,
            100.0,
        )

        both_zero = (
            (avg_gain == 0)
            & (avg_loss == 0)
        )

        rsi = rsi.where(
            ~both_zero,
            50.0,
        )

        result["rsi"] = rsi.clip(
            0.0,
            100.0,
        )

        # ============================================================
        # EMA
        #
        # main.py explicitly requires:
        #   ema
        #   ema_slope
        # ============================================================

        ema_period = 9

        result["ema"] = close.ewm(
            span=ema_period,
            adjust=False,
            min_periods=ema_period,
        ).mean()

        # EMA slope as percentage change between consecutive EMA values.
        result["ema_slope"] = (
            result["ema"].pct_change()
        )

        # ============================================================
        # BOLLINGER BANDS
        #
        # main.py explicitly requires:
        #   bb_percent_b
        #
        # Keep percent_b too for compatibility.
        # ============================================================

        bb_period = 20

        bb_middle = close.rolling(
            bb_period,
            min_periods=bb_period,
        ).mean()

        bb_std = close.rolling(
            bb_period,
            min_periods=bb_period,
        ).std(
            ddof=0
        )

        bb_upper = (
            bb_middle
            + (2.0 * bb_std)
        )

        bb_lower = (
            bb_middle
            - (2.0 * bb_std)
        )

        result["bb_middle"] = bb_middle
        result["bb_upper"] = bb_upper
        result["bb_lower"] = bb_lower

        band_width = (
            bb_upper - bb_lower
        )

        bb_percent_b = (
            (close - bb_lower)
            / band_width.replace(
                0,
                np.nan,
            )
        )

        result["bb_percent_b"] = (
            bb_percent_b
        )

        # Backward-compatible alias.
        result["percent_b"] = (
            bb_percent_b
        )

        # ============================================================
        # ATR
        # ============================================================

        previous_close = close.shift(1)

        tr1 = (
            result["high"]
            - result["low"]
        )

        tr2 = (
            result["high"]
            - previous_close
        ).abs()

        tr3 = (
            result["low"]
            - previous_close
        ).abs()

        true_range = pd.concat(
            [
                tr1,
                tr2,
                tr3,
            ],
            axis=1,
        ).max(axis=1)

        result["atr"] = (
            true_range
            .rolling(
                14,
                min_periods=5,
            )
            .mean()
        )

        return result

    # ================================================================
    # ENSURE INDICATORS
    # ================================================================

    def _ensure_indicators(
        self,
        df: pd.DataFrame,
    ) -> pd.DataFrame:

        required = {
            "rsi",
            "bb_percent_b",
            "ema",
            "ema_slope",
            "atr",
        }

        if not required.issubset(
            df.columns
        ):
            return self.calculate_indicators(
                df
            )

        result = df.copy()

        for column in required:

            result[column] = pd.to_numeric(
                result[column],
                errors="coerce",
            )

        # Maintain compatibility with code
        # expecting percent_b.
        if "percent_b" not in result.columns:
            result["percent_b"] = (
                result["bb_percent_b"]
            )

        return result

    # ================================================================
    # ENTRY SIGNAL
    # ================================================================

    def evaluate_entry_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        positions: Dict[str, Any],
        available_slot_id: Optional[str],
    ) -> TradeSignal:

        if df is None or df.empty:

            return TradeSignal(
                action="HOLD",
                reason="NO_DATA",
                price=0.0,
            )

        df = self._ensure_indicators(
            df
        )

        minimum_candles = max(
            25,
            self.rsi_period + 5,
        )

        if len(df) < minimum_candles:

            price = 0.0

            if "close" in df.columns:
                price = self._safe_float(
                    df["close"].iloc[-1]
                )

            return TradeSignal(
                action="HOLD",
                reason="NOT_ENOUGH_DATA",
                price=price,
            )

        # main.py supplies CLOSED candles.
        current = df.iloc[-1]
        prev = df.iloc[-2]
        prev2 = df.iloc[-3]
        prev3 = df.iloc[-4]

        current_price = self._safe_float(
            current["close"]
        )

        if current_price <= 0:

            return TradeSignal(
                action="HOLD",
                reason="INVALID_PRICE",
                price=current_price,
            )

        # ============================================================
        # INDICATORS
        # ============================================================

        rsi_current = self._safe_float(
            current["rsi"],
            50.0,
        )

        rsi_prev = self._safe_float(
            prev["rsi"],
            50.0,
        )

        rsi_prev2 = self._safe_float(
            prev2["rsi"],
            50.0,
        )

        percent_b = self._safe_float(
            current["bb_percent_b"],
            0.5,
        )

        percent_b_prev = self._safe_float(
            prev["bb_percent_b"],
            0.5,
        )

        ema_current = self._safe_float(
            current["ema"],
            current_price,
        )

        ema_prev = self._safe_float(
            prev["ema"],
            ema_current,
        )

        ema_slope = self._safe_float(
            current["ema_slope"],
            0.0,
        )

        # ============================================================
        # PRICES
        # ============================================================

        prev_close = self._safe_float(
            prev["close"]
        )

        prev2_close = self._safe_float(
            prev2["close"]
        )

        prev3_close = self._safe_float(
            prev3["close"]
        )

        prev_low = self._safe_float(
            prev["low"]
        )

        prev2_low = self._safe_float(
            prev2["low"]
        )

        prev3_low = self._safe_float(
            prev3["low"]
        )

        prev_high = self._safe_float(
            prev["high"]
        )

        prev2_high = self._safe_float(
            prev2["high"]
        )

        # ============================================================
        # 1. RECENT DIP
        # ============================================================

        decline_1 = (
            prev_close < prev2_close
        )

        decline_2 = (
            prev2_close < prev3_close
        )

        low_decline_1 = (
            prev_low < prev2_low
        )

        low_decline_2 = (
            prev2_low < prev3_low
        )

        recent_reference = max(
            prev3_close,
            prev2_close,
            prev2_high,
        )

        dip_pct = 0.0

        if recent_reference > 0:

            dip_pct = (
                (
                    recent_reference
                    - prev_close
                )
                / recent_reference
            ) * 100.0

        # Very small decline qualifies.
        small_dip = (
            dip_pct >= 0.01
        )

        dip_detected = (
            decline_1
            or decline_2
            or low_decline_1
            or low_decline_2
            or small_dip
        )

        # ============================================================
        # 2. FIRST RECOVERY
        # ============================================================

        price_rebound = (
            current_price > prev_close
        )

        candle_recovery = (
            prev_close >= prev2_close
            or prev_high > prev2_high
        )

        break_previous_high = (
            current_price > prev_high
        )

        rebound = (
            price_rebound
            or candle_recovery
            or break_previous_high
        )

        rebound_pct = 0.0

        if prev_close > 0:

            rebound_pct = (
                (
                    current_price
                    - prev_close
                )
                / prev_close
            ) * 100.0

        small_rebound_confirmed = (
            rebound_pct >= 0.0
        )

        # ============================================================
        # 3. RSI
        #
        # No need to reach oversold.
        # ============================================================

        rsi_turning_up = (
            rsi_current >= rsi_prev
        )

        rsi_stabilizing = (
            rsi_prev >= rsi_prev2
        )

        rsi_recovery = (
            rsi_turning_up
            or rsi_stabilizing
        )

        rsi_not_extreme = (
            rsi_current
            <= self.rsi_overbought
        )

        # ============================================================
        # 4. EMA
        #
        # A positive slope is preferred, but an immediate reversal
        # is allowed even if the EMA has not turned positive yet.
        # ============================================================

        ema_rising = (
            ema_current >= ema_prev
        )

        price_above_ema = (
            current_price >= ema_current
        )

        ema_recovery = (
            ema_rising
            or price_above_ema
        )

        # ============================================================
        # 5. BOLLINGER CONTEXT
        # ============================================================

        lower_band_context = (
            percent_b <= 0.60
        )

        earlier_lower_band_context = (
            percent_b_prev <= 0.65
        )

        pullback_context = (
            lower_band_context
            or earlier_lower_band_context
            or dip_detected
        )

        # ============================================================
        # 6. DON'T CHASE
        # ============================================================

        candle_extension_pct = 0.0

        if prev_close > 0:

            candle_extension_pct = (
                (
                    current_price
                    - prev_close
                )
                / prev_close
            ) * 100.0

        not_chasing = (
            candle_extension_pct <= 0.35
        )

        # ============================================================
        # 7. ACTIVE POSITION PROTECTION
        # ============================================================

        if positions:

            for _, position in positions.items():

                if not isinstance(
                    position,
                    dict,
                ):
                    continue

                if not position.get(
                    "active",
                    True,
                ):
                    continue

                position_symbol = str(
                    position.get(
                        "symbol",
                        symbol,
                    )
                )

                if position_symbol != symbol:
                    continue

                return TradeSignal(
                    action="HOLD",
                    reason="POSITION_ALREADY_ACTIVE",
                    price=current_price,
                    rsi_value=rsi_current,
                    percent_b=percent_b,
                )

        # ============================================================
        # 8. AVAILABLE SLOT
        # ============================================================

        if not available_slot_id:

            return TradeSignal(
                action="HOLD",
                reason="NO_AVAILABLE_SLOT",
                price=current_price,
                rsi_value=rsi_current,
                percent_b=percent_b,
            )

        # ============================================================
        # 9. DUPLICATE PRICE PROTECTION
        # ============================================================

        if len(df) >= 8:

            historical_reference = (
                self._safe_float(
                    df["close"].iloc[-8],
                    current_price,
                )
            )

        else:

            historical_reference = (
                self._safe_float(
                    df["close"].iloc[0],
                    current_price,
                )
            )

        price_distance_pct = 0.0

        if historical_reference > 0:

            price_distance_pct = abs(
                (
                    current_price
                    - historical_reference
                )
                / historical_reference
            )

        duplicate_guard_ok = (
            self.min_slot_price_diff_pct <= 0
            or price_distance_pct
            >= self.min_slot_price_diff_pct
        )

        # ============================================================
        # 10. FAST ENTRY
        #
        # The important change:
        #
        # We do NOT require every indicator to turn bullish.
        # A recent dip + first recovery + stable RSI is enough.
        #
        # EMA is confirmation only.
        # ============================================================

        fast_recovery_entry = (
            dip_detected
            and rebound
            and rsi_recovery
            and rsi_not_extreme
            and pullback_context
            and small_rebound_confirmed
            and not_chasing
        )

        # ============================================================
        # 11. IMMEDIATE REVERSAL
        #
        # Allows an especially fast entry after a new/lower low.
        # ============================================================

        immediate_reversal = (
            current_price > prev_close
            and prev_low <= prev2_low
            and rsi_current >= rsi_prev
            and rsi_current <= 65.0
            and not_chasing
        )

        # ============================================================
        # 12. EMA-ASSISTED FAST ENTRY
        #
        # This can trigger when price is recovering around the EMA.
        # ============================================================

        ema_fast_reversal = (
            dip_detected
            and current_price > prev_close
            and rsi_current >= rsi_prev
            and rsi_current <= 67.0
            and ema_recovery
            and not_chasing
        )

        buy_signal = (
            fast_recovery_entry
            or immediate_reversal
            or ema_fast_reversal
        )

        # ============================================================
        # 13. STOP LOSS
        # ============================================================

        suggested_sl = None

        if self.stop_loss_pct > 0:

            suggested_sl = (
                current_price
                * (
                    1.0
                    - self.stop_loss_pct
                )
            )

        # ============================================================
        # 14. TRAILING ACTIVATION TARGET
        # ============================================================

        suggested_tp = None

        if (
            self.trailing_stop_activation_pct
            > 0
        ):

            suggested_tp = (
                current_price
                * (
                    1.0
                    + self.trailing_stop_activation_pct
                )
            )

        # ============================================================
        # 15. BUY
        # ============================================================

        if (
            buy_signal
            and duplicate_guard_ok
        ):

            if (
                immediate_reversal
                and not fast_recovery_entry
            ):

                reason = (
                    "FAST_IMMEDIATE_REVERSAL"
                    f" | dip={dip_pct:.3f}%"
                    f" | rebound={rebound_pct:.3f}%"
                    f" | RSI={rsi_current:.2f}"
                    f" | %B={percent_b:.3f}"
                    f" | EMA_SLOPE={ema_slope:.6f}"
                )

            elif (
                ema_fast_reversal
                and not fast_recovery_entry
            ):

                reason = (
                    "FAST_EMA_REVERSAL"
                    f" | dip={dip_pct:.3f}%"
                    f" | rebound={rebound_pct:.3f}%"
                    f" | RSI={rsi_current:.2f}"
                    f" | %B={percent_b:.3f}"
                    f" | EMA_SLOPE={ema_slope:.6f}"
                )

            else:

                reason = (
                    "FAST_BOTTOM_REBOUND"
                    f" | dip={dip_pct:.3f}%"
                    f" | rebound={rebound_pct:.3f}%"
                    f" | RSI={rsi_current:.2f}"
                    f" | %B={percent_b:.3f}"
                    f" | EMA_SLOPE={ema_slope:.6f}"
                )

            return TradeSignal(
                action="BUY",
                reason=reason,
                price=current_price,
                target_slot_id=available_slot_id,
                suggested_sl=suggested_sl,
                suggested_tp=suggested_tp,
                rsi_value=rsi_current,
                percent_b=percent_b,
            )

        # ============================================================
        # 16. HOLD DIAGNOSTICS
        # ============================================================

        reason = (
            "WAIT_FAST_REVERSAL"
            f" | dip={dip_detected}"
            f" | rebound={rebound}"
            f" | rsi_recovery={rsi_recovery}"
            f" | rsi={rsi_current:.2f}"
            f" | %B={percent_b:.3f}"
            f" | ema={ema_current:.2f}"
            f" | ema_slope={ema_slope:.6f}"
            f" | ema_recovery={ema_recovery}"
            f" | pullback={pullback_context}"
            f" | not_chasing={not_chasing}"
            f" | duplicate_guard={duplicate_guard_ok}"
        )

        return TradeSignal(
            action="HOLD",
            reason=reason,
            price=current_price,
            rsi_value=rsi_current,
            percent_b=percent_b,
        )

    # ================================================================
    # EXIT API
    # ================================================================

    def evaluate_slot_exit(
        self,
        symbol: str,
        df: pd.DataFrame,
        position: Dict[str, Any],
        current_price: float,
    ) -> Optional[TradeSignal]:

        # main.py controls all exits.
        return None

    # ================================================================
    # BACKWARD COMPATIBILITY
    # ================================================================

    def generate_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        positions: Dict[str, Any],
        available_slot_id: Optional[str] = None,
    ) -> TradeSignal:

        return self.evaluate_entry_signal(
            symbol=symbol,
            df=df,
            positions=positions,
            available_slot_id=available_slot_id,
        )
