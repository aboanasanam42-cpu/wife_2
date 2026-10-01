import unittest
import tempfile
from pathlib import Path

from grid_worker import MEXCGridWorker, SafetyHalt, Settings, StateStore


class FakeExchange:
    def __init__(self):
        self.markets = {"BTC/USDT:USDT": {
            "swap": True, "linear": True, "settle": "USDT", "contract": True,
            "contractSize": 0.0001, "precision": {"amount": 1, "price": 1},
            "limits": {"amount": {"min": 1}, "cost": {"min": 1}},
        }}

    def load_markets(self):
        return self.markets

    def market(self, symbol):
        return self.markets[symbol]

    def amount_to_precision(self, symbol, amount):
        return str(int(amount))

    def price_to_precision(self, symbol, price):
        return str(round(price, 1))


class MemoryState:
    def __init__(self, data):
        self.data = data

    def save(self):
        pass


class ReconcileExchange(FakeExchange):
    def __init__(self):
        super().__init__()
        self.created = []

    def fetch_open_orders(self, symbol):
        return []

    def fetch_positions(self, symbols):
        return [{"contracts": 5, "side": "long", "entryPrice": 84000}]

    def create_order(self, symbol, order_type, side, amount, price, params):
        order = {"id": "take-profit-1", "side": side, "amount": amount, "price": price, "params": params}
        self.created.append(order)
        return order


class EntryExchange(FakeExchange):
    def __init__(self):
        super().__init__()
        self.margin_mode = None
        self.created = []

    def fetch_ticker(self, symbol):
        return {"last": 84050}

    def fetch_balance(self):
        return {"free": {"USDT": 10}}

    def set_margin_mode(self, mode, symbol, params):
        self.margin_mode = (mode, symbol, params)

    def create_order(self, symbol, order_type, side, amount, price, params):
        order = {"id": "entry-1", "params": params, "amount": amount, "price": price}
        self.created.append(order)
        return order


class EntryState(MemoryState):
    def start_cycle(self):
        self.data = {"phase": "entry_submitting", "entry_client_id": "w2e-test"}


class GridWorkerTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings()
        self.exchange = FakeExchange()
        self.worker = MEXCGridWorker(self.settings, self.exchange)

    def test_two_grid_levels_are_range_boundaries(self):
        self.assertEqual(self.worker.grid_levels(), [84000.0, 84700.0])

    def test_market_validation_and_contract_sizing(self):
        self.worker.load_market()
        amount = self.worker.amount_for_price(84000.0)
        self.assertEqual(amount, 5)
        self.assertLessEqual(amount * 0.0001 * 84000, 50)

    def test_rejects_non_linear_market(self):
        self.exchange.markets[self.settings.symbol]["linear"] = False
        with self.assertRaises(Exception):
            self.worker.load_market()

    def test_live_mode_requires_credentials(self):
        with self.assertRaisesRegex(ValueError, "MEXC_API_KEY"):
            Settings(live_trading=True).validate()

    def test_notional_cannot_exceed_leveraged_margin(self):
        settings = Settings(margin_usdt=4.0, target_notional_usdt=50.0)
        with self.assertRaisesRegex(ValueError, "exceeds margin"):
            settings.validate()

    def test_recovers_position_after_entry_submission_and_places_reduce_only_exit(self):
        state = MemoryState({"phase": "entry_submitting", "entry_client_id": "w2e-recovered"})
        exchange = ReconcileExchange()
        worker = MEXCGridWorker(Settings(live_trading=True), exchange, state)

        worker.reconcile_live()

        self.assertEqual(state.data["phase"], "tp_open")
        self.assertEqual(len(exchange.created), 1)
        self.assertEqual(exchange.created[0]["side"], "sell")
        self.assertTrue(exchange.created[0]["params"]["reduceOnly"])

    def test_order_without_id_or_bot_client_id_is_not_owned(self):
        state = MemoryState({"phase": "idle"})
        worker = MEXCGridWorker(self.settings, self.exchange, state)
        with self.assertRaises(SafetyHalt):
            worker.verify_orders_are_owned([{"id": None, "side": "buy"}])

    def test_entry_uses_mexc_isolated_one_way_contract_parameters(self):
        exchange = EntryExchange()
        state = EntryState({"phase": "idle"})
        worker = MEXCGridWorker(Settings(live_trading=True), exchange, state)
        worker.load_market()

        worker.place_entry()

        self.assertEqual(exchange.margin_mode[0], "isolated")
        self.assertEqual(exchange.margin_mode[2], {"leverage": 10, "direction": "long"})
        self.assertEqual(exchange.created[0]["params"]["positionMode"], 2)
        self.assertEqual(exchange.created[0]["params"]["leverage"], 10)
        self.assertIn("externalOid", exchange.created[0]["params"])

    def test_state_is_restored_after_worker_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "state.json")
            first = StateStore(path)
            first.start_cycle()
            client_id = first.data["entry_client_id"]
            first.lock.close()

            restarted = StateStore(path)
            try:
                self.assertEqual(restarted.data["phase"], "entry_submitting")
                self.assertEqual(restarted.data["entry_client_id"], client_id)
            finally:
                restarted.lock.close()

    def test_does_not_submit_entry_below_configured_margin(self):
        exchange = EntryExchange()
        exchange.fetch_balance = lambda: {"free": {"USDT": 4.99}}
        state = EntryState({"phase": "idle"})
        worker = MEXCGridWorker(Settings(live_trading=True), exchange, state)
        worker.load_market()

        with self.assertRaisesRegex(SafetyHalt, "below configured margin"):
            worker.place_entry()
        self.assertEqual(exchange.created, [])


if __name__ == "__main__":
    unittest.main()