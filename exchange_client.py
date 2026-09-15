import ccxt

from config import (
    MEXC_API_KEY,
    MEXC_API_SECRET,
    SYMBOL,
    TRADE_AMOUNT_USDT,
)


class MEXCClient:
    def __init__(self):
        if not MEXC_API_KEY:
            raise RuntimeError("MEXC_API_KEY is missing")

        if not MEXC_API_SECRET:
            raise RuntimeError("MEXC_API_SECRET is missing")

        self.exchange = ccxt.mexc({
            "apiKey": MEXC_API_KEY,
            "secret": MEXC_API_SECRET,
            "enableRateLimit": True,
            "options": {
                "defaultType": "spot",
            },
        })

        self.exchange.load_markets()

        if SYMBOL not in self.exchange.markets:
            raise RuntimeError(
                f"{SYMBOL} is not available on MEXC Spot"
            )

    def ticker(self):
        return self.exchange.fetch_ticker(SYMBOL)

    def ohlcv(self, timeframe="1m", limit=100):
        return self.exchange.fetch_ohlcv(
            SYMBOL,
            timeframe=timeframe,
            limit=limit,
        )

    def balance(self):
        return self.exchange.fetch_balance()

    def create_market_buy(self, amount_usdt: float):
        if amount_usdt <= 0:
            raise ValueError("Trade amount must be greater than zero")

        return self.exchange.create_market_buy_order(
            SYMBOL,
            amount_usdt,
        )

    def create_market_sell(self, amount):
        if amount <= 0:
            raise ValueError("Sell amount must be greater than zero")

        return self.exchange.create_market_sell_order(
            SYMBOL,
            amount,
        )

    def last_price(self) -> float:
        ticker = self.ticker()

        price = ticker.get("last")

        if price is None:
            raise RuntimeError("MEXC returned no last price")

        return float(price)
