import fcntl
import json
import logging
import math
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
    """Stop trading when exchange/account state cannot be safely determined."""


@dataclass(frozen=True)
class Settings:
    symbol: str = "BTC/USDT:USDT"

    lower_price: float = 84000.0
    upper_price: float = 85000.0

    leverage: int = 10
    margin_usdt: float = 5.0
    target_notional_usdt: float = 50.0

    # limit = wait for configured entry price
    # market = enter immediately when there is no active bot position/order
    entry_mode: str = "limit"

    poll_seconds: int = 5
    retry_seconds: int = 15

    live_trading: bool = False

    api_key: str = ""
    api_secret: str = ""

    state_file: str = ".wife_2_state.json"

    @classmethod
    def from_env(cls) -> "Settings":
        settings = cls(
            symbol=os.getenv("SYMBOL", cls.symbol).strip(),

            lower_price=float(
                os.getenv("LOWER_PRICE", str(cls.lower_price))
            ),

            upper_price=float(
                os.getenv("UPPER_PRICE", str(cls.upper_price))
            ),

            leverage=int(
                os.getenv("LEVERAGE", str(cls.leverage))
            ),

            margin_usdt=float(
                os.getenv("MARGIN_USDT", str(cls.margin_usdt))
            ),

            target_notional_usdt=float(
                os.getenv(
                    "TARGET_NOTIONAL_USDT",
                    str(cls.target_notional_usdt),
                )
            ),

            entry_mode=os.getenv(
                "ENTRY_MODE",
                cls.entry_mode,
            ).strip().lower(),

            poll_seconds=int(
                os.getenv("POLL_SECONDS", str(cls.poll_seconds))
            ),

            retry_seconds=int(
                os.getenv("RETRY_SECONDS", str(cls.retry_seconds))
            ),

            live_trading=(
                os.getenv("LIVE_TRADING", "false")
                .strip()
                .lower()
                == "true"
            ),

            api_key=os.getenv(
                "MEXC_API_KEY",
                "",
            ).strip(),

            api_secret=os.getenv(
                "MEXC_API_SECRET",
                "",
            ).strip(),

            state_file=os.getenv(
                "BOT_STATE_FILE",
                cls.state_file,
            ).strip(),
        )

        settings.validate()
        return settings

    def validate(self) -> None:
        if self.symbol != "BTC/USDT:USDT":
            raise ValueError(
                "This worker is configured for BTC/USDT:USDT only."
            )

        if self.lower_price <= 0:
            raise ValueError("LOWER_PRICE must be positive.")

        if self.upper_price <= self.lower_price:
            raise ValueError(
                "UPPER_PRICE must be greater than LOWER_PRICE."
            )

        if self.leverage < 1:
            raise ValueError("LEVERAGE must be >= 1.")

        if self.margin_usdt <= 0:
            raise ValueError("MARGIN_USDT must be positive.")

        if self.target_notional_usdt <= 0:
            raise ValueError(
                "TARGET_NOTIONAL_USDT must be positive."
            )

        if self.target_notional_usdt > (
            self.margin_usdt * self.leverage
        ):
            raise ValueError(
                "TARGET_NOTIONAL_USDT cannot exceed "
                "MARGIN_USDT * LEVERAGE."
            )

        if self.entry_mode not in {"limit", "market"}:
            raise ValueError(
                "ENTRY_MODE must be either 'limit' or 'market'."
            )

        if self.poll_seconds < 1:
            raise ValueError(
                "POLL_SECONDS must be >= 1."
            )

        if self.retry_seconds < 1:
            raise ValueError(
                "RETRY_SECONDS must be >= 1."
            )

        if self.live_trading:
            if not self.api_key:
                raise ValueError(
                    "LIVE_TRADING=true requires MEXC_API_KEY."
                )

            if not self.api_secret:
                raise ValueError(
                    "LIVE_TRADING=true requires MEXC_API_SECRET."
                )


