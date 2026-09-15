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
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None


class SpotStrategy:
    """
    Fast BTC/USDT Spot strategy.

    Strategy:
    - MEXC Spot only is enforced by the exchange client.
    - BTC/USDT and 1m are controlled by the environment/configuration.
    - BUY quickly after a short local dip and first confirmed rebound.
    - SELL quickly after even a small confirmed pullback from the highest
      price reached after entry.
    - No fixed take-profit is used here; the exit is based on the trailing
      high / pullback logic.
    - Uses closed candles for confirmation where possible.
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
        self.bollinger_period = int(bollinger_period)
        self.bollinger_std = float(bollinger_std)

        self.bollinger_b_entry = float(bollinger_b_entry)
        self.atr_period = int(atr_period)

        self.stop_loss_pct = float(stop_loss_pct)
        self.take_profit_pct = float(take_profit_pct)

        self.trailing_stop_activation_pct = float(
            trailing_stop_activation_pct
        )
        self.trailing_stop_offset_pct = float(
            trailing_stop_offset_pct
        )

        self.min_slot_price_diff_pct = float(min_slot_price_diff_pct)

    # ------------------------------------------------------------------
    # INDICATORS
    # ------------------------------------------------------------------

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate all indicators required by the strategy.

        The function is deliberately defensive because MEXC OHLCV data can
        occasionally contain incomplete/invalid rows.
        """

        if df is None or df.empty:
            return pd.DataFrame()

        df = df.copy()

        required = ["open", "high", "low", "close", "volume"]

        for column in required:
            if column not in df.columns:
                raise ValueError(f"Missing required OHLCV column: {column}")

            df[column] = pd.to_numeric(
                df[column],
                errors="coerce",
            )

        df = df.replace([np.inf, -np.inf], np.nan)

        df = df.dropna(
            subset=["open", "high", "low", "close"]
        ).reset_index(drop=True)

        if df.empty:
            return df

        # --------------------------------------------------------------
        # RSI
        # --------------------------------------------------------------

        delta = df["close"].diff()

        gain = delta.clip(lower=0.0)
        loss = -delta.clip(upper=0.0)

        avg_gain = gain.ewm(
            alpha=1 / self.rsi_period,
            adjust=False,
            min_periods=self.rsi_period,
        ).mean()

        avg_loss = loss.ewm(
            alpha=1 / self.rsi_period,
            adjust=False,
            min_periods=self.rsi_period,
        ).mean()

        rs = avg_gain / avg_loss.replace(0, np.nan)

        df["rsi"] = 100 - (100 / (1 + rs))

        # Flat market handling.
        df.loc[
            (avg_loss == 0) & (avg_gain > 0),
            "rsi",
        ] = 100.0

        df.loc[
            (avg_gain == 0) & (avg_loss > 0),
            "rsi",
        ] = 0.0

        df.loc[
            (avg_gain == 0) & (avg_loss == 0),
            "rsi",
        ] = 50.0

        # --------------------------------------------------------------
        # EMA
        # --------------------------------------------------------------

        df["ema"] = df["close"].ewm(
            span=self.ema_period,
            adjust=False,
            min_periods=1,
        ).mean()

        df["ema_slope"] = df["ema"].diff()

        # --------------------------------------------------------------
        # Bollinger Bands
        # --------------------------------------------------------------

        middle = df["close"].rolling(
            self.bollinger_period,
            min_periods=self.bollinger_period,
        ).mean()

        std = df["close"].rolling(
            self.bollinger_period,
            min_periods=self.bollinger_period,
        ).std()

        df["bb_middle"] = middle
        df["bb_upper"] = middle + (
            self.bollinger_std * std
        )
        df["bb_lower"] = middle - (
            self.bollinger_std * std
        )

        band_width = (
            df["bb_upper"] - df["bb_lower"]
        )

        df["bb_percent_b"] = (
            (df["close"] - df["bb_lower"])
            / band_width.replace(0, np.nan)
        )

        # --------------------------------------------------------------
        # ATR
        # --------------------------------------------------------------

        previous_close = df["close"].shift(1)

        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - previous_close).abs()
        tr3 = (df["low"] - previous_close).abs()

        true_range = pd.concat(
            [tr1, tr2, tr3],
            axis=1,
        ).max(axis=1)

        df["atr"] = true_range.rolling(
            self.atr_period,
            min_periods=self.atr_period,
        ).mean()

        df = df.replace(
            [np.inf, -np.inf],
            np.nan,
        )

        return df

    # ------------------------------------------------------------------
    # INTERNAL HELPERS
    # ------------------------------------------------------------------

    def _ensure_indicators(
        self,
        df: pd.DataFrame,
    ) -> pd.DataFrame:

        required_indicators = {
            "rsi",
            "ema",
            "ema_slope",
            "bb_middle",
            "bb_upper",
            "bb_lower",
            "bb_percent_b",
            "atr",
        }

        if not required_indicators.issubset(
            set(df.columns)
        ):
            return self.calculate_indicators(df)

        cleaned = df.copy()

        cleaned = cleaned.replace(
            [np.inf, -np.inf],
            np.nan,
        )

        return cleaned

    def _valid_number(self, value) -> bool:
        try:
            return bool(np.isfinite(float(value)))
        except (TypeError, ValueError):
            return False

    # ------------------------------------------------------------------
    # ENTRY
    # ------------------------------------------------------------------

    def evaluate_entry_signal(
        self,
        df: pd.DataFrame,
        current_price: float,
        slot_available: bool = True,
    ) -> TradeSignal:

        if not slot_available:
            return TradeSignal(
                action="HOLD",
                reason="No trading slot available",
                price=float(current_price),
            )

        if not self._valid_number(current_price):
            return TradeSignal(
                action="HOLD",
                reason="Invalid current price",
                price=0.0,
            )

        current_price = float(current_price)

        df = self._ensure_indicators(df)

        # We need enough candles to detect a short local bottom.
        if len(df) < max(
            self.bollinger_period,
            self.rsi_period,
            20,
        ) + 3:

            return TradeSignal(
                action="HOLD",
                reason="Waiting for enough 1m candles",
                price=current_price,
            )

        last = df.iloc[-1]
        prev = df.iloc[-2]
        prev2 = df.iloc[-3]
        prev3 = df.iloc[-4]

        values = [
            last["rsi"],
            prev["rsi"],
            prev2["rsi"],
            prev3["rsi"],
            prev["low"],
            prev2["low"],
            prev3["low"],
            prev["close"],
            prev2["close"],
            prev["high"],
            prev2["high"],
            prev["bb_percent_b"],
            prev2["bb_percent_b"],
        ]

        if not all(
            self._valid_number(value)
            for value in values
        ):
            return TradeSignal(
                action="HOLD",
                reason="Waiting for valid indicators",
                price=current_price,
            )

        rsi_current = float(last["rsi"])
        rsi_prev = float(prev["rsi"])
        rsi_prev2 = float(prev2["rsi"])
        rsi_prev3 = float(prev3["rsi"])

        prev_low = float(prev["low"])
        prev2_low = float(prev2["low"])
        prev3_low = float(prev3["low"])

        prev_close = float(prev["close"])
        prev2_close = float(prev2["close"])

        prev_high = float(prev["high"])
        prev2_high = float(prev2["high"])

        prev_pct_b = float(prev["bb_percent_b"])
        prev2_pct_b = float(prev2["bb_percent_b"])

        # --------------------------------------------------------------
        # FAST LOCAL BOTTOM DETECTION
        # --------------------------------------------------------------

        recent_lows = [
            prev_low,
            prev2_low,
            prev3_low,
        ]

        local_bottom = (
            prev2_low <= min(recent_lows)
            or prev_low <= prev2_low
        )

        # --------------------------------------------------------------
        # FIRST REBOUND
        # --------------------------------------------------------------

        rebound_now = (
            current_price > prev_close
        )

        rebound_from_bottom = (
            prev_close >= prev2_close
            or prev_high > prev2_high
        )

        rebound = (
            rebound_now
            and rebound_from_bottom
        )

        # --------------------------------------------------------------
        # FAST RSI RECOVERY
        #
        # IMPORTANT:
        # The old strategy required RSI <= 46, which caused valid
        # rebounds such as RSI 54.62 to be rejected.
        #
        # New logic:
        # RSI must be turning upward, but does NOT need to be deeply
        # oversold.
        # --------------------------------------------------------------

        rsi_reversal = (
            rsi_current > rsi_prev
            and rsi_prev >= rsi_prev2
            and rsi_prev2 <= rsi_prev3
            and rsi_prev <= max(
                self.rsi_oversold + 17.0,
                55.0,
            )
        )

        # --------------------------------------------------------------
        # DIP / PULLBACK CONTEXT
        # --------------------------------------------------------------

        lower_band_touch = (
            prev_pct_b
            <= max(
                self.bollinger_b_entry + 0.20,
                0.40,
            )
        )

        previous_lower_band_touch = (
            prev2_pct_b
            <= max(
                self.bollinger_b_entry + 0.25,
                0.45,
            )
        )

        pullback_context = (
            lower_band_touch
            or previous_lower_band_touch
            or prev_low <= prev2_low
            or prev2_low <= prev3_low
        )

        # --------------------------------------------------------------
        # QUICK PRICE RECOVERY
        #
        # Only a small recovery is required. We do not wait for a large
        # move before buying.
        # --------------------------------------------------------------

        bottom_price = min(
            prev_low,
            prev2_low,
            prev3_low,
        )

        rebound_pct = (
            (current_price - bottom_price)
            / bottom_price
            * 100.0
        )

        small_rebound_confirmed = (
            rebound_pct >= 0.01
        )

        # --------------------------------------------------------------
        # FINAL BUY CONFIRMATION
        #
        # We intentionally keep this fast:
        # local bottom + rebound + RSI recovery + pullback context.
        # --------------------------------------------------------------

        confirmed_bottom = (
            local_bottom
            and rebound
            and rsi_reversal
            and pullback_context
            and small_rebound_confirmed
        )

        if confirmed_bottom:

            # Prevent repeated BUY signals at essentially the same price.
            # This is NOT a delay; it simply prevents duplicate entries
            # while the same rebound is being processed.
            if len(df) >= 6:

                previous_reference = float(
                    df["close"].iloc[-6]
                )

                if self._valid_number(
                    previous_reference
                ) and previous_reference > 0:

                    slot_diff_pct = abs(
                        current_price
                        - previous_reference
                    ) / previous_reference

                    if slot_diff_pct < self.min_slot_price_diff_pct:
                        return TradeSignal(
                            action="HOLD",
                            reason=(
                                "Bottom confirmed but entry is too close "
                                "to previous slot price"
                            ),
                            price=current_price,
                        )

            atr = float(last["atr"])

            if not self._valid_number(atr):
                atr = current_price * 0.002

            # Fast protective stop.
            dynamic_stop_distance = max(
                current_price * 0.003,
                atr * 1.20,
            )

            stop_loss = (
                current_price
                - dynamic_stop_distance
            )

            # Keep stop sane and above zero.
            stop_loss = max(
                stop_loss,
                current_price * (
                    1.0 - self.stop_loss_pct
                ),
            )

            # Kept for compatibility with the existing main.py.
            # Exit logic itself is handled by evaluate_slot_exit.
            take_profit = current_price * (
                1.0 + self.take_profit_pct
            )

            return TradeSignal(
                action="BUY",
                reason=(
                    "FAST BOTTOM + REBOUND CONFIRMED | "
                    f"RSI {rsi_prev2:.2f}->{rsi_prev:.2f}"
                    f"->{rsi_current:.2f} | "
                    f"rebound={rebound_pct:.3f}%"
                ),
                price=current_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

        return TradeSignal(
            action="HOLD",
            reason=(
                "Waiting for fast bottom/rebound | "
                f"local_bottom={local_bottom} | "
                f"rebound={rebound} | "
                f"rsi_reversal={rsi_reversal} | "
                f"pullback={pullback_context} | "
                f"rebound_pct={rebound_pct:.3f}% | "
                f"RSI={rsi_current:.2f} | "
                f"%B={prev_pct_b:.3f}"
            ),
            price=current_price,
        )

    # ------------------------------------------------------------------
    # EXIT
    # ------------------------------------------------------------------

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

        if not self._valid_number(entry_price):
            return None

        if not self._valid_number(current_price):
            return None

        entry_price = float(entry_price)
        current_price = float(current_price)

        if entry_price <= 0:
            return None

        # --------------------------------------------------------------
        # CURRENT PNL
        # --------------------------------------------------------------

        pnl_pct = (
            (current_price - entry_price)
            / entry_price
            * 100.0
        )

        # --------------------------------------------------------------
        # HARD STOP LOSS
        #
        # The trailing logic is the main profit exit, but a hard stop
        # remains as capital protection.
        # --------------------------------------------------------------

        if (
            stop_loss_price is not None
            and self._valid_number(stop_loss_price)
            and current_price <= float(stop_loss_price)
        ):

            return TradeSignal(
                action="SELL",
                reason=(
                    "STOP LOSS | "
                    f"PnL={pnl_pct:.3f}%"
                ),
                price=current_price,
            )

        # --------------------------------------------------------------
        # TRACK HIGHEST PRICE
        # --------------------------------------------------------------

        if (
            highest_price is None
            or not self._valid_number(highest_price)
            or float(highest_price) <= 0
        ):
            highest_price = current_price

        highest_price = max(
            float(highest_price),
            current_price,
            entry_price,
        )

        # --------------------------------------------------------------
        # PROFIT FROM ENTRY
        # --------------------------------------------------------------

        profit_pct_from_entry = (
            (highest_price - entry_price)
            / entry_price
            * 100.0
        )

        # --------------------------------------------------------------
        # FAST TRAILING ACTIVATION
        #
        # As soon as price makes even a small positive move, trailing
        # protection becomes active.
        # --------------------------------------------------------------

        activation_pct = max(
            0.05,
            self.trailing_stop_activation_pct * 100.0,
        )

        trailing_active = (
            profit_pct_from_entry >= activation_pct
        )

        if trailing_active:

            # User requested minimal pullback from the top.
            #
            # The environment's trailing offset remains respected, but
            # the minimum is deliberately very small.
            offset_pct = min(
                max(
                    0.05,
                    self.trailing_stop_offset_pct * 100.0,
                ),
                0.20,
            )

            drawdown_pct = (
                (highest_price - current_price)
                / highest_price
                * 100.0
            )

            if drawdown_pct >= offset_pct:

                return TradeSignal(
                    action="SELL",
                    reason=(
                        "FAST TRAILING SELL | "
                        f"entry={entry_price:.8f} | "
                        f"peak={highest_price:.8f} | "
                        f"price={current_price:.8f} | "
                        f"peak_profit={profit_pct_from_entry:.3f}% | "
                        f"drawdown={drawdown_pct:.3f}%"
                    ),
                    price=current_price,
                )

        # --------------------------------------------------------------
        # MOMENTUM EXIT
        #
        # If the position has a small profit and RSI turns down, exit
        # quickly rather than waiting for a larger reversal.
        # --------------------------------------------------------------

        if (
            current_rsi is not None
            and previous_rsi is not None
            and self._valid_number(current_rsi)
            and self._valid_number(previous_rsi)
        ):

            current_rsi = float(current_rsi)
            previous_rsi = float(previous_rsi)

            rsi_turn_down = (
                current_rsi < previous_rsi
            )

            if (
                pnl_pct >= 0.10
                and rsi_turn_down
                and current_rsi >= 52.0
            ):

                return TradeSignal(
                    action="SELL",
                    reason=(
                        "FAST RSI EXIT | "
                        f"PnL={pnl_pct:.3f}% | "
                        f"RSI={previous_rsi:.2f}->{current_rsi:.2f}"
                    ),
                    price=current_price,
                )

        # --------------------------------------------------------------
        # OVERBOUGHT EXHAUSTION
        # --------------------------------------------------------------

        if (
            current_rsi is not None
            and current_pct_b is not None
            and self._valid_number(current_rsi)
            and self._valid_number(current_pct_b)
        ):

            current_rsi = float(current_rsi)
            current_pct_b = float(current_pct_b)

            if (
                pnl_pct >= 0.10
                and current_rsi >= self.rsi_overbought
                and current_pct_b >= 0.95
            ):

                return TradeSignal(
                    action="SELL",
                    reason=(
                        "OVERBOUGHT EXIT | "
                        f"PnL={pnl_pct:.3f}% | "
                        f"RSI={current_rsi:.2f} | "
                        f"%B={current_pct_b:.3f}"
                    ),
                    price=current_price,
                )

        return None

    # ------------------------------------------------------------------
    # SIMPLE COMPATIBILITY METHOD
    # ------------------------------------------------------------------

    def generate_signal(
        self,
        df: pd.DataFrame,
        current_price: float,
        slot_available: bool = True,
    ) -> TradeSignal:

        return self.evaluate_entry_signal(
            df=df,
            current_price=current_price,
            slot_available=slot_available,
        )
