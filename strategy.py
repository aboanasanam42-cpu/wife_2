import time
from config import Config

class TradingStrategy:
    def __init__(self, client):
        self.client = client
        # تتبع الصفقات المفتوحة: {symbol: {'buy_price': float, 'quantity': float, 'time': float}}
        self.open_positions = {}

    def run_cycle_for_symbol(self, symbol, current_balance):
        """فحص وتطبيق الاستراتيجية لزوج محدد"""
        current_price = self.client.get_ticker_price(symbol)
        if not current_price or current_price <= 0:
            return current_balance

        base_asset = symbol.replace("USDT", "")

        # 1. إذا كان لدينا مركز مفتوح على هذا الزوج، نفحص جني الأرباح أو وقف الخسارة
        if symbol in self.open_positions:
            pos = self.open_positions[symbol]
            buy_price = pos["buy_price"]
            change_pct = ((current_price - buy_price) / buy_price) * 100.0

            # شرط جني الأرباح (Take Profit)
            if change_pct >= Config.TAKE_PROFIT_PCT:
                print(f"[Strategy] تحقق هدف الربح ({change_pct:.2f}%) لزوج {symbol}. تنفيذ البيع...")
                asset_qty = self.client.get_asset_balance(base_asset)
                if asset_qty > 0:
                    order = self.client.create_market_order(symbol, side="SELL", quantity=asset_qty)
                    if order:
                        del self.open_positions[symbol]
                        return self.client.get_usdt_balance()

            # شرط وقف الخسارة (Stop Loss)
            elif change_pct <= -Config.STOP_LOSS_PCT:
                print(f"[Strategy] تفعيل وقف الخسارة ({change_pct:.2f}%) لزوج {symbol}. إغلاق المركز...")
                asset_qty = self.client.get_asset_balance(base_asset)
                if asset_qty > 0:
                    order = self.client.create_market_order(symbol, side="SELL", quantity=asset_qty)
                    if order:
                        del self.open_positions[symbol]
                        return self.client.get_usdt_balance()

        # 2. فحص إمكانية فتح مركز جديد
        else:
            # التحقق من أن عدد الصفقات المفتوحة لم يتجاوز الحد الأقصى
            if len(self.open_positions) >= Config.MAX_OPEN_POSITIONS:
                return current_balance

            # التحقق من كفاية رصيد الـ USDT
            if current_balance >= Config.TRADE_AMOUNT_USDT:
                # مثال منطقي: الشراء بمبلغ مخصص لكل صفقة
                order = self.client.create_market_order(
                    symbol=symbol,
                    side="BUY",
                    quote_order_qty=Config.TRADE_AMOUNT_USDT
                )
                if order:
                    self.open_positions[symbol] = {
                        "buy_price": current_price,
                        "time": time.time()
                    }
                    current_balance -= Config.TRADE_AMOUNT_USDT
                    print(f"[Strategy] تم فتح صفقة جديدة على {symbol} بسعر {current_price} USDT")

        return current_balance