class StateStore:
    def __init__(self, path: str):
        self.path = Path(path)

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.lock = self.path.with_suffix(
            self.path.suffix + ".lock"
        ).open("a+")

        try:
            fcntl.flock(
                self.lock.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError as exc:
            self.lock.close()

            raise SafetyHalt(
                "Another wife_2 worker is already running."
            ) from exc

        self.data: dict[str, Any] = self.load()

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"phase": "idle"}

        try:
            data = json.loads(
                self.path.read_text(
                    encoding="utf-8"
                )
            )
        except (
            OSError,
            json.JSONDecodeError,
        ) as exc:
            raise SafetyHalt(
                f"Cannot read state file: {self.path}"
            ) from exc

        allowed = {
            "idle",
            "entry_submitting",
            "entry_open",
            "tp_submitting",
            "tp_open",
        }

        if (
            not isinstance(data, dict)
            or data.get("phase") not in allowed
        ):
            raise SafetyHalt(
                "Invalid wife_2 state file."
            )

        return data

    def save(self) -> None:
        temporary = self.path.with_suffix(
            self.path.suffix + ".tmp"
        )

        with temporary.open(
            "w",
            encoding="utf-8",
        ) as state_file:
            state_file.write(
                json.dumps(
                    self.data,
                    sort_keys=True,
                )
            )

            state_file.flush()
            os.fsync(
                state_file.fileno()
            )

        os.replace(
            temporary,
            self.path,
        )

    def start_cycle(self) -> None:
        self.data = {
            "phase": "entry_submitting",
            "entry_client_id": (
                "w2e" + uuid.uuid4().hex[:20]
            ),
        }

        self.save()

    def clear(self) -> None:
        self.data = {
            "phase": "idle"
        }

        self.save()


