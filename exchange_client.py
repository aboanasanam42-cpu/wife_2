import time
import hmac
import hashlib
import requests
from config import Config

class ExchangeClient:
    def __init__(self):
        self.api_key = Config.API_KEY
        self.api_secret = Config.API_SECRET
        self.base_url = Config.BASE_URL
        self.session = requests.Session()
        self.session.headers.update({
            "X-MEXC-APIKEY": self.api_key,
            "Content-Type": "application/json"
        })
        self.symbols_cache = []
        self.last_symbols_fetch = 0

    def _sign_params(self, params=None):
        if params is None:
            params = {}
        params["timestamp"] = int(time.time() * 1000)
        query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        params["signature"] = signature
        return params

    def get_active_usdt_symbols(self):
        """جلب كافة أزواج USDT النشطة من MEXC وتحديثها دورياً"""
        now = time.time()
        if self.symbols_cache and (now - self.last_symbols_fetch < Config.SYMBOLS_REFRESH_MINUTES * 60):
            return self.symbols_cache

        endpoint = f"{self.base_url}/api/v3/exchangeInfo"
        try:
            res = self.session.get(endpoint, timeout=15)
            if res.status_code == 200:
                data = res.json()
                active_symbols = []
                for s in data.get("symbols", []):
                    # التحقق أن الزوج مقابل USDT، نشط، ومتاح للتداول الفوري
                    if (
                        s.get("quoteAsset") == "USDT"
                        and s.get("status") == "ENABLED"
                        and s.get("isSpotTradingAllowed", True)
                    ):
                        active_symbols.append(s["symbol"])
                
                self.symbols_cache = active_symbols
                self.last_symbols_fetch = now
                print(f"[Exchange] تم تحديث أزواج التداول: {len(active_symbols)} زوج نشط مقابل USDT.")
                return active_symbols
            else:
                print(f"[Exchange] خطأ في جلب الأزواج من MEXC: كود {res.status_code}")
        except Exception as e:
            print(f"[Exchange] استثناء أثناء جلب أزواج العملات: {e}")

        return self.symbols_cache if self.symbols_cache else ["BTCUSDT"]

    def get_target_symbols(self):
        """تحديد الأزواج المستهدفة بحسب إعداد المتغيرات"""
        raw = Config.SYMBOLS_CONFIG.upper()
        if raw in ["ALL_USDT", "ALL", "*", ""]:
            return self.get_active_usdt_symbols()
        return [s.strip() for s in raw.split(",") if s.strip()]

    def get_usdt_balance(self):
        """الاستعلام عن الرصيد المتاح من عملة USDT"""
        endpoint = f"{self.base_url}/api/v3/account"
        params = self._sign_params()
        try:
            res = self.session.get(endpoint, params=params, timeout=10)
            if res.status_code == 200:
                balances = res.json().get("balances", [])
                for b in balances:
                    if b.get("asset") == "USDT":
                        return float(b.get("free", 0.0))
            else:
                print(f"[Exchange] فشل جلب الرصيد: {res.text}")
        except Exception as e:
            print(f"[Exchange] استثناء أثناء قراءة رصيد الحساب: {e}")
        return 0.0

    def get_asset_balance(self, asset):
        """الاستعلام عن رصيد عملة معينة"""
        endpoint = f"{self.base_url}/api/v3/account"
        params = self._sign_params()
        try:
            res = self.session.get(endpoint, params=params, timeout=10)
            if res.status_code == 200:
                balances = res.json().get("balances", [])
                for b in balances:
                    if b.get("asset") == asset:
                        return float(b.get("free", 0.0))
        except Exception:
            pass
        return 0.0

    def get_ticker_price(self, symbol):
        """جلب السعر الحالي لزوج محدد"""
        endpoint = f"{self.base_url}/api/v3/ticker/price"
        try:
            res = self.session.get(endpoint, params={"symbol": symbol}, timeout=5)
            if res.status_code == 200:
                return float(res.json().get("price", 0.0))
        except Exception:
            pass
        return None

    def create_market_order(self, symbol, side, quote_order_qty=None, quantity=None):
        """تنفيذ أمر شراء أو بيع بسعر السوق (Market Order)"""
        endpoint = f"{self.base_url}/api/v3/order"
        params = {
            "symbol": symbol,
            "side": side.upper(),
            "type": "MARKET"
        }
        if quote_order_qty:
            params["quoteOrderQty"] = round(quote_order_qty, 2)
        elif quantity:
            params["quantity"] = quantity

        signed_params = self._sign_params(params)
        try:
            res = self.session.post(endpoint, params=signed_params, timeout=10)
            data = res.json()
            if res.status_code == 200:
                print(f"[Exchange] تنفيذ ناجح: {side} {symbol} - التفاصيل: {data.get('orderId')}")
                return data
            else:
                print(f"[Exchange] فشل تنفيذ الأمر لـ {symbol}: {data.get('msg', res.text)}")
                return None
        except Exception as e:
            print(f"[Exchange] خطأ اتصال أثناء تنفيذ الأمر لـ {symbol}: {e}")
            return None
