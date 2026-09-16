from __future__ import annotations

import ccxt

from config import (
    MEXC_API_KEY,
    MEXC_API_SECRET,
    SYMBOL,
)


class MEXCClient:
    """
    Real MEXC Spot client.

    The client:
    - uses MEXC Spot only
    - loads and validates the requested market
    - validates balances
    - validates order limits
    - confirms submitted orders
    - never handles withdrawals
    """

    def __init__(self) -> None:

        if not MEXC_API_KEY:
            raise RuntimeError(
                "MEXC_API_KEY is missing"
            )

        if not MEXC_API_SECRET:
            raise RuntimeError(
                "MEXC_API_SECRET is missing"
            )

        self.exchange = ccxt.mexc(
            {
                "apiKey": MEXC_API_KEY,
                "secret": MEXC_API_SECRET,
                "enableRateLimit": True,
                "options": {
                    "defaultType": "spot",
                },
            }
        )

        self.exchange.load_markets()

        if SYMBOL not in self.exchange.markets:
            raise RuntimeError(
                f"{SYMBOL} is not available on MEXC"
            )

        market = self.exchange.market(SYMBOL)

        if market.get("spot") is not True:
            raise RuntimeError(
                f"{SYMBOL} is not a Spot market"
            )

        if market.get("active") is False:
            raise RuntimeError(
                f"{SYMBOL} market is inactive on MEXC"
            )

        if not self.exchange.has.get(
            "createMarketOrder"
        ):
            raise RuntimeError(
                "MEXC/CCXT does not report market orders "
                "as supported"
            )

    # ========================================================
    # MARKET
    # ========================================================

    def market(self):
        return self.exchange.market(
            SYMBOL
        )

    def ticker(self):
        return self.exchange.fetch_ticker(
            SYMBOL
        )

    def last_price(self) -> float:

        ticker = self.ticker()

        price = ticker.get("last")

        if price is None:
            price = ticker.get("close")

        if price is None:
            raise RuntimeError(
                "MEXC returned no usable market price"
            )

        price = float(price)

        if price <= 0:
            raise RuntimeError(
                f"Invalid MEXC price: {price}"
            )

        return price

    def ohlcv(
        self,
        timeframe: str = "1m",
        limit: int = 100,
    ):
        return self.exchange.fetch_ohlcv(
            SYMBOL,
            timeframe=timeframe,
            limit=limit,
        )

    # ========================================================
    # BALANCE
    # ========================================================

    def balance(self):
        return self.exchange.fetch_balance()

    def free_balance(
        self,
        currency: str,
    ) -> float:

        balance = self.balance()

        free = (
            balance
            .get("free", {})
            .get(currency)
        )

        if free is None:
            free = (
                balance
                .get(currency, {})
                .get("free", 0.0)
            )

        return float(
            free or 0.0
        )

    # ========================================================
    # PRECISION
    # ========================================================

    def normalize_amount(
        self,
        amount: float,
    ) -> float:

        return float(
            self.exchange.amount_to_precision(
                SYMBOL,
                amount,
            )
        )

    def normalize_price(
        self,
        price: float,
    ) -> float:

        return float(
            self.exchange.price_to_precision(
                SYMBOL,
                price,
            )
        )

    # ========================================================
    # LIMIT HELPERS
    # ========================================================

    def _limits(self):
        return (
            self.market()
            .get("limits", {})
            or {}
        )

    def minimum_amount(self) -> float:
        value = (
            self._limits()
            .get("amount", {})
            .get("min")
        )

        return float(
            value or 0.0
        )

    def maximum_amount(self) -> float:
        value = (
            self._limits()
            .get("amount", {})
            .get("max")
        )

        return float(
            value or 0.0
        )

    def minimum_cost(self) -> float:
        value = (
            self._limits()
            .get("cost", {})
            .get("min")
        )

        return float(
            value or 0.0
        )

    def maximum_cost(self) -> float:
        value = (
            self._limits()
            .get("cost", {})
            .get("max")
        )

        return float(
            value or 0.0
        )

    # ========================================================
    # REAL MARKET BUY
    # ========================================================

    def create_market_buy(
        self,
        cost_usdt: float,
    ):

        cost_usdt = float(
            cost_usdt
        )

        if cost_usdt <= 0:
            raise ValueError(
                "Buy cost must be greater than zero"
            )

        market = self.market()

        quote_currency = market["quote"]

        available = self.free_balance(
            quote_currency
        )

        if available <= 0:
            raise RuntimeError(
                f"No available "
                f"{quote_currency} balance"
            )

        if cost_usdt > available:
            raise RuntimeError(
                f"Insufficient {quote_currency}: "
                f"requested={cost_usdt:.8f}, "
                f"available={available:.8f}"
            )

        min_cost = self.minimum_cost()
        max_cost = self.maximum_cost()

        if (
            min_cost > 0
            and cost_usdt < min_cost
        ):
            raise ValueError(
                f"Buy cost {cost_usdt:.8f} "
                f"is below MEXC minimum "
                f"cost {min_cost:.8f}"
            )

        if (
            max_cost > 0
            and cost_usdt > max_cost
        ):
            raise ValueError(
                f"Buy cost {cost_usdt:.8f} "
                f"exceeds MEXC maximum "
                f"cost {max_cost:.8f}"
            )

        price = self.last_price()

        estimated_amount = (
            cost_usdt / price
        )

        min_amount = self.minimum_amount()
        max_amount = self.maximum_amount()

        if (
            min_amount > 0
            and estimated_amount < min_amount
        ):
            raise ValueError(
                f"Estimated amount "
                f"{estimated_amount:.12f} "
                f"is below MEXC minimum "
                f"amount {min_amount:.12f}"
            )

        if (
            max_amount > 0
            and estimated_amount > max_amount
        ):
            raise ValueError(
                f"Estimated amount "
                f"{estimated_amount:.12f} "
                f"exceeds MEXC maximum "
                f"amount {max_amount:.12f}"
            )

        # Preferred CCXT method when supported.
        if self.exchange.has.get(
            "createMarketBuyOrderWithCost"
        ):
            return (
                self.exchange
                .create_market_buy_order_with_cost(
                    SYMBOL,
                    cost_usdt,
                )
            )

        # Fallback to quantity-based market order.
        amount = self.normalize_amount(
            estimated_amount
        )

        if amount <= 0:
            raise RuntimeError(
                "Calculated market-buy amount is zero"
            )

        return self.exchange.create_order(
            SYMBOL,
            "market",
            "buy",
            amount,
        )

    # ========================================================
    # REAL MARKET SELL
    # ========================================================

    def create_market_sell(
        self,
        amount: float,
    ):

        amount = float(
            amount
        )

        if amount <= 0:
            raise ValueError(
                "Sell amount must be greater than zero"
            )

        market = self.market()

        base_currency = market["base"]

        available = self.free_balance(
            base_currency
        )

        if available <= 0:
            raise RuntimeError(
                f"No available "
                f"{base_currency} balance"
            )

        amount = min(
            amount,
            available,
        )

        amount = self.normalize_amount(
            amount
        )

        if amount <= 0:
            raise RuntimeError(
                "Normalized sell amount is zero"
            )

        min_amount = self.minimum_amount()

        if (
            min_amount > 0
            and amount < min_amount
        ):
            raise ValueError(
                f"Sell amount "
                f"{amount:.12f} is below "
                f"MEXC minimum amount "
                f"{min_amount:.12f}"
            )

        max_amount = self.maximum_amount()

        if (
            max_amount > 0
            and amount > max_amount
        ):
            amount = self.normalize_amount(
                max_amount
            )

        price = self.last_price()

        min_cost = self.minimum_cost()

        sell_value = (
            amount * price
        )

        if (
            min_cost > 0
            and sell_value < min_cost
        ):
            raise ValueError(
                f"Sell value "
                f"{sell_value:.8f} USDT is below "
                f"MEXC minimum cost "
                f"{min_cost:.8f} USDT"
            )

        return (
            self.exchange
            .create_market_sell_order(
                SYMBOL,
                amount,
            )
        )

    # ========================================================
    # ORDER CONFIRMATION
    # ========================================================

    def fetch_order(
        self,
        order_id: str,
    ):

        return self.exchange.fetch_order(
            order_id,
            SYMBOL,
        )

    def confirm_order(
        self,
        order: dict,
    ):

        order_id = order.get(
            "id"
        )

        if order_id:

            try:

                confirmed = (
                    self.fetch_order(
                        order_id
                    )
                )

                if confirmed:
                    order = confirmed

            except Exception as exc:

                raise RuntimeError(
                    "Could not confirm "
                    f"MEXC order "
                    f"{order_id}: {exc}"
                ) from exc

        status = str(
            order.get("status")
            or ""
        ).lower()

        filled = float(
            order.get("filled")
            or 0.0
        )

        if status in {
            "canceled",
            "cancelled",
            "rejected",
            "expired",
        }:

            raise RuntimeError(
                f"MEXC order "
                f"{order_id or 'unknown'} "
                f"was not filled: "
                f"{status}"
            )

        if (
            status
            and status not in {
                "closed",
                "filled",
            }
            and filled <= 0
        ):

            raise RuntimeError(
                f"MEXC order "
                f"{order_id or 'unknown'} "
                f"is not complete: "
                f"{status}"
            )

        return order
