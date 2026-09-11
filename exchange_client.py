"""
MEXC Spot Exchange Client via ccxt.
Handles authentication, market metadata, OHLCV candle retrieval, balance checks,
and order execution with robust error handling for spot trading only.
"""

import time
import logging
from typing import Dict, Any, List, Optional
import ccxt
import pandas as pd

logger = logging.getLogger("mexc_trader.exchange")


class MexcSpotClient:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        simulation_mode: bool = False,
    ):
        self.simulation_mode = simulation_mode
        self.simulated_balances: Dict[str, float] = {"USDT": 1000.0, "BTC": 0.0}

        # Initialize authenticated CCXT MEXC client locked strictly to Spot API
        exchange_config = {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {
                "defaultType": "spot",  # Strictly force Spot endpoints (no Futures/Contracts)
                "adjustForTimeDifference": True,
            },
            "timeout": 15000,
        }

        self.exchange = ccxt.mexc(exchange_config)
        self.markets = None

    def initialize(self):
        """Loads market metadata and verifies connectivity."""
        try:
            logger.info("Connecting to MEXC Spot API & loading markets...")
            self.markets = self.exchange.load_markets()
            logger.info("Successfully connected to MEXC. Spot markets loaded: %d", len(self.markets))
        except (ccxt.NetworkError, ccxt.ExchangeError) as e:
            logger.error("Failed to initialize MEXC Spot client: %s", e)
            raise

    def get_spot_balance(self, currency: str = "USDT") -> float:
        """Fetches free available spot balance for a given asset."""
        if self.simulation_mode:
            return self.simulated_balances.get(currency.upper(), 0.0)

        try:
            balance_data = self.exchange.fetch_balance(params={"type": "spot"})
            free_balance = float(balance_data.get("free", {}).get(currency.upper(), 0.0))
            return free_balance
        except ccxt.NetworkError as e:
            logger.warning("Network glitch while fetching balance: %s", e)
            raise
        except ccxt.ExchangeError as e:
            logger.error("MEXC Exchange error while fetching balance: %s", e)
            raise

    def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """Fetches the latest spot ticker for the given symbol."""
        try:
            return self.exchange.fetch_ticker(symbol)
        except (ccxt.NetworkError, ccxt.ExchangeError) as e:
            logger.error("Error fetching ticker for %s: %s", symbol, e)
            raise

    def fetch_closed_ohlcv(
        self,
        symbol: str,
        timeframe: str = "15m",
        limit: int = 100,
    ) -> pd.DataFrame:
        """
        Fetches OHLCV candlestick data and returns a pandas DataFrame.
        Drops the currently forming (open) candle to ensure indicators only compute on closed candles.
        """
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit + 1)
            if not ohlcv or len(ohlcv) < 2:
                raise ValueError(f"Insufficient OHLCV data returned for {symbol}")

            # Columns: timestamp, open, high, low, close, volume
            df = pd.DataFrame(
                ohlcv,
                columns=["timestamp", "open", "high", "low", "close", "volume"],
            )
            df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = df[col].astype(float)

            # Drop the in-progress candle so strategy only trades on finalized, closed candles
            df_closed = df.iloc[:-1].copy().reset_index(drop=True)
            return df_closed

        except (ccxt.NetworkError, ccxt.ExchangeError) as e:
            logger.error("Error fetching OHLCV for %s: %s", symbol, e)
            raise

    def get_symbol_constraints(self, symbol: str) -> Dict[str, Any]:
        """Fetches minimum cost (USDT), minimum amount, and precision rules for the symbol."""
        if not self.markets or symbol not in self.markets:
            if self.exchange.markets:
                self.markets = self.exchange.markets
            else:
                self.markets = self.exchange.load_markets()

        market = self.markets.get(symbol, {})
        limits = market.get("limits", {})
        min_cost = limits.get("cost", {}).get("min", 5.0) or 5.0
        min_amount = limits.get("amount", {}).get("min", 0.0001) or 0.0001
        amount_precision = market.get("precision", {}).get("amount", 6)
        price_precision = market.get("precision", {}).get("price", 2)

        return {
            "min_cost": float(min_cost),
            "min_amount": float(min_amount),
            "amount_precision": int(amount_precision) if amount_precision is not None else 6,
            "price_precision": int(price_precision) if price_precision is not None else 2,
        }

    def execute_market_buy(
        self,
        symbol: str,
        usdt_amount: float,
        max_slippage_pct: float = 0.005,
    ) -> Dict[str, Any]:
        """
        Executes a Spot Market BUY order with slippage and volume constraints.
        """
        ticker = self.get_ticker(symbol)
        current_ask = float(ticker.get("ask") or ticker.get("last"))
        if current_ask <= 0:
            raise ValueError(f"Invalid market price for {symbol}")

        constraints = self.get_symbol_constraints(symbol)
        min_cost = constraints.get("min_cost", 1.0)
        # Note: MEXC spot API minimum order value is typically 1 to 5 USDT depending on symbol.
        if usdt_amount < min_cost:
            logger.warning(
                "Trade amount %.2f USDT is below reported minimum spot order cost of %.2f USDT for %s. Attempting order with exchange limits.",
                usdt_amount, min_cost, symbol
            )

        # In simulation mode, perform mock execution
        if self.simulation_mode:
            qty = usdt_amount / current_ask
            self.simulated_balances["USDT"] -= usdt_amount
            base = symbol.split("/")[0]
            self.simulated_balances[base] = self.simulated_balances.get(base, 0.0) + qty
            logger.info(
                "[SIMULATION BUY] Purchased %.6f %s at $%.4f (Cost: %.2f USDT)",
                qty,
                base,
                current_ask,
                usdt_amount,
            )
            return {
                "id": f"sim_buy_{int(time.time()*1000)}",
                "symbol": symbol,
                "side": "buy",
                "price": current_ask,
                "amount": qty,
                "cost": usdt_amount,
                "fee": usdt_amount * 0.001,  # Standard 0.1% spot fee
            }

        # Check USDT balance
        free_usdt = self.get_spot_balance("USDT")
        if free_usdt < usdt_amount:
            raise ValueError(
                f"Insufficient USDT balance: Available {free_usdt:.2f} USDT, Required {usdt_amount:.2f} USDT"
            )

        # MEXC Spot Market Buy natively expects cost/quoteOrderQty in USDT or base amount
        logger.info("Dispatching MEXC Spot Market BUY for %s: %s USDT", symbol, usdt_amount)
        try:
            # MEXC spot API allows passing quoteOrderQty in params
            order = self.exchange.create_order(
                symbol=symbol,
                type="market",
                side="buy",
                amount=None,
                price=None,
                params={"quoteOrderQty": usdt_amount},
            )
            # Normalize response fields
            exec_price = float(order.get("price") or current_ask)
            filled_amount = float(order.get("filled") or (usdt_amount / exec_price))
            cost = float(order.get("cost") or usdt_amount)

            # Check slippage
            if (exec_price - current_ask) / current_ask > max_slippage_pct:
                logger.warning("Order executed with higher slippage: expected $%.2f, filled $%.2f", current_ask, exec_price)

            return {
                "id": order.get("id"),
                "symbol": symbol,
                "side": "buy",
                "price": exec_price,
                "amount": filled_amount,
                "cost": cost,
                "fee": order.get("fee", {}).get("cost", cost * 0.001),
            }
        except (ccxt.NetworkError, ccxt.ExchangeError) as e:
            logger.error("MEXC Spot Market BUY execution failed: %s", e)
            raise

    def execute_market_sell(
        self,
        symbol: str,
        token_amount: float,
    ) -> Dict[str, Any]:
        """
        Executes a Spot Market SELL order for the specified token amount.
        """
        ticker = self.get_ticker(symbol)
        current_bid = float(ticker.get("bid") or ticker.get("last"))

        if self.simulation_mode:
            cost = token_amount * current_bid
            base = symbol.split("/")[0]
            self.simulated_balances[base] = max(0.0, self.simulated_balances.get(base, 0.0) - token_amount)
            self.simulated_balances["USDT"] = self.simulated_balances.get("USDT", 0.0) + cost
            logger.info(
                "[SIMULATION SELL] Sold %.6f %s at $%.4f (Received: %.2f USDT)",
                token_amount,
                base,
                current_bid,
                cost,
            )
            return {
                "id": f"sim_sell_{int(time.time()*1000)}",
                "symbol": symbol,
                "side": "sell",
                "price": current_bid,
                "amount": token_amount,
                "cost": cost,
                "fee": cost * 0.001,
            }

        constraints = self.get_symbol_constraints(symbol)
        precision = constraints.get("amount_precision", 6)
        
        # Use CCXT's exchange.amount_to_precision when available for exact lot sizing
        if hasattr(self.exchange, "amount_to_precision"):
            try:
                formatted_amount = float(self.exchange.amount_to_precision(symbol, token_amount))
            except Exception:
                formatted_amount = float(f"{token_amount:.{precision}f}")
        else:
            formatted_amount = float(f"{token_amount:.{precision}f}")

        logger.info("Dispatching MEXC Spot Market SELL for %s: %s tokens", symbol, formatted_amount)
        try:
            order = self.exchange.create_order(
                symbol=symbol,
                type="market",
                side="sell",
                amount=formatted_amount,
            )
            exec_price = float(order.get("price") or current_bid)
            filled_amount = float(order.get("filled") or formatted_amount)
            cost = float(order.get("cost") or (filled_amount * exec_price))

            return {
                "id": order.get("id"),
                "symbol": symbol,
                "side": "sell",
                "price": exec_price,
                "amount": filled_amount,
                "cost": cost,
                "fee": order.get("fee", {}).get("cost", cost * 0.001),
            }
        except (ccxt.NetworkError, ccxt.ExchangeError) as e:
            logger.error("MEXC Spot Market SELL execution failed: %s", e)
            raise
