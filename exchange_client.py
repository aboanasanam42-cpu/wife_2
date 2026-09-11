import logging
import time
from typing import Any, Dict, List

import ccxt
import pandas as pd

logger = logging.getLogger("mexc_trader.exchange")


class MexcSpotClient:
    def __init__(self, api_key: str, api_secret: str, simulation_mode: bool = False):
        self.simulation_mode = simulation_mode
        self.simulated_balances: Dict[str, float] = {"USDT": 100.0}
        self.exchange = ccxt.mexc({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "timeout": 20000,
            "options": {"defaultType": "spot", "adjustForTimeDifference": True},
        })
        self.markets: Dict[str, Any] = {}

    def initialize(self) -> None:
        if self.simulation_mode:
            logger.info("Simulation mode enabled; no live MEXC orders will be sent.")
            return
        self.markets = self.exchange.load_markets()
        logger.info("MEXC Spot connected; %d markets loaded.", len(self.markets))

    def select_top_usdt_symbols(self, count: int = 4) -> List[str]:
        """Select active USDT spot pairs automatically by current 24h quote volume.

        Only markets explicitly marked active/spot with USDT as quote are considered.
        Stablecoin-vs-stablecoin pairs and leveraged/contract-like symbols are excluded.
        """
        if self.simulation_mode:
            # Simulation remains deterministic and does not need live market discovery.
            return ["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT"][:max(1, count)]
        if not self.markets:
            self.markets = self.exchange.load_markets()

        candidates = []
        for symbol, market in self.markets.items():
            if market.get("quote") != "USDT":
                continue
            if market.get("spot") is False or market.get("active") is False:
                continue
            base = str(market.get("base") or "").upper()
            if base in {"USDT", "USDC", "FDUSD", "DAI", "TUSD", "USDE", "USD1"}:
                continue
            if not market.get("symbol", "").endswith("/USDT"):
                continue
            candidates.append(symbol)

        if not candidates:
            raise RuntimeError("No active MEXC Spot USDT markets were found for automatic selection.")

        tickers = self.exchange.fetch_tickers(candidates)
        ranked = []
        for symbol in candidates:
            ticker = tickers.get(symbol) or {}
            quote_volume = float(ticker.get("quoteVolume") or 0.0)
            last = float(ticker.get("last") or 0.0)
            if quote_volume > 0 and last > 0:
                ranked.append((quote_volume, symbol))
        ranked.sort(reverse=True)
        selected = [symbol for _, symbol in ranked[:max(1, count)]]
        if not selected:
            raise RuntimeError("MEXC returned no usable USDT volume data for automatic pair selection.")
        logger.info("Automatic pair selection: %s", ", ".join(selected))
        return selected

    def get_spot_balance(self, currency: str = "USDT") -> float:
        currency = currency.upper()
        if self.simulation_mode:
            return float(self.simulated_balances.get(currency, 0.0))
        balance = self.exchange.fetch_balance({"type": "spot"})
        return float(balance.get("free", {}).get(currency, 0.0) or 0.0)

    def get_ticker(self, symbol: str) -> Dict[str, Any]:
        return self.exchange.fetch_ticker(symbol)

    def fetch_closed_ohlcv(self, symbol: str, timeframe: str = "1m", limit: int = 100) -> pd.DataFrame:
        ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit + 1)
        if len(ohlcv) < limit:
            raise ValueError(f"Insufficient OHLCV data for {symbol}: {len(ohlcv)} candles")
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df.iloc[:-1].dropna().reset_index(drop=True)

    def get_symbol_constraints(self, symbol: str) -> Dict[str, float]:
        if not self.markets:
            self.markets = self.exchange.load_markets()
        market = self.markets[symbol]
        limits = market.get("limits", {})
        return {
            "min_cost": float(limits.get("cost", {}).get("min") or 0.0),
            "min_amount": float(limits.get("amount", {}).get("min") or 0.0),
        }

    def _amount(self, symbol: str, amount: float) -> float:
        return float(self.exchange.amount_to_precision(symbol, amount))

    def execute_market_buy(self, symbol: str, usdt_amount: float, max_slippage_pct: float = 0.005) -> Dict[str, Any]:
        ticker = self.exchange.fetch_ticker(symbol)
        ask = float(ticker.get("ask") or ticker.get("last") or 0.0)
        if ask <= 0:
            raise ValueError(f"Invalid ask price for {symbol}")
        constraints = self.get_symbol_constraints(symbol) if not self.simulation_mode else {"min_cost": 0.0, "min_amount": 0.0}
        if constraints["min_cost"] > usdt_amount:
            raise ValueError(f"MEXC minimum cost for {symbol} is {constraints['min_cost']:.8f} USDT; requested exact slot is {usdt_amount:.2f} USDT")
        if self.get_spot_balance("USDT") < usdt_amount:
            raise ValueError("Insufficient free USDT after the cash-reserve check.")

        if self.simulation_mode:
            qty = usdt_amount / ask
            self.simulated_balances["USDT"] -= usdt_amount
            base = symbol.split("/")[0]
            self.simulated_balances[base] = self.simulated_balances.get(base, 0.0) + qty
            return {"id": f"sim-buy-{int(time.time()*1000)}", "symbol": symbol, "price": ask, "amount": qty, "cost": usdt_amount, "fee": 0.0}

        order = self.exchange.create_order(symbol, "market", "buy", None, None, {"quoteOrderQty": usdt_amount})
        filled = float(order.get("filled") or 0.0)
        cost = float(order.get("cost") or 0.0)
        price = float(order.get("average") or order.get("price") or (cost / filled if filled else ask))
        if filled <= 0:
            raise RuntimeError(f"MEXC returned a BUY order without a filled quantity: {order.get('id')}")
        if ask > 0 and (price - ask) / ask > max_slippage_pct:
            logger.warning("BUY slippage %.3f%% exceeded configured %.3f%% on %s", ((price-ask)/ask)*100, max_slippage_pct*100, symbol)
        fee = float((order.get("fee") or {}).get("cost") or 0.0)
        return {"id": order.get("id"), "symbol": symbol, "price": price, "amount": filled, "cost": cost or usdt_amount, "fee": fee}

    def execute_market_sell(self, symbol: str, token_amount: float) -> Dict[str, Any]:
        ticker = self.exchange.fetch_ticker(symbol)
        bid = float(ticker.get("bid") or ticker.get("last") or 0.0)
        if bid <= 0:
            raise ValueError(f"Invalid bid price for {symbol}")
        amount = token_amount if self.simulation_mode else self._amount(symbol, token_amount)
        if amount <= 0:
            raise ValueError("Sell quantity rounded to zero by exchange precision.")
        if not self.simulation_mode:
            constraints = self.get_symbol_constraints(symbol)
            if constraints["min_amount"] and amount < constraints["min_amount"]:
                raise ValueError(f"Sell quantity {amount} is below MEXC minimum {constraints['min_amount']} for {symbol}")
            order = self.exchange.create_order(symbol, "market", "sell", amount)
            filled = float(order.get("filled") or amount)
            cost = float(order.get("cost") or filled * bid)
            price = float(order.get("average") or order.get("price") or (cost / filled if filled else bid))
            fee = float((order.get("fee") or {}).get("cost") or 0.0)
            return {"id": order.get("id"), "symbol": symbol, "price": price, "amount": filled, "cost": cost, "fee": fee}
        base = symbol.split("/")[0]
        held = self.simulated_balances.get(base, 0.0)
        sold = min(amount, held)
        cost = sold * bid
        self.simulated_balances[base] = held - sold
        self.simulated_balances["USDT"] = self.simulated_balances.get("USDT", 0.0) + cost
        return {"id": f"sim-sell-{int(time.time()*1000)}", "symbol": symbol, "price": bid, "amount": sold, "cost": cost, "fee": 0.0}
