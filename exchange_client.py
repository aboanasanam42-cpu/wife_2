from __future__ import annotations

import hashlib
import hmac
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any

import requests

from config import Config


class ExchangeClient:
    """
    MEXC Spot REST client.

    Responsibilities:
    - public market information
    - USDT balance
    - asset balance
    - ticker price
    - candles
    - signed account requests
    - market orders
    - ALL_USDT market discovery
    """

    def __init__(self) -> None:
        self.api_key = Config.API_KEY
        self.api_secret = Config.API_SECRET
        self.base_url = Config.BASE_URL

        self.session = requests.Session()

        self.session.headers.update(
            {
                "X-MEXC-APIKEY": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

        self.market_cache: dict[str, dict[str, Any]] = {}
        self.symbols_cache: list[str] = []
        self.last_symbols_fetch = 0.0

    # =========================================================
    # INTERNAL HTTP
    # =========================================================

    def _sign_params(
        self,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:

        payload = dict(params or {})

        payload["timestamp"] = int(
            time.time() * 1000
        )

        query_string = "&".join(
            f"{key}={payload[key]}"
            for key in sorted(payload)
        )

        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        payload["signature"] = signature

        return payload

    def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        timeout: int = 15,
    ) -> Any:

        response = self.session.get(
            f"{self.base_url}{path}",
            params=params,
            timeout=timeout,
        )

        response.raise_for_status()

        return response.json()

    def _signed_get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:

        signed = self._sign_params(params)

        return self._get(
            path,
            signed,
            timeout=15,
        )

    def _signed_post(
        self,
        path: str,
        params: dict[str, Any],
    ) -> Any:

        signed = self._sign_params(params)

        response = self.session.post(
            f"{self.base_url}{path}",
            params=signed,
            timeout=15,
        )

        try:
            data = response.json()
        except ValueError:
            data = {
                "msg": response.text,
            }

        if response.status_code >= 400:
            message = (
                data.get("msg")
                if isinstance(data, dict)
                else str(data)
            )

            raise RuntimeError(
                f"MEXC API error "
                f"{response.status_code}: {message}"
            )

        return data

    # =========================================================
    # MARKET INFORMATION
    # =========================================================

    def get_market_info(
        self,
    ) -> dict[str, dict[str, Any]]:

        now = time.time()

        cache_valid = (
            self.market_cache
            and (
                now - self.last_symbols_fetch
                < Config.SYMBOLS_REFRESH_MINUTES * 60
            )
        )

        if cache_valid:
            return self.market_cache

        data = self._get(
            "/api/v3/exchangeInfo"
        )

        markets = []

        if isinstance(data, dict):
            markets = data.get(
                "symbols",
                [],
            )

        result: dict[str, dict[str, Any]] = {}

        for market in markets:

            if not isinstance(market, dict):
                continue

            symbol = str(
                market.get(
                    "symbol",
                    "",
                )
            ).upper()

            quote_asset = str(
                market.get(
                    "quoteAsset",
                    "",
                )
            ).upper()

            status = str(
                market.get(
                    "status",
                    "",
                )
            ).upper()

            if not symbol:
                continue

            if quote_asset != "USDT":
                continue

            if status != "ENABLED":
                continue

            spot_allowed = market.get(
                "isSpotTradingAllowed",
                True,
            )

            if spot_allowed is False:
                continue

            result[symbol] = market

        self.market_cache = result
        self.symbols_cache = sorted(result.keys())
        self.last_symbols_fetch = now

        print(
            "[MEXC] ALL_USDT universe refreshed: "
            f"{len(self.symbols_cache)} symbols"
        )

        return result

    def get_active_usdt_symbols(self) -> list[str]:

        return sorted(
            self.get_market_info().keys()
        )

    def get_target_symbols(self) -> list[str]:

        raw = Config.SYMBOLS.strip().upper()

        if raw in {
            "",
            "ALL",
            "*",
            "ALL_USDT",
        }:

            return self.get_active_usdt_symbols()

        markets = self.get_market_info()

        requested: list[str] = []

        for item in raw.split(","):

            symbol = (
                item.strip()
                .upper()
                .replace("/", "")
            )

            if symbol:
                requested.append(symbol)

        valid = [
            symbol
            for symbol in requested
            if symbol in markets
        ]

        invalid = [
            symbol
            for symbol in requested
            if symbol not in markets
        ]

        if invalid:
            print(
                "[MEXC] Ignoring unavailable "
                f"symbols: {', '.join(invalid)}"
            )

        return valid

    # =========================================================
    # ACCOUNT
    # =========================================================

    def get_account(self) -> dict[str, Any]:

        data = self._signed_get(
            "/api/v3/account"
        )

        if not isinstance(data, dict):
            raise RuntimeError(
                "Invalid account response from MEXC."
            )

        return data

    def get_usdt_balance(self) -> float:

        data = self.get_account()

        for balance in data.get(
            "balances",
            [],
        ):

            if balance.get("asset") == "USDT":

                return float(
                    balance.get(
                        "free",
                        0,
                    )
                    or 0
                )

        return 0.0

    def get_asset_balance(
        self,
        asset: str,
    ) -> float:

        asset = asset.upper()

        data = self.get_account()

        for balance in data.get(
            "balances",
            [],
        ):

            if (
                str(
                    balance.get(
                        "asset",
                        "",
                    )
                ).upper()
                == asset
            ):

                return float(
                    balance.get(
                        "free",
                        0,
                    )
                    or 0
                )

        return 0.0

    # =========================================================
    # MARKET DATA
    # =========================================================

    def get_ticker_price(
        self,
        symbol: str,
    ) -> float | None:

        symbol = (
            symbol.upper()
            .replace("/", "")
        )

        data = self._get(
            "/api/v3/ticker/price",
            {
                "symbol": symbol,
            },
            timeout=10,
        )

        if isinstance(data, list):
            data = data[0] if data else {}

        if not isinstance(data, dict):
            return None

        try:
            price = float(
                data.get(
                    "price",
                    0,
                )
                or 0
            )
        except (TypeError, ValueError):
            return None

        return price if price > 0 else None

    def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int,
    ) -> list[list[Any]]:

        symbol = (
            symbol.upper()
            .replace("/", "")
        )

        data = self._get(
            "/api/v3/klines",
            {
                "symbol": symbol,
                "interval": interval,
                "limit": limit,
            },
            timeout=15,
        )

        return (
            data
            if isinstance(data, list)
            else []
        )

    # =========================================================
    # QUANTITY FORMATTING
    # =========================================================

    @staticmethod
    def _decimal_places(
        step: Any,
    ) -> int:

        try:
            value = Decimal(
                str(step)
            )
        except Exception:
            return 8

        if value <= 0:
            return 8

        return max(
            0,
            -value.as_tuple().exponent,
        )

    def format_quantity(
        self,
        symbol: str,
        quantity: float,
    ) -> str:

        symbol = (
            symbol.upper()
            .replace("/", "")
        )

        market = self.market_cache.get(
            symbol,
            {},
        )

        step = (
            market.get(
                "baseSizePrecision"
            )
            or market.get(
                "baseAssetPrecision"
            )
            or "0.00000001"
        )

        decimals = self._decimal_places(
            step
        )

        quantum = Decimal("1").scaleb(
            -decimals
        )

        value = Decimal(
            str(quantity)
        ).quantize(
            quantum,
            rounding=ROUND_DOWN,
        )

        return format(
            value,
            "f",
        )

    # =========================================================
    # ORDERS
    # =========================================================

    def create_market_order(
        self,
        symbol: str,
        side: str,
        quote_order_qty: float | None = None,
        quantity: float | None = None,
    ) -> dict[str, Any]:

        symbol = (
            symbol.upper()
            .replace("/", "")
        )

        side = side.upper()

        if side not in {
            "BUY",
            "SELL",
        }:
            raise ValueError(
                "side must be BUY or SELL"
            )

        if not Config.LIVE_TRADING:

            print(
                "[DRY-RUN] "
                f"{side} {symbol} "
                f"quote={quote_order_qty} "
                f"quantity={quantity}"
            )

            simulated_quantity = (
                quantity
                if quantity is not None
                else 0.0
            )

            return {
                "orderId": (
                    f"DRY-{int(time.time() * 1000)}"
                ),
                "symbol": symbol,
                "side": side,
                "status": "FILLED",
                "executedQty": str(
                    simulated_quantity
                ),
                "cummulativeQuoteQty": str(
                    quote_order_qty or 0
                ),
            }

        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
        }

        if quote_order_qty is not None:

            if quote_order_qty <= 0:
                raise ValueError(
                    "quote_order_qty must be greater than 0."
                )

            params["quoteOrderQty"] = (
                f"{quote_order_qty:.8f}"
                .rstrip("0")
                .rstrip(".")
            )

        elif quantity is not None:

            if quantity <= 0:
                raise ValueError(
                    "quantity must be greater than 0."
                )

            formatted = self.format_quantity(
                symbol,
                quantity,
            )

            if Decimal(formatted) <= 0:
                raise ValueError(
                    f"{symbol}: quantity becomes zero "
                    "after precision formatting."
                )

            params["quantity"] = formatted

        else:

            raise ValueError(
                "Either quote_order_qty or "
                "quantity must be supplied."
            )

        return self._signed_post(
            "/api/v3/order",
            params,
        )
