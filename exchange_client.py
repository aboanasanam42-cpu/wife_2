import ccxt

from config import (
    MEXC_API_KEY,
    MEXC_API_SECRET,
    SYMBOL,
)


class MEXCClient:
    def __init__(self):
        if not MEXC_API_KEY:
            raise RuntimeError("MEXC_API_KEY is missing")

        if not MEXC_API_SECRET:
            raise RuntimeError("MEXC_API_SECRET is missing")

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
                f"{SYMBOL} is not available on MEXC Spot"
            )

        market = self.exchange.market(SYMBOL)

        if market.get("spot") is not True:
            raise RuntimeError(
                f"{SYMBOL} is not a Spot market"
            )

        if not self.exchange.has.get("createMarketOrder"):
            raise RuntimeError(
                "MEXC/CCXT does not report market orders as supported"
            )

    def ticker(self):
        return self.exchange.fetch_ticker(SYMBOL)

    def last_price(self) -> float:
        ticker = self.ticker()
        price = ticker.get("last")

        if price is None:
            raise RuntimeError("MEXC returned no last price")

        return float(price)

    def ohlcv(self, timeframe="1m", limit=100):
        return self.exchange.fetch_ohlcv(
            SYMBOL,
            timeframe=timeframe,
            limit=limit,
        )

    def balance(self):
        return self.exchange.fetch_balance()

    def free_balance(self, currency: str) -> float:
        balance = self.balance()

        free = balance.get("free", {}).get(currency)

        if free is None:
            free = (
                balance.get(currency, {})
                .get("free", 0.0)
            )

        return float(free or 0.0)

    def market(self):
        return self.exchange.market(SYMBOL)

    def normalize_amount(self, amount: float) -> float:
        return float(
            self.exchange.amount_to_precision(
                SYMBOL,
                amount,
            )
        )

    def normalize_price(self, price: float) -> float:
        return float(
            self.exchange.price_to_precision(
                SYMBOL,
                price,
            )
        )

    def create_market_buy(self, cost_usdt: float):
        if cost_usdt <= 0:
            raise ValueError(
                "Buy cost must be greater than zero"
            )

        market = self.market()

        limits = market.get("limits", {})
        cost_limits = limits.get("cost", {}) or {}

        min_cost = cost_limits.get("min")

        if min_cost is not None and cost_usdt < float(min_cost):
            raise ValueError(
                f"Buy cost {cost_usdt} USDT is below "
                f"MEXC minimum cost {min_cost} USDT"
            )

        quote_currency = market["quote"]

        available = self.free_balance(quote_currency)

        if available <= 0:
            raise RuntimeError(
                f"No available {quote_currency} balance"
            )

        if cost_usdt > available:
            raise RuntimeError(
                f"Insufficient {quote_currency}: "
                f"requested={cost_usdt:.8f}, "
                f"available={available:.8f}"
            )

        # MEXC supports market-buy-with-cost through CCXT.
        if self.exchange.has.get(
            "createMarketBuyOrderWithCost"
        ):
            return self.exchange.create_market_buy_order_with_cost(
                SYMBOL,
                cost_usdt,
            )

        # Safe fallback:
        # convert the USDT budget into base quantity.
        price = self.last_price()

        amount = cost_usdt / price

        amount = self.normalize_amount(amount)

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

    def create_market_sell(self, amount: float):
        if amount <= 0:
            raise ValueError(
                "Sell amount must be greater than zero"
            )

        base_currency = self.market()["base"]

        available = self.free_balance(base_currency)

        if available <= 0:
            raise RuntimeError(
                f"No available {base_currency} balance"
            )

        amount = min(float(amount), available)

        amount = self.normalize_amount(amount)

        if amount <= 0:
            raise RuntimeError(
                "Calculated market-sell amount is zero"
            )

        return self.exchange.create_market_sell_order(
            SYMBOL,
            amount,
        )

    def fetch_order(self, order_id: str):
        return self.exchange.fetch_order(
            order_id,
            SYMBOL,
        )