class MEXCGridWorker:
    def __init__(
        self,
        settings: Settings,
        exchange: Any,
        state: StateStore | None,
    ):
        self.settings = settings
        self.exchange = exchange
        self.state = state
        self.market: dict[str, Any] | None = None

    # ---------------------------------------------------------
    # MARKET
    # ---------------------------------------------------------

    def load_market(self) -> dict[str, Any]:
        self.exchange.load_markets()

        if self.settings.symbol not in self.exchange.markets:
            raise SafetyHalt(
                f"MEXC does not list {self.settings.symbol}"
            )

        market = self.exchange.market(
            self.settings.symbol
        )

        if not market.get("swap"):
            raise SafetyHalt(
                "Selected market is not a perpetual swap."
            )

        if market.get("linear") is not True:
            raise SafetyHalt(
                "Selected market is not a linear USDT contract."
            )

        if market.get("settle") != "USDT":
            raise SafetyHalt(
                "Selected market does not settle in USDT."
            )

        if not market.get("contract"):
            raise SafetyHalt(
                "Selected market is not a contract market."
            )

        contract_size = market.get(
            "contractSize"
        )

        if not contract_size:
            raise SafetyHalt(
                "MEXC did not provide contractSize."
            )

        amount_precision = (
            market.get("precision", {})
            .get("amount")
        )

        price_precision = (
            market.get("precision", {})
            .get("price")
        )

        if amount_precision is None:
            raise SafetyHalt(
                "MEXC did not provide amount precision."
            )

        if price_precision is None:
            raise SafetyHalt(
                "MEXC did not provide price precision."
            )

        self.market = market

        LOG.info(
            "Market validated | symbol=%s | "
            "contractSize=%s | amountPrecision=%s | "
            "pricePrecision=%s | minAmount=%s",
            self.settings.symbol,
            contract_size,
            amount_precision,
            price_precision,
            (
                market.get("limits", {})
                .get("amount", {})
                .get("min")
            ),
        )

        return market

    # ---------------------------------------------------------
    # PRECISION
    # ---------------------------------------------------------

    def precise_price(
        self,
        price: float,
    ) -> float:
        return float(
            self.exchange.price_to_precision(
                self.settings.symbol,
                price,
            )
        )

    def amount_for_price(
        self,
        price: float,
    ) -> float:
        if self.market is None:
            raise SafetyHalt(
                "Market has not been loaded."
            )

        contract_size = float(
            self.market["contractSize"]
        )

        raw_contracts = (
            self.settings.target_notional_usdt
            / (price * contract_size)
        )

        amount_precision = (
            self.market["precision"]["amount"]
        )

        # We deliberately round UP to the next valid
        # contract increment so the target is not silently
        # reduced by exchange precision.
        if (
            isinstance(
                amount_precision,
                (int, float),
            )
            and float(amount_precision) >= 1
        ):
            step = float(
                amount_precision
            )

            contracts = (
                math.ceil(
                    raw_contracts / step
                )
                * step
            )
        else:
            # Fallback for decimal precision markets.
            formatted = (
                self.exchange.amount_to_precision(
                    self.settings.symbol,
                    raw_contracts,
                )
            )

            contracts = float(formatted)

        contracts = float(
            self.exchange.amount_to_precision(
                self.settings.symbol,
                contracts,
            )
        )

        limits = self.market.get(
            "limits",
            {},
        )

        min_amount = (
            limits.get("amount", {})
            .get("min")
        )

        if (
            min_amount is not None
            and contracts < float(min_amount)
        ):
            contracts = float(
                self.exchange.amount_to_precision(
                    self.settings.symbol,
                    float(min_amount),
                )
            )

        if contracts <= 0:
            raise SafetyHalt(
                "Calculated Futures contract amount is zero."
            )

        notional = (
            contracts
            * contract_size
            * price
        )

        max_amount = (
            limits.get("amount", {})
            .get("max")
        )

        if (
            max_amount is not None
            and contracts > float(max_amount)
        ):
            raise SafetyHalt(
                f"Calculated contracts {contracts} "
                f"exceed MEXC maximum {max_amount}."
            )

        min_cost = (
            limits.get("cost", {})
            .get("min")
        )

        if (
            min_cost is not None
            and notional < float(min_cost)
        ):
            raise SafetyHalt(
                f"Order notional {notional:.8f} "
                f"is below exchange minimum {min_cost}."
            )

        max_cost = (
            limits.get("cost", {})
            .get("max")
        )

        if (
            max_cost is not None
            and notional > float(max_cost)
        ):
            raise SafetyHalt(
                f"Order notional {notional:.8f} "
                f"exceeds exchange maximum {max_cost}."
            )

        LOG.info(
            "Contract sizing | price=%s | "
            "contracts=%s | contractSize=%s | "
            "notional=%s USDT",
            price,
            contracts,
            contract_size,
            notional,
        )

        return contracts

    # ---------------------------------------------------------
    # ORDER IDENTIFICATION
    # ---------------------------------------------------------

    @staticmethod
    def client_id(
        order: dict[str, Any],
    ) -> str | None:
        info = order.get("info") or {}

        return (
            order.get("clientOrderId")
            or order.get("clientOrderID")
            or info.get("clientOrderId")
            or info.get("externalOid")
        )

    def is_owned(
        self,
        order: dict[str, Any],
    ) -> bool:
        cid = self.client_id(order)

        if cid:
            return cid.startswith(
                (
                    "w2e",
                    "w2t",
                )
            )

        if self.state is None:
            return False

        known_ids = {
            self.state.data.get(
                "entry_order_id"
            ),
            self.state.data.get(
                "tp_order_id"
            ),
        }

        return order.get("id") in known_ids

    def find_owned_entry(
        self,
        orders: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        return next(
            (
                order
                for order in orders
                if (
                    self.client_id(order)
                    or ""
                ).startswith("w2e")
            ),
            None,
        )

    def find_owned_tp(
        self,
        orders: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        return next(
            (
                order
                for order in orders
                if (
                    self.client_id(order)
                    or ""
                ).startswith("w2t")
            ),
            None,
        )

    def verify_orders(
        self,
        orders: list[dict[str, Any]],
    ) -> None:
        foreign = [
            order
            for order in orders
            if not self.is_owned(order)
        ]

        if foreign:
            raise SafetyHalt(
                "Found unmanaged open order(s) for "
                f"{self.settings.symbol}. "
                "The bot will not modify them."
            )

        entries = [
            order
            for order in orders
            if (
                self.client_id(order)
                or ""
            ).startswith("w2e")
        ]

        tps = [
            order
            for order in orders
            if (
                self.client_id(order)
                or ""
            ).startswith("w2t")
        ]

        if len(entries) > 1:
            raise SafetyHalt(
                "More than one wife_2 entry order exists."
            )

        if len(tps) > 1:
            raise SafetyHalt(
                "More than one wife_2 take-profit exists."
            )

    # ---------------------------------------------------------
    # POSITIONS
    # ---------------------------------------------------------

    def positions(
        self,
    ) -> list[dict[str, Any]]:
        positions = self.exchange.fetch_positions(
            [self.settings.symbol]
        )

        active = []

        for position in positions:
            contracts = position.get(
                "contracts"
            )

            if contracts is None:
                contracts = (
                    position.get("info") or {}
                ).get("vol")

            if (
                contracts is not None
                and abs(float(contracts)) > 0
            ):
                active.append(position)

        return active

    # ---------------------------------------------------------
    # LEVERAGE / MARGIN
    # ---------------------------------------------------------

    def configure_account(self) -> None:
        try:
            self.exchange.set_margin_mode(
                "isolated",
                self.settings.symbol,
                {
                    "leverage": self.settings.leverage,
                    "direction": "long",
                },
            )

            LOG.info(
                "Margin mode configured | isolated"
            )

        except ccxt.ExchangeError as exc:
            message = str(exc).lower()

            if (
                "no need" in message
                or "already" in message
                or "same" in message
            ):
                LOG.info(
                    "Isolated margin already configured."
                )
            else:
                raise

        try:
            self.exchange.set_leverage(
                self.settings.leverage,
                self.settings.symbol,
                {
                    "marginMode": "isolated",
                },
            )

            LOG.info(
                "Leverage configured | %sx",
                self.settings.leverage,
            )

        except ccxt.ExchangeError as exc:
            message = str(exc).lower()

            if (
                "no need" in message
                or "already" in message
                or "same" in message
            ):
                LOG.info(
                    "Leverage already configured | %sx",
                    self.settings.leverage,
                )
            else:
                raise

    # ---------------------------------------------------------
    # ENTRY
    # ---------------------------------------------------------

    def place_entry(
        self,
    ) -> None:
        ticker = self.exchange.fetch_ticker(
            self.settings.symbol
        )

        last_price = ticker.get("last")

        if last_price is None:
            raise SafetyHalt(
                "MEXC did not return a last price."
            )

        last_price = float(last_price)

        # MARKET ENTRY
        if self.settings.entry_mode == "market":
            entry_price_for_sizing = (
                self.precise_price(last_price)
            )

            amount = self.amount_for_price(
                entry_price_for_sizing
            )

            LOG.info(
                "MARKET ENTRY | last=%s | "
                "contracts=%s | targetNotional=%s USDT",
                last_price,
                amount,
                self.settings.target_notional_usdt,
            )

            if not self.settings.live_trading:
                LOG.info(
                    "DRY RUN | market entry not submitted."
                )
                return

            self.configure_account()

            self.state.start_cycle()

            client_id = (
                self.state.data[
                    "entry_client_id"
                ]
            )

            try:
                order = self.exchange.create_order(
                    self.settings.symbol,
                    "market",
                    "buy",
                    amount,
                    None,
                    {
                        "externalOid": client_id,
                        "positionMode": 2,
                        "marginMode": "isolated",
                        "leverage": self.settings.leverage,
                    },
                )

            except Exception:
                self.state.clear()
                raise

            self.state.data.update(
                phase="entry_open",
                entry_order_id=order.get("id"),
            )

            self.state.save()

            LOG.info(
                "MARKET ENTRY SUBMITTED | "
                "order=%s | clientOrderId=%s",
                order.get("id"),
                client_id,
            )

            return

        # LIMIT ENTRY
        if not (
            self.settings.lower_price
            <= last_price
            <= self.settings.upper_price
        ):
            LOG.info(
                "No limit entry | last=%s is "
                "outside range %s-%s",
                last_price,
                self.settings.lower_price,
                self.settings.upper_price,
            )

            return

        price = self.precise_price(
            self.settings.lower_price
        )

        amount = self.amount_for_price(
            price
        )

        contract_size = float(
            self.market["contractSize"]
        )

        notional = (
            amount
            * contract_size
            * price
        )

        LOG.info(
            "LIMIT ENTRY | price=%s | "
            "contracts=%s | notional=%s USDT | "
            "estimatedMargin=%s USDT",
            price,
            amount,
            notional,
            notional / self.settings.leverage,
        )

        if not self.settings.live_trading:
            LOG.info(
                "DRY RUN | limit order not submitted."
            )
            return

        self.configure_account()

        self.state.start_cycle()

        client_id = (
            self.state.data[
                "entry_client_id"
            ]
        )

        try:
            order = self.exchange.create_order(
                self.settings.symbol,
                "limit",
                "buy",
                amount,
                price,
                {
                    "externalOid": client_id,
                    "positionMode": 2,
                    "marginMode": "isolated",
                    "leverage": self.settings.leverage,
                },
            )

        except Exception:
            self.state.clear()
            raise

        self.state.data.update(
            phase="entry_open",
            entry_order_id=order.get("id"),
        )

        self.state.save()

        LOG.info(
            "LIMIT ENTRY SUBMITTED | "
            "order=%s | price=%s | contracts=%s",
            order.get("id"),
            price,
            amount,
        )

    # ---------------------------------------------------------
    # TAKE PROFIT
    # ---------------------------------------------------------

    def place_take_profit(
        self,
        position: dict[str, Any],
    ) -> None:
        side = position.get("side")

        if side not in (
            "long",
            "buy",
        ):
            raise SafetyHalt(
                f"Unexpected position side: {side}"
            )

        contracts = position.get(
            "contracts"
        )

        if contracts is None:
            contracts = (
                position.get("info") or {}
            ).get("vol")

        if contracts is None:
            raise SafetyHalt(
                "Position contracts unavailable."
            )

        contracts = abs(
            float(contracts)
        )

        entry_price = position.get(
            "entryPrice"
        )

        if not entry_price:
            entry_price = (
                position.get("info") or {}
            ).get("avgEntryPrice")

        if contracts <= 0:
            raise SafetyHalt(
                "Position has zero contracts."
            )

        if not entry_price:
            raise SafetyHalt(
                "Position entry price unavailable."
            )

        entry_price = float(
            entry_price
        )

        target = self.precise_price(
            self.settings.upper_price
        )

        if target <= entry_price:
            raise SafetyHalt(
                "Take-profit must be above "
                "the long entry price."
            )

        client_id = (
            "w2t"
            + uuid.uuid4().hex[:20]
        )

        self.state.data.update(
            phase="tp_submitting",
            tp_client_id=client_id,
            position_amount=contracts,
            entry_price=entry_price,
        )

        self.state.save()

        try:
            order = self.exchange.create_order(
                self.settings.symbol,
                "limit",
                "sell",
                contracts,
                target,
                {
                    "reduceOnly": True,
                    "externalOid": client_id,
                    "positionMode": 2,
                    "marginMode": "isolated",
                    "leverage": self.settings.leverage,
                },
            )

        except Exception:
            raise

        self.state.data.update(
            phase="tp_open",
            tp_order_id=order.get("id"),
        )

        self.state.save()

        LOG.info(
            "TAKE PROFIT SUBMITTED | "
            "order=%s | price=%s | contracts=%s",
            order.get("id"),
            target,
            contracts,
        )

    # ---------------------------------------------------------
    # ORDER STATUS
    # ---------------------------------------------------------

    def order_status(
        self,
        order_id: str | None,
    ) -> dict[str, Any]:
        if not order_id:
            raise SafetyHalt(
                "Missing order ID."
            )

        return self.exchange.fetch_order(
            order_id,
            self.settings.symbol,
        )
    
    # ---------------------------------------------------------
    # LIVE RECONCILIATION
    # ---------------------------------------------------------

    def reconcile_live(self) -> None:
        orders = self.exchange.fetch_open_orders(
            self.settings.symbol
        )

        self.verify_orders(orders)

        active_positions = self.positions()

        if len(active_positions) > 1:
            raise SafetyHalt(
                "More than one active position exists."
            )

        position = (
            active_positions[0]
            if active_positions
            else None
        )

        entry = self.find_owned_entry(
            orders
        )

        tp = self.find_owned_tp(
            orders
        )

        # -----------------------------------------------------
        # Recover exchange state after Railway restart
        # -----------------------------------------------------

        if self.state.data["phase"] == "idle":

            if position and tp:
                self.state.data.update(
                    phase="tp_open",
                    tp_client_id=self.client_id(tp),
                    tp_order_id=tp.get("id"),
                )

                self.state.save()

                LOG.info(
                    "Recovered existing position "
                    "and take-profit from MEXC."
                )

                return

            if position and not tp:
                self.state.data.update(
                    phase="entry_open",
                    entry_client_id=(
                        self.client_id(entry)
                        if entry
                        else None
                    ),
                    entry_order_id=(
                        entry.get("id")
                        if entry
                        else None
                    ),
                )

                self.state.save()

                LOG.warning(
                    "Recovered position without TP."
                )

                self.place_take_profit(
                    position
                )

                return

            if position and not tp:
                self.state.data.update(
                    phase="entry_open",
                    entry_client_id=(
                        self.client_id(entry)
                        if entry
                        else None
                    ),
                    entry_order_id=(
                        entry.get("id")
                        if entry
                        else None
                    ),
                )

                self.state.save()

                LOG.warning(
                    "Recovered position without TP."
                )

                self.place_take_profit(
                    position
                )

                return

            if entry and not position:
                self.state.data.update(
                    phase="entry_open",
                    entry_client_id=self.client_id(
                        entry
                    ),
                    entry_order_id=entry.get(
                        "id"
                    ),
                )

                self.state.save()

                LOG.info(
                    "Recovered existing entry "
                    "order from MEXC | order=%s",
                    entry.get("id"),
                )

                return

            if not position and not entry and not tp:
                self.place_entry()
                return

        # -----------------------------------------------------
        # Position exists
        # -----------------------------------------------------

        if position:

            if entry:
                LOG.warning(
                    "Position exists while entry "
                    "order is still open. Cancelling "
                    "remaining entry quantity."
                )

                self.exchange.cancel_order(
                    entry["id"],
                    self.settings.symbol,
                )

                time.sleep(1)

                orders = (
                    self.exchange.fetch_open_orders(
                        self.settings.symbol
                    )
                )

                self.verify_orders(
                    orders
                )

                tp = self.find_owned_tp(
                    orders
                )

            if tp:
                self.state.data.update(
                    phase="tp_open",
                    tp_order_id=tp.get("id"),
                    tp_client_id=self.client_id(tp),
                )

                self.state.save()

                LOG.info(
                    "Position protected by TP | "
                    "order=%s",
                    tp.get("id"),
                )

                return

            if self.state.data["phase"] == "tp_submitting":
                raise SafetyHalt(
                    "TP submission result is ambiguous."
                )

            self.place_take_profit(
                position
            )

            return
        # -----------------------------------------------------
        # No position
        # -----------------------------------------------------

        phase = self.state.data[
            "phase"
        ]

        if phase == "entry_submitting":

            if entry:
                self.state.data.update(
                    phase="entry_open",
                    entry_order_id=entry.get(
                        "id"
                    ),
                    entry_client_id=self.client_id(
                        entry
                    ),
                )

                self.state.save()

                return

            raise SafetyHalt(
                "Entry submission outcome "
                "cannot be determined safely."
            )

        if phase == "entry_open":

            if entry:
                LOG.info(
                    "Entry order remains open | "
                    "order=%s",
                    entry.get("id"),
                )

                return

            old_order_id = self.state.data.get(
                "entry_order_id"
            )

            status = self.order_status(
                old_order_id
            )

            status_name = (
                status.get("status")
                or ""
            ).lower()

            if status_name in {
                "canceled",
                "cancelled",
                "expired",
                "rejected",
            }:
                LOG.info(
                    "Entry finished without position | "
                    "status=%s",
                    status_name,
                )

                self.state.clear()

                return

            if status_name == "closed":
                # Closed but no position found.
                # Re-read positions before allowing
                # a new order.
                time.sleep(1)

                active_positions = self.positions()

                if active_positions:
                    return

                raise SafetyHalt(
                    "Entry order is closed but no "
                    "position was detected."
                )

            raise SafetyHalt(
                "Entry order disappeared without "
                "confirmed cancellation."
            )

        # -----------------------------------------------------
        # TP state
        # -----------------------------------------------------

        if phase == "tp_submitting":

            if tp:
                self.state.data.update(
                    phase="tp_open",
                    tp_order_id=tp.get("id"),
                )

                self.state.save()

                return

            raise SafetyHalt(
                "TP submission result "
                "cannot be determined."
            )

        if phase == "tp_open":

            if tp:
                LOG.info(
                    "Take-profit remains open | "
                    "order=%s",
                    tp.get("id"),
                )

                return

            tp_order_id = self.state.data.get(
                "tp_order_id"
            )

            status = self.order_status(
                tp_order_id
            )

            status_name = (
                status.get("status")
                or ""
            ).lower()

            filled = float(
                status.get("filled") or 0
            )

            if (
                status_name == "closed"
                and filled > 0
            ):
                LOG.info(
                    "Take-profit filled. "
                    "Grid cycle completed."
                )

                self.state.clear()

                return

            raise SafetyHalt(
                "Take-profit disappeared "
                "without confirmed fill."
            )

    # ---------------------------------------------------------
    # DRY RUN
    # ---------------------------------------------------------

    def dry_run(self) -> None:
        ticker = self.exchange.fetch_ticker(
            self.settings.symbol
        )

        last = ticker.get("last")

        price = self.precise_price(
            last
        )

        amount = self.amount_for_price(
            price
        )

        contract_size = float(
            self.market["contractSize"]
        )

        notional = (
            amount
            * contract_size
            * price
        )

        LOG.info(
            "DRY RUN | last=%s | contracts=%s | "
            "notional=%s USDT | estimatedMargin=%s USDT | "
            "entryMode=%s",
            last,
            amount,
            notional,
            notional / self.settings.leverage,
            self.settings.entry_mode,
        )

        LOG.info(
            "DRY RUN | no real order submitted."
        )

    # ---------------------------------------------------------
    # MAIN LOOP
    # ---------------------------------------------------------

    def run_once(self) -> None:
        if not self.settings.live_trading:
            self.dry_run()
            return

        self.reconcile_live()

    def run_forever(self) -> None:
        self.load_market()

        LOG.info(
            "Worker started | LIVE_TRADING=%s | "
            "symbol=%s | range=%s-%s | "
            "leverage=%sx | targetNotional=%s USDT | "
            "marginTarget=%s USDT | entryMode=%s",
            self.settings.live_trading,
            self.settings.symbol,
            self.settings.lower_price,
            self.settings.upper_price,
            self.settings.leverage,
            self.settings.target_notional_usdt,
            self.settings.margin_usdt,
            self.settings.entry_mode,
        )

        while True:
            try:
                self.run_once()

                time.sleep(
                    self.settings.poll_seconds
                )

            except (
                ccxt.RateLimitExceeded,
                ccxt.NetworkError,
                ccxt.RequestTimeout,
            ) as exc:
                LOG.warning(
                    "Temporary MEXC network/rate-limit error: %s",
                    exc,
                )

                time.sleep(
                    self.settings.retry_seconds
                )

            except SafetyHalt as exc:
                LOG.error(
                    "SAFETY HALT: %s",
                    exc,
                )

                time.sleep(
                    self.settings.retry_seconds
                )

            except ccxt.AuthenticationError as exc:
                LOG.error(
                    "MEXC authentication/permission error: %s",
                    exc,
                )

                time.sleep(
                    self.settings.retry_seconds
                )

            except ccxt.ExchangeError as exc:
                LOG.error(
                    "MEXC rejected request: %s",
                    exc,
                )

                time.sleep(
                    self.settings.retry_seconds
                )

            except Exception as exc:
                LOG.exception(
                    "Unexpected worker error: %s",
                    exc,
                )

                time.sleep(
                    self.settings.retry_seconds
                )


def main() -> None:
    settings = Settings.from_env()

    exchange = ccxt.mexc(
        {
            "apiKey": settings.api_key,
            "secret": settings.api_secret,
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",
                "defaultSubType": "linear",
                "defaultSettle": "USDT",
            },
        }
    )

    state = (
        StateStore(settings.state_file)
        if settings.live_trading
        else None
    )

    worker = MEXCGridWorker(
        settings,
        exchange,
        state,
    )

    worker.run_forever()


if __name__ == "__main__":
    main()
