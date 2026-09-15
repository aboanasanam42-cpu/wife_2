from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class TradeSignal:
    action: str
    reason: str
    price: float

    # Fields expected by main.py
    target_slot_id: Optional[str] = None
    suggested_sl: Optional[float] = None
    suggested_tp: Optional[float] = None

    # Logging fields expected by main.py
    rsi_value: float = 0.0
    percent_b: float = 0.0


class SpotStrategy:
    """
    Fast MEXC Spot BTC/USDT strategy.

    IMPORTANT:
    - This class does NOT manage API keys.
    - This class does NOT select futures.
    - Exchange type remains controlled by exchange_client.py.
    - Symbol/timeframe remain controlled by the environment.
    - main.py manages real order execution and trailing state.

    ENTRY:
        Fast local bottom
        + short dip
        + first rebound
        + improving RSI
        + pullback context

    EXIT:
        main.py manages:
        - highest price
        - trailing stop
        - trailing decline confirmations
        - hard stop
        - maximum hold time

    The strategy deliberately does NOT require deep oversold RSI.
    """

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
        self.rsi_period = int(rsi_period)
        self.rsi_oversold = float(rsi_oversold)
        self.rsi_overbought = float(rsi_overbought)

        self.ema_period = int(ema_period)

        self.bollinger_period = int(
            bollinger_period
        )

        self.bollinger_std = float(
            bollinger_std
        )

        self.bollinger_b_entry = float(
            bollinger_b_entry
        )

        self.atr_period = int(
            atr_period
        )

        self.stop_loss_pct = float(
            stop_loss_pct
        )

        self.take_profit_pct = float(
            take_profit_pct
        )

        self.trailing_stop_activation_pct = float(
            trailing_stop_activation_pct
        )

        self.trailing_stop_offset_pct = float(
            trailing_stop_offset_pct
        )

        self.min_slot_price_diff_pct = float(
            min_slot_price_diff_pct
        )

    # ==============================================================
    # HELPERS
    # ==============================================================

    @staticmethod
    def _valid_number(value) -> bool:
        try:
            return bool(np.isfinite(float(value)))
        except (TypeError, ValueError):
            return False

    def _hold(
        self,
        price: float,
        reason: str,
        rsi: float = 0.0,
        percent_b: float = 0.0,
        slot_id: Optional[str] = None,
    ) -> TradeSignal:
        return TradeSignal(
            action="HOLD",
            reason=reason,
            price=float(price),
            target_slot_id=slot_id,
            rsi_value=float(rsi),
            percent_b=float(percent_b),
        )

    # ==============================================================
    # INDICATORS
    # ==============================================================

    def calculate_indicators(
        self,
        df: pd.DataFrame,
    ) -> pd.DataFrame:

        if df is None or df.empty:
            return pd.DataFrame()

        df = df.copy()

        required = [
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]

        for column in required:
            if column not in df.columns:
                raise ValueError(
                    f"Missing required OHLCV column: {column}"
                )

            df[column] = pd.to_numeric(
                df[column],
                errors="coerce",
            )

        df = df.replace(
            [np.inf, -np.inf],
            np.nan,
        )

        df = df.dropna(
            subset=[
                "open",
                "high",
                "low",
                "close",
            ]
        ).reset_index(drop=True)

        if df.empty:
            return df

        # ----------------------------------------------------------
        # RSI
        # ----------------------------------------------------------

        delta = df["close"].diff()

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

        rs = (
            avg_gain
            / avg_loss.replace(
                0,
                np.nan,
            )
        )

        df["rsi"] = (
            100.0
            - (
                100.0
                / (1.0 + rs)
            )
        )

        # Rising market with zero losses.
        df.loc[
            (avg_loss == 0)
            & (avg_gain > 0),
            "rsi",
        ] = 100.0

        # Falling market with zero gains.
        df.loc[
            (avg_gain == 0)
            & (avg_loss > 0),
            "rsi",
        ] = 0.0

        # Flat market.
        df.loc[
            (avg_gain == 0)
            & (avg_loss == 0),
            "rsi",
        ] = 50.0

        # ----------------------------------------------------------
        # EMA
        # ----------------------------------------------------------

        df["ema"] = df["close"].ewm(
            span=self.ema_period,
            adjust=False,
            min_periods=1,
        ).mean()

        df["ema_slope"] = df["ema"].diff()

        # ----------------------------------------------------------
        # BOLLINGER BANDS
        # ----------------------------------------------------------

        middle = df["close"].rolling(
            self.bollinger_period,
            min_periods=self.bollinger_period,
        ).mean()

        std = df["close"].rolling(
            self.bollinger_period,
            min_periods=self.bollinger_period,
        ).std()

        df["bb_middle"] = middle

        df["bb_upper"] = (
            middle
            + (
                self.bollinger_std
                * std
            )
        )

        df["bb_lower"] = (
            middle
            - (
                self.bollinger_std
                * std
            )
        )

        band_width = (
            df["bb_upper"]
            - df["bb_lower"]
        )

        df["bb_percent_b"] = (
            (
                df["close"]
                - df["bb_lower"]
            )
            / band_width.replace(
                0,
                np.nan,
            )
        )

        # ----------------------------------------------------------
        # ATR
        # ----------------------------------------------------------

        previous_close = (
            df["close"].shift(1)
        )

        tr1 = (
            df["high"]
            - df["low"]
        )

        tr2 = (
            df["high"]
            - previous_close
        ).abs()

        tr3 = (
            df["low"]
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

        df["atr"] = (
            true_range.rolling(
                self.atr_period,
                min_periods=self.atr_period,
            ).mean()
        )

        df = df.replace(
            [np.inf, -np.inf],
            np.nan,
        )

        return df

    # ==============================================================
    # ENSURE INDICATORS
    # ==============================================================

    def _ensure_indicators(
        self,
        df: pd.DataFrame,
    ) -> pd.DataFrame:

        required = {
            "rsi",
            "ema",
            "ema_slope",
            "bb_middle",
            "bb_upper",
            "bb_lower",
            "bb_percent_b",
            "atr",
        }

        if not required.issubset(
            set(df.columns)
        ):
            return self.calculate_indicators(
                df
            )

        df = df.copy()

        df = df.replace(
            [np.inf, -np.inf],
            np.nan,
        )

        return df

    # ==============================================================
    # ENTRY
    # ==============================================================

    def evaluate_entry_signal(
        self,
        symbol,
        df: pd.DataFrame,
        positions,
        available_slot_id,
    ) -> TradeSignal:
        """
        Signature intentionally matches main.py:

            strategy.evaluate_entry_signal(
                SYMBOL,
                df,
                positions,
                available_slot_id,
            )
        """

        if available_slot_id is None:
            return self._hold(
                0.0,
                "No available trading slot",
            )

        df = self._ensure_indicators(
            df
        )

        if df.empty:
            return self._hold(
                0.0,
                "No candle data",
                slot_id=available_slot_id,
            )

        if len(df) < max(
            self.bollinger_period,
            self.rsi_period,
            25,
        ) + 3:

            return self._hold(
                float(df["close"].iloc[-1]),
                "Waiting for enough closed 1m candles",
                slot_id=available_slot_id,
            )

        # ----------------------------------------------------------
        # Last CLOSED candles
        #
        # main.py already removes the currently forming candle.
        # ----------------------------------------------------------

        last = df.iloc[-1]
        prev = df.iloc[-2]
        prev2 = df.iloc[-3]
        prev3 = df.iloc[-4]
        prev4 = df.iloc[-5]

        current_price = float(
            last["close"]
        )

        rsi_current = float(
            last["rsi"]
        )

        rsi_prev = float(
            prev["rsi"]
        )

        rsi_prev2 = float(
            prev2["rsi"]
        )

        rsi_prev3 = float(
            prev3["rsi"]
        )

        rsi_prev4 = float(
            prev4["rsi"]
        )

        pct_b_current = float(
            last["bb_percent_b"]
        )

        pct_b_prev = float(
            prev["bb_percent_b"]
        )

        pct_b_prev2 = float(
            prev2["bb_percent_b"]
        )

        atr = float(
            last["atr"]
        )

        if not all(
            self._valid_number(value)
            for value in [
                current_price,
                rsi_current,
                rsi_prev,
                rsi_prev2,
                rsi_prev3,
                rsi_prev4,
                pct_b_current,
                pct_b_prev,
                pct_b_prev2,
            ]
        ):
            return self._hold(
                current_price,
                "Waiting for valid indicators",
                rsi_current
                if self._valid_number(
                    rsi_current
                )
                else 0.0,
                pct_b_current
                if self._valid_number(
                    pct_b_current
                )
                else 0.0,
                available_slot_id,
            )

        # ==========================================================
        # 1. FAST LOCAL BOTTOM
        # ==========================================================

        low_1 = float(
            prev["low"]
        )

        low_2 = float(
            prev2["low"]
        )

        low_3 = float(
            prev3["low"]
        )

        low_4 = float(
            prev4["low"]
        )

        # A short-term local low.
        local_bottom = (
            low_2 <= low_3
            and low_2 <= low_1
        )

        # Also allow an immediate lower low followed by recovery.
        immediate_bottom = (
            low_1 <= low_2
            and current_price
            > float(prev["close"])
        )

        local_bottom = (
            local_bottom
            or immediate_bottom
        )

        # ==========================================================
        # 2. PRICE REBOUND
        # ==========================================================

        prev_close = float(
            prev["close"]
        )

        prev2_close = float(
            prev2["close"]
        )

        prev3_close = float(
            prev3["close"]
        )

        prev_high = float(
            prev["high"]
        )

        prev2_high = float(
            prev2["high"]
        )

        # First upward movement.
        price_rebound = (
            current_price
            > prev_close
        )

        # The preceding closed candle must also show some recovery
        # from the bottom, OR the latest candle must break its high.
        structural_rebound = (
            prev_close
            >= prev2_close
            or prev_high
            > prev2_high
            or current_price
            > prev_high
        )

        rebound = (
            price_rebound
            and structural_rebound
        )

        # ==========================================================
        # 3. RSI RECOVERY
        #
        # OLD:
        #     RSI had to be <= 46.
        #
        # NEW:
        #     RSI does NOT need to be deeply oversold.
        #
        # We want a turning RSI:
        #     current > previous >= previous2
        #
        # This allows fast bottoms around RSI 45-55.
        # ==========================================================

        rsi_turning_up = (
            rsi_current
            > rsi_prev
            and rsi_prev
            >= rsi_prev2
        )

        rsi_recovery_zone = (
            rsi_prev
            <= max(
                self.rsi_oversold
                + 17.0,
                55.0,
            )
        )

        rsi_reversal = (
            rsi_turning_up
            and rsi_recovery_zone
        )

        # ==========================================================
        # 4. DIP / PULLBACK
        # ==========================================================

        lower_band_context = (
            pct_b_prev
            <= max(
                self.bollinger_b_entry
                + 0.20,
                0.40,
            )
        )

        earlier_lower_band_context = (
            pct_b_prev2
            <= max(
                self.bollinger_b_entry
                + 0.25,
                0.45,
            )
        )

        price_dip_context = (
            low_1 <= low_2
            or low_2 <= low_3
            or prev_close <= prev2_close
        )

        pullback_context = (
            lower_band_context
            or earlier_lower_band_context
            or price_dip_context
        )

        # ==========================================================
        # 5. MINIMUM REBOUND
        #
        # We don't wait for a large rebound.
        # A very small bounce from the short-term bottom is enough.
        # ==========================================================

        local_low = min(
            low_1,
            low_2,
            low_3,
            low_4,
        )

        rebound_pct = 0.0

        if local_low > 0:
            rebound_pct = (
                (
                    current_price
                    - local_low
                )
                / local_low
                * 100.0
            )

        small_rebound_confirmed = (
            rebound_pct >= 0.005
        )

        # ==========================================================
        # 6. DON'T BUY A STRONG EXTENDED CANDLE
        #
        # The objective is bottom entry, not chasing a pump.
        # ==========================================================

        candle_extension_pct = 0.0

        if prev_close > 0:
            candle_extension_pct = (
                (
                    current_price
                    - prev_close
                )
                / prev_close
                * 100.0
            )

        not_chasing = (
            candle_extension_pct
            <= 0.35
        )

        # ==========================================================
        # 7. FINAL CONFIRMATION
        # ==========================================================

        confirmed_bottom = (
            local_bottom
            and rebound
            and rsi_reversal
            and pullback_context
            and small_rebound_confirmed
            and not_chasing
        )

        # ==========================================================
        # BUY
        # ==========================================================

        if confirmed_bottom:

            # ------------------------------------------------------
            # Duplicate-entry protection only.
            #
            # This does NOT impose an artificial delay.
            # It simply prevents buying the same already processed
            # movement repeatedly.
            # ------------------------------------------------------

            if len(df) >= 8:

                old_price = float(
                    df["close"].iloc[-8]
                )

                if (
                    self._valid_number(
                        old_price
                    )
                    and old_price > 0
                ):

                    price_difference_pct = (
                        abs(
                            current_price
                            - old_price
                        )
                        / old_price
                        * 100.0
                    )

                    # Only block if the configured anti-clustering
                    # threshold is explicitly greater than zero.
                    if (
                        self.min_slot_price_diff_pct
                        > 0
                        and price_difference_pct
                        < self.min_slot_price_diff_pct
                    ):
                        return self._hold(
                            current_price,
                            (
                                "Bottom confirmed but "
                                "entry is too close to "
                                "previous slot reference"
                            ),
                            rsi_current,
                            pct_b_current,
                            available_slot_id,
                        )

            # ------------------------------------------------------
            # Protective stop.
            #
            # Keep the existing environment-controlled stop.
            # ------------------------------------------------------

            if (
                self._valid_number(
                    atr
                )
                and atr > 0
            ):
                atr_stop_distance = (
                    atr * 1.20
                )
            else:
                atr_stop_distance = (
                    current_price * 0.003
                )

            configured_stop_distance = (
                current_price
                * self.stop_loss_pct
            )

            stop_distance = max(
                configured_stop_distance,
                atr_stop_distance,
            )

            stop_loss = (
                current_price
                - stop_distance
            )

            if stop_loss <= 0:
                stop_loss = (
                    current_price
                    * (
                        1.0
                        - self.stop_loss_pct
                    )
                )

            # ------------------------------------------------------
            # TP is supplied only for compatibility with main.py.
            #
            # main.py does NOT use this value as its trailing exit.
            # ------------------------------------------------------

            suggested_tp = (
                current_price
                * (
                    1.0
                    + self.take_profit_pct
                )
            )

            reason = (
                "FAST BOTTOM + FIRST REBOUND | "
                f"RSI={rsi_prev2:.2f}"
                f"->{rsi_prev:.2f}"
                f"->{rsi_current:.2f} | "
                f"rebound={rebound_pct:.3f}% | "
                f"local_bottom={local_bottom} | "
                f"pullback={pullback_context}"
            )

            return TradeSignal(
                action="BUY",
                reason=reason,
                price=current_price,
                target_slot_id=available_slot_id,
                suggested_sl=stop_loss,
                suggested_tp=suggested_tp,
                rsi_value=rsi_current,
                percent_b=pct_b_current,
            )

        # ==========================================================
        # HOLD
        # ==========================================================

        reason = (
            "WAIT_BOTTOM_REBOUND | "
            f"bottom={local_bottom} | "
            f"rebound={rebound} | "
            f"rsi_reversal={rsi_reversal} | "
            f"pullback={pullback_context} | "
            f"small_rebound={small_rebound_confirmed} | "
            f"not_chasing={not_chasing} | "
            f"RSI={rsi_current:.2f} | "
            f"%B={pct_b_current:.3f} | "
            f"rebound={rebound_pct:.3f}%"
        )

        return self._hold(
            current_price,
            reason,
            rsi_current,
            pct_b_current,
            available_slot_id,
        )

    # ==============================================================
    # EXIT COMPATIBILITY
    # ==============================================================

    def evaluate_slot_exit(
        self,
        entry_price: float,
        current_price: float,
        highest_price: Optional[float] = None,
        current_rsi: Optional[float] = None,
        previous_rsi: Optional[float] = None,
        current_pct_b: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
    ) -> Optional[TradeSignal]:
        """
        Compatibility method.

        The supplied main.py manages actual exits through:
            update_trailing_position()
            should_exit_position()

        Therefore this method intentionally does not compete with main.py.
        """

        if not self._valid_number(
            entry_price
        ):
            return None

        if not self._valid_number(
            current_price
        ):
            return None

        entry_price = float(
            entry_price
        )

        current_price = float(
            current_price
        )

        if entry_price <= 0:
            return None

        # This strategy deliberately leaves exit management to main.py.
        return None

    # ==============================================================
    # COMPATIBILITY ALIAS
    # ==============================================================

    def generate_signal(
        self,
        symbol,
        df: pd.DataFrame,
        positions,
        available_slot_id,
    ) -> TradeSignal:

        return self.evaluate_entry_signal(
            symbol,
            df,
            positions,
            available_slot_id,
        )
