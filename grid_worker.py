import fcntl
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ccxt
from dotenv import load_dotenv

load_dotenv()

LOG = logging.getLogger("mexc_grid")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


class SafetyHalt(RuntimeError):
    """Raised when exchange state cannot safely be inferred."""


@dataclass(frozen=True)
class Settings:
    symbol: str = "BTC/USDT:USDT"
    lower_price: float = 84000.0
    upper_price: float = 84700.0
    grid_count: int = 2
    leverage: int = 10
    margin_usdt: float = 5.0
    target_notional_usdt: float = 50.0
    poll_seconds: int = 5
    retry_seconds: int = 15
    live_trading: bool = False
    api_key: str = ""
    api_secret: str = ""
    state_file: str = ".wife_2_state.json"

    @classmethod
    def from_env(cls) -> "Settings":
        settings = cls(
            symbol=os.getenv("SYMBOL", cls.symbol),
            lower_price=float(os.getenv("LOWER_PRICE", str(cls.lower_price))),
            upper_price=float(os.getenv("UPPER_PRICE", str(cls.upper_price))),
            grid_count=int(os.getenv("GRID_COUNT", str(cls.grid_count))),
            leverage=int(os.getenv("LEVERAGE", str(cls.leverage))),
            margin_usdt=float(os.getenv("MARGIN_USDT", str(cls.margin_usdt))),
            target_notional_usdt=float(os.getenv("TARGET_NOTIONAL_USDT", str(cls.target_notional_usdt))),
            poll_seconds=int(os.getenv("POLL_SECONDS", str(cls.poll_seconds))),
            retry_seconds=int(os.getenv("RETRY_SECONDS", str(cls.retry_seconds))),
            live_trading=os.getenv("LIVE_TRADING", "false").strip().lower() == "true",
            api_key=os.getenv("MEXC_API_KEY", "").strip(),
            api_secret=os.getenv("MEXC_API_SECRET", "").strip(),
            state_file=os.getenv("BOT_STATE_FILE", cls.state_file),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.symbol != "BTC/USDT:USDT":
            raise ValueError("This worker is configured only for BTC/USDT:USDT")
        if self.lower_price <= 0 or self.lower_price >= self.upper_price:
            raise ValueError("Price range must be positive and lower < upper")
        if self.grid_count != 2:
            raise ValueError("This single-position worker requires GRID_COUNT=2")
        if self.leverage != 10:
            raise ValueError("This worker is configured for LEVERAGE=10")
        if self.margin_usdt <= 0 or self.target_notional_usdt <= 0:
            raise ValueError("Margin and target notional must be positive")
        if self.target_notional_usdt > self.margin_usdt * self.leverage:
            raise ValueError("Target notional exceeds margin multiplied by leverage")
        if self.poll_seconds < 1 or self.retry_seconds < 1:
            raise ValueError("Polling intervals must be positive")
        if self.live_trading and (not self.api_key or not self.api_secret):
            raise ValueError("LIVE_TRADING=true requires MEXC_API_KEY and MEXC_API_SECRET")


class StateStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = self.path.with_suffix(self.path.suffix + ".lock").open("a+")
        try:
            fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.lock.close()
            raise SafetyHalt("Another worker already holds the state lock") from exc
        self.data: dict[str, Any] = self.load()

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"phase": "idle"}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SafetyHalt(f"Cannot read worker state at {self.path}") from exc
        allowed = {"idle", "entry_submitting", "entry_open", "tp_submitting", "tp_open"}
        if not isinstance(data, dict) or data.get("phase") not in allowed:
            raise SafetyHalt("Worker state is invalid; refusing to place orders")
        return data

    def save(self) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as state_file:
            state_file.write(json.dumps(self.data, sort_keys=True))
            state_file.flush()
            os.fsync(state_file.fileno())
        os.replace(temporary, self.path)
        directory_fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def start_cycle(self) -> None:
        self.data = {"phase": "entry_submitting", "entry_client_id": "w2e" + uuid.uuid4().hex[:20]}
        self.save()

    def clear(self) -> None:
        self.data = {"phase": "idle"}
        self.save()


