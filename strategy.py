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

    الهدف:
    - التقاط الارتداد بسرعة بعد هبوط صغير.
    - عدم انتظار تشكل اتجاه صاعد قوي بالكامل.
    - استخدام RSI و EMA و Bollinger كعوامل تأكيد مرنة.
    - السماح بالدخول حتى لو لم يصبح EMA موجبًا بعد.
    - تجنب الدخول فقط عندما تكون الشمعة ممتدة بشكل مبالغ فيه.

    Entry:
    - Recent dip OR short-term weakness.
    - Current candle showing recovery.
    - RSI stable / recovering.
    - Price recovering around or above EMA.
    - Bollinger context remains acceptable.
    - No active position for the same symbol.
    - Available slot required.

    Exit:
    - Exit management is handled by main.py.
    - main.py manages trailing stop, stop loss and max holding time.
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

        self.buy_fee = float(buy_fee)
        self.sell_fee = float(sell_fee)
        self.net_profit_rate = float(net_profit_rate)

    def calculate_take_profit_price(self, buy_price: float) -> float:
        return calculate_take_profit_price(
            buy_price,
            buy_fee=self.buy_fee,
            sell_fee=self.sell_fee,
            net_profit_rate=self.net_profit_rate,
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
        # ============================================================

        ema_period = 9

        result["ema"] = close.ewm(
            span=ema_period,
            adjust=False,
            min_periods=ema_period,
        ).mean()

        result["ema_slope"] = (
            result["ema"].pct_change()
        )

        # ============================================================
        # BOLLINGER BANDS
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

        # Backward compatibility.
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
            rsi_current,
        )

        rsi_prev2 = self._safe_float(
            prev2["rsi"],
            rsi_prev,
        )

        percent_b = self._safe_float(
            current["bb_percent_b"],
            0.5,
        )

        percent_b_prev = self._safe_float(
            prev["bb_percent_b"],
            percent_b,
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

        current_open = self._safe_float(
            current["open"],
            current_price,
        )

        current_high = self._safe_float(
            current["high"],
            current_price,
        )

        current_low = self._safe_float(
            current["low"],
            current_price,
        )

        prev_close = self._safe_float(
            prev["close"],
            current_price,
        )

        prev2_close = self._safe_float(
            prev2["close"],
            prev_close,
        )

        prev3_close = self._safe_float(
            prev3["close"],
            prev2_close,
        )

        prev_low = self._safe_float(
            prev["low"],
            prev_close,
        )

        prev2_low = self._safe_float(
            prev2["low"],
            prev2_close,
        )

        prev3_low = self._safe_float(
            prev3["low"],
            prev3_close,
        )

        prev_high = self._safe_float(
            prev["high"],
            prev_close,
        )

        prev2_high = self._safe_float(
            prev2["high"],
            prev2_close,
        )

        # ============================================================
        # 1. RECENT DIP
        #
        # أخف من النسخة السابقة.
        # ============================================================

        decline_1 = (
            prev_close < prev2_close
        )

        decline_2 = (
            prev2_close < prev3_close
        )

        low_decline_1 = (
            prev_low <= prev2_low
        )

        low_decline_2 = (
            prev2_low <= prev3_low
        )

        recent_reference = max(
            prev3_close,
            prev2_close,
            prev2_high,
            prev_high,
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

        # دخول أسرع حتى مع حركة هبوط صغيرة.
        small_dip = (
            dip_pct >= 0.005
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
            current_price >= prev_close
        )

        current_green = (
            current_price >= current_open
        )

        candle_recovery = (
            prev_close >= prev2_close
            or prev_high >= prev2_high
        )

        break_previous_high = (
            current_price >= prev_high
        )

        rebound = (
            price_rebound
            or current_green
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

        # السماح بالارتداد الصغير جدًا.
        small_rebound_confirmed = (
            rebound_pct >= -0.03
        )

        # ============================================================
        # 3. RSI
        #
        # مرن:
        # - لا يشترط oversold.
        # - يكفي ألا يكون RSI في منطقة مبالغة شديدة.
        # ============================================================

        rsi_turning_up = (
            rsi_current >= rsi_prev - 0.50
        )

        rsi_stabilizing = (
            rsi_current >= rsi_prev2 - 1.00
        )

        rsi_recovery = (
            rsi_turning_up
            or rsi_stabilizing
        )

        rsi_not_extreme = (
            rsi_current
            <= self.rsi_overbought
        )

        # إذا كان RSI منخفضًا، نعطيه أفضلية كارتداد.
        rsi_dip_bonus = (
            rsi_current
            <= self.rsi_oversold
        )

        # ============================================================
        # 4. EMA
        #
        # EMA تأكيد وليس شرطًا إلزاميًا.
        # ============================================================

        ema_rising = (
            ema_current
            >= ema_prev * 0.9998
        )

        price_above_ema = (
            current_price
            >= ema_current * 0.999
        )

        ema_recovery = (
            ema_rising
            or price_above_ema
        )

        # ============================================================
        # 5. BOLLINGER CONTEXT
        #
        # أوسع من السابق.
        # ============================================================

        lower_band_context = (
            percent_b
            <= max(
                0.75,
                self.bollinger_b_entry,
            )
        )

        earlier_lower_band_context = (
            percent_b_prev <= 0.80
        )

        # إذا كان السعر تحت/حول Bollinger السفلي
        # نعتبره منطقة ارتداد محتملة.
        bollinger_recovery = (
            percent_b
            >= percent_b_prev - 0.05
        )

        pullback_context = (
            lower_band_context
            or earlier_lower_band_context
            or bollinger_recovery
            or dip_detected
        )

        # ============================================================
        # 6. DON'T CHASE
        #
        # أصبح أكثر تساهلًا.
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
            candle_extension_pct <= 0.60
        )

        # حماية من شمعة ضخمة جدًا.
        current_range_pct = 0.0

        if current_open > 0:

            current_range_pct = (
                (
                    current_high
                    - current_low
                )
                / current_open
            ) * 100.0

        not_extreme_candle = (
            current_range_pct <= 1.50
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
        #
        # أخف من السابق.
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
        # 10. FAST RECOVERY ENTRY
        #
        # لا نطلب كل المؤشرات أن تكون bullish.
        # ============================================================

        fast_recovery_entry = (
            dip_detected
            and rebound
            and rsi_recovery
            and rsi_not_extreme
            and pullback_context
            and small_rebound_confirmed
            and not_chasing
            and not_extreme_candle
        )

        # ============================================================
        # 11. IMMEDIATE REVERSAL
        #
        # دخول سريع بعد تسجيل قاع جديد أو قريب منه.
        # ============================================================

        immediate_reversal = (
            current_price >= prev_close
            and prev_low <= prev2_low
            and rsi_current >= rsi_prev - 1.50
            and rsi_current <= 70.0
            and not_chasing
            and not_extreme_candle
        )

        # ============================================================
        # 12. RSI QUICK REVERSAL
        #
        # يسمح بالدخول عندما RSI يبدأ بالاستقرار بعد ضعف.
        # ============================================================

        rsi_quick_reversal = (
            rsi_current >= rsi_prev - 0.25
            and rsi_current <= 68.0
            and current_price >= prev_close
            and dip_detected
            and not_chasing
        )

        # ============================================================
        # 13. EMA-ASSISTED FAST ENTRY
        # ============================================================

        ema_fast_reversal = (
            dip_detected
            and current_price >= prev_close
            and rsi_current >= rsi_prev - 1.00
            and rsi_current <= 70.0
            and ema_recovery
            and not_chasing
            and not_extreme_candle
        )

        # ============================================================
        # 14. VERY FAST BOTTOM ENTRY
        #
        # إذا كان RSI منخفضًا + السعر يرتد، لا ننتظر EMA.
        # ============================================================

        bottom_recovery = (
            rsi_dip_bonus
            and current_price >= prev_close
            and rsi_current >= rsi_prev - 1.50
            and not_chasing
            and not_extreme_candle
        )

        buy_signal = (
            fast_recovery_entry
            or immediate_reversal
            or rsi_quick_reversal
            or ema_fast_reversal
            or bottom_recovery
        )

        # ============================================================
        # 15. STOP LOSS
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
        # 16. TRAILING ACTIVATION TARGET
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
        # 17. BUY
        # ============================================================

        if (
            buy_signal
            and duplicate_guard_ok
        ):

            if (
                bottom_recovery
                and not immediate_reversal
            ):

                reason = (
                    "FAST_BOTTOM_RSI_RECOVERY"
                    f" | symbol={symbol}"
                    f" | dip={dip_pct:.3f}%"
                    f" | rebound={rebound_pct:.3f}%"
                    f" | RSI={rsi_current:.2f}"
                    f" | %B={percent_b:.3f}"
                    f" | EMA_SLOPE={ema_slope:.6f}"
                )

            elif (
                immediate_reversal
                and not fast_recovery_entry
            ):

                reason = (
                    "FAST_IMMEDIATE_REVERSAL"
                    f" | symbol={symbol}"
                    f" | dip={dip_pct:.3f}%"
                    f" | rebound={rebound_pct:.3f}%"
                    f" | RSI={rsi_current:.2f}"
                    f" | %B={percent_b:.3f}"
                    f" | EMA_SLOPE={ema_slope:.6f}"
                )

            elif (
                rsi_quick_reversal
                and not fast_recovery_entry
            ):

                reason = (
                    "FAST_RSI_REVERSAL"
                    f" | symbol={symbol}"
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
                    f" | symbol={symbol}"
                    f" | dip={dip_pct:.3f}%"
                    f" | rebound={rebound_pct:.3f}%"
                    f" | RSI={rsi_current:.2f}"
                    f" | %B={percent_b:.3f}"
                    f" | EMA_SLOPE={ema_slope:.6f}"
                )

            else:

                reason = (
                    "FAST_BOTTOM_REBOUND"
                    f" | symbol={symbol}"
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
        # 18. HOLD DIAGNOSTICS
        # ============================================================

        reason = (
            "WAIT_FAST_REVERSAL"
            f" | symbol={symbol}"
            f" | dip={dip_detected}"
            f" | rebound={rebound}"
            f" | rsi_recovery={rsi_recovery}"
            f" | rsi={rsi_current:.2f}"
            f" | %B={percent_b:.3f}"
            f" | ema={ema_current:.8f}"
            f" | ema_slope={ema_slope:.6f}"
            f" | ema_recovery={ema_recovery}"
            f" | pullback={pullback_context}"
            f" | not_chasing={not_chasing}"
            f" | candle_ok={not_extreme_candle}"
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
