from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from config import Config


@dataclass
class Position:
    symbol: str
    buy_price: float
    quantity: float
    invested_usdt: float
    opened_at: float


class TradingStrategy:
    """
    MEXC Spot strategy.

    Entry:
        RSI <= BUY_RSI_MAX
        AND current closed candle rebounds by BUY_REBOUND_PCT.

    Exit:
        TAKE_PROFIT_PCT
        OR STOP_LOSS_PCT.

    The strategy does not use pandas.
    """

    def __init__(
        self,
        client: Any,
    ) -> None:

        self.client = client

        self.open_positions: dict[
            str,
            Position,
        ] = {}

    # =========================================================
    # RSI
    # =========================================================

    @staticmethod
    def calculate_rsi(
        closes: list[float],
        period: int,
    ) -> float | None:

        if len(closes) < period + 1:
            return None

        gains: list[float] = []
        losses: list[float] = []

        for previous, current in zip(
            closes[:-1],
            closes[1:],
        ):

            change = current - previous

            if change >= 0:
                gains.append(change)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(-change)

        recent_gains = gains[-period:]
        recent_losses = losses[-period:]

        avg_gain = (
            sum(recent_gains) / period
        )

        avg_loss = (
            sum(recent_losses) / period
        )

        if avg_loss == 0:

            if avg_gain == 0:
                return 50.0

            return 100.0

        rs = avg_gain / avg_loss

        return 100.0 - (
            100.0 / (1.0 + rs)
        )

    # =========================================================
    # ENTRY SIGNAL
    # =========================================================

    def evaluate_entry_signal(
        self,
        symbol: str,
        candles: Any,
        positions: dict[str, Any] | None = None,
        slot_id: str | None = None,
    ) -> dict[str, Any]:

        """
        Compatibility method for CI and external callers.

        Returns:
            {
                "action": "BUY" or "HOLD",
                "price": float,
                "rsi": float | None,
                "rebound_pct": float,
                "reason": str,
                "symbol": str,
                "slot_id": str | None,
            }
        """

        rows: list[Any]

        if hasattr(
            candles,
            "to_dict",
        ):
            rows = candles.to_dict(
                orient="records"
            )

            closes = [
                float(
                    row["close"]
                )
                for row in rows
                if "close" in row
            ]

        else:

            rows = list(
                candles or []
            )

            closes = []

            for row in rows:

                try:
                    closes.append(
                        float(row[4])
                    )
                except (
                    TypeError,
                    ValueError,
                    IndexError,
                ):
                    continue

        if len(closes) < (
            Config.RSI_PERIOD + 3
        ):

            return {
                "action": "HOLD",
                "price": (
                    closes[-1]
                    if closes
                    else 0.0
                ),
                "rsi": None,
                "rebound_pct": 0.0,
                "reason": "INSUFFICIENT_DATA",
                "symbol": symbol,
                "slot_id": slot_id,
            }

        # Ignore the currently forming candle.
        closed = closes[:-1]

        current = closed[-1]
        previous = closed[-2]

        rsi = self.calculate_rsi(
            closed,
            Config.RSI_PERIOD,
        )

        if rsi is None:

            return {
                "action": "HOLD",
                "price": current,
                "rsi": None,
                "rebound_pct": 0.0,
                "reason": "NO_RSI",
                "symbol": symbol,
                "slot_id": slot_id,
            }

        if previous <= 0:

            return {
                "action": "HOLD",
                "price": current,
                "rsi": rsi,
                "rebound_pct": 0.0,
                "reason": "INVALID_PREVIOUS_PRICE",
                "symbol": symbol,
                "slot_id": slot_id,
            }

        rebound_pct = (
            (current - previous)
            / previous
        ) * 100.0

        buy_signal = (
            rsi <= Config.BUY_RSI_MAX
            and rebound_pct >= Config.BUY_REBOUND_PCT
        )

        if buy_signal:

            action = "BUY"

            reason = (
                f"RSI={rsi:.2f}; "
                f"REBOUND={rebound_pct:.3f}%"
            )

        else:

            action = "HOLD"

            reason = (
                f"RSI={rsi:.2f}; "
                f"REBOUND={rebound_pct:.3f}%"
            )

        return {
            "action": action,
            "price": current,
            "rsi": rsi,
            "rebound_pct": rebound_pct,
            "reason": reason,
            "symbol": symbol,
            "slot_id": slot_id,
        }

    # =========================================================
    # SIGNAL FROM MEXC
    # =========================================================

    def _entry_signal(
        self,
        symbol: str,
    ) -> tuple[
        bool,
        float,
        str,
    ]:

        candles = self.client.get_klines(
            symbol,
            Config.TIMEFRAME,
            Config.CANDLE_LIMIT,
        )

        if len(candles) < (
            Config.RSI_PERIOD + 3
        ):

            return (
                False,
                0.0,
                "INSUFFICIENT_DATA",
            )

        result = self.evaluate_entry_signal(
            symbol,
            candles,
        )

        return (
            result["action"] == "BUY",
            float(
                result["price"]
            ),
            str(
                result["reason"]
            ),
        )

    # =========================================================
    # EXIT
    # =========================================================

    def _manage_position(
        self,
        symbol: str,
        position: Position,
        current_price: float,
    ) -> tuple[
        bool,
        float,
    ]:

        if position.buy_price <= 0:
            return (
                False,
                0.0,
            )

        change_pct = (
            (
                current_price
                - position.buy_price
            )
            / position.buy_price
        ) * 100.0

        should_sell = False

        if (
            change_pct
            >= Config.TAKE_PROFIT_PCT
        ):

            reason = (
                f"TAKE_PROFIT "
                f"{change_pct:.2f}%"
            )

            should_sell = True

        elif (
            change_pct
            <= -Config.STOP_LOSS_PCT
        ):

            reason = (
                f"STOP_LOSS "
                f"{change_pct:.2f}%"
            )

            should_sell = True

        else:

            return (
                False,
                0.0,
            )

        base_asset = (
            symbol.upper()
            .replace("/", "")
        )

        base_asset = base_asset.removesuffix(
            "USDT"
        )

        quantity = (
            self.client.get_asset_balance(
                base_asset
            )
        )

        if quantity <= 0:
            quantity = position.quantity

        if quantity <= 0:

            print(
                f"[EXIT BLOCKED] {symbol}: "
                "no sellable quantity."
            )

            return (
                False,
                0.0,
            )

        print(
            f"[SELL SIGNAL] {symbol} "
            f"{reason}"
        )

        order = self.client.create_market_order(
            symbol,
            side="SELL",
            quantity=quantity,
        )

        if not order:
            return (
                False,
                0.0,
            )

        self.open_positions.pop(
            symbol,
            None,
        )

        # Refresh actual USDT balance after exit.
        try:
            balance = (
                self.client.get_usdt_balance()
            )

            return (
                True,
                balance,
            )

        except Exception:
            return (
                True,
                position.invested_usdt,
            )

    # =========================================================
    # ONE SYMBOL CYCLE
    # =========================================================

    def run_cycle_for_symbol(
        self,
        symbol: str,
        current_balance: float,
    ) -> float:

        symbol = (
            symbol.upper()
            .replace("/", "")
        )

        try:

            current_price = (
                self.client.get_ticker_price(
                    symbol
                )
            )

        except Exception as exc:

            print(
                f"[PRICE ERROR] "
                f"{symbol}: {exc}"
            )

            return current_balance

        if (
            current_price is None
            or current_price <= 0
        ):

            return current_balance

        # -----------------------------------------------------
        # EXISTING POSITION
        # -----------------------------------------------------

        existing = self.open_positions.get(
            symbol
        )

        if existing:

            sold, new_balance = (
                self._manage_position(
                    symbol,
                    existing,
                    current_price,
                )
            )

            if sold:
                return new_balance

            return current_balance

        # -----------------------------------------------------
        # POSITION LIMIT
        # -----------------------------------------------------

        if (
            len(self.open_positions)
            >= Config.MAX_OPEN_POSITIONS
        ):

            return current_balance

        # -----------------------------------------------------
        # BALANCE LIMIT
        # -----------------------------------------------------

        if (
            current_balance
            < Config.TRADE_AMOUNT_USDT
        ):

            return current_balance

        # -----------------------------------------------------
        # ENTRY
        # -----------------------------------------------------

        try:

            signal, signal_price, reason = (
                self._entry_signal(
                    symbol
                )
            )

        except Exception as exc:

            print(
                f"[SIGNAL ERROR] "
                f"{symbol}: {exc}"
            )

            return current_balance

        if not signal:
            return current_balance

        # -----------------------------------------------------
        # BUY
        # -----------------------------------------------------

        try:

            order = (
                self.client.create_market_order(
                    symbol,
                    side="BUY",
                    quote_order_qty=(
                        Config.TRADE_AMOUNT_USDT
                    ),
                )
            )

        except Exception as exc:

            print(
                f"[BUY ERROR] "
                f"{symbol}: {exc}"
            )

            return current_balance

        if not order:
            return current_balance

        # -----------------------------------------------------
        # ACTUAL EXECUTED QUANTITY
        # -----------------------------------------------------

        try:

            executed_qty = float(
                order.get(
                    "executedQty",
                    0,
                )
                or 0
            )

        except (
            TypeError,
            ValueError,
        ):

            executed_qty = 0.0

        if executed_qty <= 0:

            executed_qty = (
                Config.TRADE_AMOUNT_USDT
                / signal_price
            )

        # -----------------------------------------------------
        # POSITION
        # -----------------------------------------------------

        self.open_positions[symbol] = (
            Position(
                symbol=symbol,
                buy_price=signal_price,
                quantity=executed_qty,
                invested_usdt=(
                    Config.TRADE_AMOUNT_USDT
                ),
                opened_at=time.time(),
            )
        )

        new_balance = (
            current_balance
            - Config.TRADE_AMOUNT_USDT
        )

        print(
            f"[BUY] {symbol} "
            f"amount="
            f"{Config.TRADE_AMOUNT_USDT:.2f} USDT "
            f"price="
            f"{signal_price:.8f} "
            f"quantity="
            f"{executed_qty:.12f} "
            f"reason={reason}"
        )

        return new_balance
