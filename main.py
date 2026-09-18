import time
import sys
from config import Config
from exchange_client import ExchangeClient
from strategy import TradingStrategy

def main():
    print("==================================================")
    print("  تشغيل بوت التداول الآلي على منصة MEXC عبر السحابة")
    print("  الوضع: تداول USDT مقابل كافة العملات النشطة")
    print("==================================================")

    client = ExchangeClient()
    strategy = TradingStrategy(client)

    # التحقق من صحة المفاتيح
    if not Config.API_KEY or not Config.API_SECRET:
        print("[خطأ] لم يتم ضبط MEXC_API_KEY أو MEXC_API_SECRET في متغيرات البيئة!")
        sys.exit(1)

    while True:
        try:
            # 1. الاستعلام عن الرصيد الحالي
            usdt_balance = client.get_usdt_balance()
            print(f"\n[دورة جديدة] الرصيد المتاح: {usdt_balance:.2f} USDT | الصفقات المفتوحة: {len(strategy.open_positions)}")

            # 2. جلب جميع أزواج USDT النشطة حالياً
            symbols = client.get_target_symbols()
            if not symbols:
                print("[تحذير] لم يتم العثور على أزواج نشطة. إعادة المحاولة بعد دقيقة...")
                time.sleep(60)
                continue

            # 3. فحص العملات وإجراء العمليات
            for symbol in symbols:
                try:
                    usdt_balance = strategy.run_cycle_for_symbol(symbol, usdt_balance)
                except Exception as sym_err:
                    print(f"[تخطي] خطأ أثناء معالجة الزوج {symbol}: {sym_err}")
                
                # تأخير زمني خفيف لحماية الـ IP من حدود الاستدعاء
                time.sleep(Config.REQUEST_DELAY)

            print(f"[اكتملت الدورة] انتظار {Config.POLL_INTERVAL} ثانية قبل بدء الدورة التالية...")
            time.sleep(Config.POLL_INTERVAL)

        except KeyboardInterrupt:
            print("\nتم إيقاف البوت يدوياً.")
            break
        except Exception as e:
            print(f"[خطأ غير متوقع في الحلقة الرئيسية]: {e}")
            time.sleep(15)

if __name__ == "__main__":
    main()