class MEXCGridWorker:
    def __init__(self, settings: Settings, exchange: Any, state: StateStore | None = None):
        self.settings = settings
        self.exchange = exchange
        self.state = state
        self.market: dict[str, Any] | None = None

    def load_market(self) -> dict[str, Any]:
        self.exchange.load_markets()
        if self.settings.symbol not in self.exchange.markets:
            raise SafetyHalt(f"MEXC does not list {self.settings.symbol}")
        market = self.exchange.market(self.settings.symbol)
        if not market.get("swap") or market.get("linear") is not True:
            raise SafetyHalt("Market is not a linear USDT-M perpetual contract")
        if market.get("settle") != "USDT" or not market.get("contract"):
            raise SafetyHalt("Market contract/settlement metadata is not USDT-M")
        if not market.get("contractSize") or market.get("precision", {}).get("amount") is None:
            raise SafetyHalt("MEXC did not provide contract size and amount precision")
        if market.get("precision", {}).get("price") is None:
            raise SafetyHalt("MEXC did not provide price precision")
        self.market = market
        LOG.info(
            "Market validated | symbol=%s | contractSize=%s | amountPrecision=%s | pricePrecision=%s | minAmount=%s",
            self.settings.symbol, market["contractSize"], market["precision"]["amount"],
            market["precision"]["price"], (market.get("limits", {}).get("amount") or {}).get("min"),
        )
        return market

    def grid_levels(self) -> list[float]:
        span = self.settings.upper_price - self.settings.lower_price
        return [self.settings.lower_price + span * index / (self.settings.grid_count - 1)
                for index in range(self.settings.grid_count)]

    def precise_price(self, price: float) -> float:
        return float(self.exchange.price_to_precision(self.settings.symbol, price))

    def amount_for_price(self, price: float) -> float:
        if self.market is None:
            raise RuntimeError("Market must be loaded before calculating order quantity")
        contract_size = float(self.market["contractSize"])
        contracts = float(self.exchange.amount_to_precision(
            self.settings.symbol, self.settings.target_notional_usdt / (price * contract_size)
        ))
        limits = self.market.get("limits", {})
        min_amount = (limits.get("amount") or {}).get("min")
        if contracts <= 0 or (min_amount is not None and contracts < float(min_amount)):
            raise SafetyHalt(f"Calculated contracts {contracts} are below MEXC minimum {min_amount}")
        notional = contracts * contract_size * price
        min_cost = (limits.get("cost") or {}).get("min")
        if min_cost is not None and notional < float(min_cost):
            raise SafetyHalt(f"Order notional {notional} is below MEXC minimum {min_cost}")
        if notional > self.settings.target_notional_usdt * 1.000001:
            raise SafetyHalt("Contract precision would exceed the target notional")
        return contracts

    @staticmethod
    def client_id(order: dict[str, Any]) -> str | None:
        info = order.get("info") or {}
        return (order.get("clientOrderId") or order.get("clientOrderID")
                or info.get("clientOrderId") or info.get("externalOid"))

    def is_owned(self, order: dict[str, Any]) -> bool:
        known_ids = {
            order_id for order_id in
            (self.state.data.get("entry_order_id"), self.state.data.get("tp_order_id"))
            if order_id is not None
        }
        client_id = self.client_id(order)
        return order.get("id") in known_ids or bool(client_id and client_id.startswith(("w2e", "w2t")))

    def positions(self) -> list[dict[str, Any]]:
        positions = self.exchange.fetch_positions([self.settings.symbol])
        active = []
        for position in positions:
            contracts = position.get("contracts")
            if contracts is None:
                contracts = (position.get("info") or {}).get("vol")
            if contracts is not None and abs(float(contracts)) > 0:
                active.append(position)
        return active

    def verify_orders_are_owned(self, orders: list[dict[str, Any]]) -> None:
        foreign = [order for order in orders if not self.is_owned(order)]
        if foreign:
            raise SafetyHalt(f"Found {len(foreign)} unmanaged open order(s) on {self.settings.symbol}; no orders changed")
        entries = [order for order in orders if (self.client_id(order) or "").startswith("w2e")]
        take_profits = [order for order in orders if (self.client_id(order) or "").startswith("w2t")]
        if len(entries) > 1 or len(take_profits) > 1:
            raise SafetyHalt("Duplicate bot orders detected; refusing to change exchange state")

    def find_order(self, orders: list[dict[str, Any]], client_id: str | None) -> dict[str, Any] | None:
        if not client_id:
            return None
        return next((order for order in orders if self.client_id(order) == client_id), None)

    def place_entry(self) -> None:
        ticker = self.exchange.fetch_ticker(self.settings.symbol)
        last_price = ticker.get("last")
        if last_price is None or not self.settings.lower_price <= float(last_price) <= self.settings.upper_price:
            LOG.info("No entry: last price %s is outside configured range", last_price)
            return
        price = self.precise_price(self.grid_levels()[0])
        amount = self.amount_for_price(price)
        notional = amount * float(self.market["contractSize"]) * price
        LOG.info("Entry plan | side=buy | price=%s | contracts=%s | notional=%s USDT | estimatedMarginAt10x=%s USDT | targetNotional=%s USDT",
             price, amount, notional, notional / self.settings.leverage,
             self.settings.target_notional_usdt)
        if not self.settings.live_trading:
            LOG.info("DRY RUN | entry order not submitted")
            return
        balance = self.exchange.fetch_balance()
        free_usdt = (balance.get("free") or {}).get("USDT")
        if free_usdt is None:
            free_usdt = (balance.get("USDT") or {}).get("free")
        if free_usdt is None or float(free_usdt) < self.settings.margin_usdt:
            raise SafetyHalt(
                f"Available Futures USDT ({free_usdt}) is below configured margin {self.settings.margin_usdt}"
            )
        self.exchange.set_margin_mode(
            "isolated",
            self.settings.symbol,
            {"leverage": self.settings.leverage, "direction": "long"},
        )
        self.state.start_cycle()
        client_id = self.state.data["entry_client_id"]
        order = self.exchange.create_order(
            self.settings.symbol, "limit", "buy", amount, price,
            {"externalOid": client_id, "positionMode": 2, "marginMode": "isolated",
             "leverage": self.settings.leverage},
        )
        self.state.data.update(phase="entry_open", entry_order_id=order.get("id"))
        self.state.save()
        LOG.info("Entry submitted | order=%s | clientOrderId=%s", order.get("id"), client_id)

    def place_take_profit(self, position: dict[str, Any]) -> None:
        if position.get("side") not in ("long", "buy"):
            raise SafetyHalt(f"Unexpected position side {position.get('side')}; refusing to place an exit")
        contracts = abs(float(position.get("contracts") or (position.get("info") or {}).get("vol") or 0))
        entry_price = position.get("entryPrice")
        if contracts <= 0 or not entry_price:
            raise SafetyHalt("Position quantity or entry price is unavailable")
        target = self.precise_price(self.settings.upper_price)
        if target <= float(entry_price):
            raise SafetyHalt("Take-profit level is not above the long position entry price")
        client_id = "w2t" + uuid.uuid4().hex[:20]
        self.state.data.update(phase="tp_submitting", tp_client_id=client_id,
                               position_amount=contracts, entry_price=float(entry_price))
        self.state.save()
        order = self.exchange.create_order(
            self.settings.symbol, "limit", "sell", contracts, target,
            {"reduceOnly": True, "externalOid": client_id, "positionMode": 2,
             "marginMode": "isolated", "leverage": self.settings.leverage},
        )
        self.state.data.update(phase="tp_open", tp_order_id=order.get("id"))
        self.state.save()
        LOG.info("Take-profit submitted | order=%s | price=%s | contracts=%s", order.get("id"), target, contracts)

    def order_status(self, order_id: str) -> dict[str, Any]:
        if not order_id:
            raise SafetyHalt("Order identifier missing from saved state")
        return self.exchange.fetch_order(order_id, self.settings.symbol)

    def reconcile_live(self) -> None:
        orders = self.exchange.fetch_open_orders(self.settings.symbol)
        self.verify_orders_are_owned(orders)
        active_positions = self.positions()
        if len(active_positions) > 1:
            raise SafetyHalt("Multiple active positions found; refusing to change account exposure")
        position = active_positions[0] if active_positions else None
        phase = self.state.data["phase"]
        entry = self.find_order(orders, self.state.data.get("entry_client_id"))
        take_profit = self.find_order(orders, self.state.data.get("tp_client_id"))

        if position and phase == "entry_submitting":
            self.state.data.update(phase="entry_open", entry_order_id=None)
            self.state.save()
            phase = "entry_open"
            LOG.warning("Recovered a position after entry submission; reconciling its remaining entry quantity")

        if phase == "idle":
            tagged_entry = next((o for o in orders if (self.client_id(o) or "").startswith("w2e")), None)
            tagged_tp = next((o for o in orders if (self.client_id(o) or "").startswith("w2t")), None)
            if position and tagged_entry:
                self.state.data.update(phase="entry_open", entry_client_id=self.client_id(tagged_entry),
                                       entry_order_id=tagged_entry.get("id"))
                if tagged_tp:
                    self.state.data.update(tp_client_id=self.client_id(tagged_tp),
                                           tp_order_id=tagged_tp.get("id"))
                self.state.save()
                phase, entry, take_profit = "entry_open", tagged_entry, tagged_tp
            elif position and tagged_tp:
                self.state.data.update(phase="tp_open", tp_client_id=self.client_id(tagged_tp),
                                       tp_order_id=tagged_tp.get("id"))
                self.state.save()
                phase, take_profit = "tp_open", tagged_tp
            elif not position and tagged_entry:
                self.state.data.update(phase="entry_open", entry_client_id=self.client_id(tagged_entry),
                                       entry_order_id=tagged_entry.get("id"))
                self.state.save()
                phase, entry = "entry_open", tagged_entry
            elif position or orders:
                raise SafetyHalt("Exchange has exposure/order but no recoverable bot state")

        if position:
            if phase not in ("entry_open", "tp_submitting", "tp_open"):
                raise SafetyHalt("Position is not associated with a known bot entry")
            if entry:
                self.exchange.cancel_order(entry["id"], self.settings.symbol)
                LOG.warning("Cancelled remaining entry quantity after partial fill | order=%s", entry["id"])
                orders = self.exchange.fetch_open_orders(self.settings.symbol)
                self.verify_orders_are_owned(orders)
                if self.find_order(orders, self.state.data.get("entry_client_id")):
                    raise SafetyHalt("Entry order remains open after cancellation request")
                take_profit = self.find_order(orders, self.state.data.get("tp_client_id"))
            if take_profit:
                self.state.data.update(phase="tp_open", tp_order_id=take_profit.get("id"))
                self.state.save()
                LOG.info("Position protected by existing take-profit | order=%s", take_profit.get("id"))
                return
            if phase == "tp_submitting":
                raise SafetyHalt("Take-profit submission outcome is ambiguous; manual reconciliation required")
            self.place_take_profit(position)
            return

        if phase == "entry_submitting":
            if entry:
                self.state.data.update(phase="entry_open", entry_order_id=entry.get("id"))
                self.state.save()
            else:
                raise SafetyHalt("Entry submission outcome is ambiguous; refusing a duplicate order")
        elif phase == "entry_open" and not entry:
            status = self.order_status(self.state.data.get("entry_order_id"))
            if status.get("status") in ("canceled", "expired", "rejected"):
                LOG.warning("Entry did not execute | status=%s", status.get("status"))
                self.state.clear()
            else:
                raise SafetyHalt("Entry order disappeared without a matching position")
        elif phase == "tp_submitting":
            if take_profit:
                self.state.data.update(phase="tp_open", tp_order_id=take_profit.get("id"))
                self.state.save()
            else:
                raise SafetyHalt("Take-profit submission outcome is ambiguous; refusing a duplicate order")
        elif phase == "tp_open" and not take_profit:
            status = self.order_status(self.state.data.get("tp_order_id"))
            if status.get("status") == "closed" and float(status.get("filled") or 0) > 0:
                LOG.info("Grid cycle completed by take-profit fill")
                self.state.clear()
            else:
                raise SafetyHalt("Take-profit is missing or not confirmed filled")

        if self.state.data["phase"] == "idle":
            self.place_entry()
        elif self.state.data["phase"] == "entry_open":
            LOG.info("Entry order remains open | order=%s", self.state.data.get("entry_order_id"))
        elif self.state.data["phase"] == "tp_open":
            LOG.info("Take-profit remains open | order=%s", self.state.data.get("tp_order_id"))

    def run_once(self) -> None:
        if not self.settings.live_trading:
            ticker = self.exchange.fetch_ticker(self.settings.symbol)
            for price in self.grid_levels():
                precise = self.precise_price(price)
                amount = self.amount_for_price(precise)
                notional = amount * float(self.market["contractSize"]) * precise
                LOG.info("DRY RUN | grid level=%s | contracts=%s | notional=%s USDT | estimatedMarginAt10x=%s USDT",
                         precise, amount, notional, notional / self.settings.leverage)
            LOG.info("DRY RUN | last=%s | no orders submitted", ticker.get("last"))
            return
        self.reconcile_live()

    def run_forever(self) -> None:
        self.load_market()
        LOG.info("Worker started | LIVE_TRADING=%s | symbol=%s | range=%s-%s | grid=%s | targetNotional=%s USDT | marginTarget=%s USDT",
                 self.settings.live_trading, self.settings.symbol, self.settings.lower_price,
                 self.settings.upper_price, self.settings.grid_count,
                 self.settings.target_notional_usdt, self.settings.margin_usdt)
        while True:
            try:
                self.run_once()
                time.sleep(self.settings.poll_seconds)
            except (ccxt.RateLimitExceeded, ccxt.NetworkError, ccxt.RequestTimeout) as exc:
                LOG.warning("Temporary MEXC connectivity/rate-limit error: %s", exc)
                time.sleep(self.settings.retry_seconds)
            except SafetyHalt:
                LOG.exception("Safety halt; no new orders will be placed until exchange/state is reconciled")
                time.sleep(self.settings.retry_seconds)
            except ccxt.ExchangeError:
                LOG.exception("MEXC rejected a request")
                time.sleep(self.settings.retry_seconds)
            except Exception:
                LOG.exception("Unexpected worker cycle error")
                time.sleep(self.settings.retry_seconds)


def main() -> None:
    settings = Settings.from_env()
    exchange = ccxt.mexc({
        "apiKey": settings.api_key,
        "secret": settings.api_secret,
        "enableRateLimit": True,
        "options": {"defaultType": "swap", "defaultSubType": "linear", "defaultSettle": "USDT"},
    })
    state = StateStore(settings.state_file) if settings.live_trading else None
    MEXCGridWorker(settings, exchange, state).run_forever()