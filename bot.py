"""
MEXC Futures Grid Trading Bot (BTC/USDT)
Configured for Long Strategy (2 Grids, 10X Leverage, 5 USDT Margin)
Range: 84000 USDT to 84700 USDT
Designed for 24/7 Execution on Railway
"""

import os
import sys
import time
import logging
import signal
from typing import Dict, Any, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import ccxt

# ==========================================
# LOGGING SETUP
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("MEXC_GridBot")

# ==========================================
# CONFIGURATION & ENVIRONMENT VARIABLES
# ==========================================
API_KEY = os.getenv("MEXC_API_KEY") or os.getenv("API_KEY", "")
API_SECRET = os.getenv("MEXC_API_SECRET") or os.getenv("MEXC_SECRET_KEY") or os.getenv("API_SECRET", "")

# Operational Parameters from User Specification
SYMBOL = os.getenv("SYMBOL", "BTC/USDT:USDT")
LOWER_PRICE = float(os.getenv("LOWER_PRICE", "84000.0"))
UPPER_PRICE = float(os.getenv("UPPER_PRICE", "84700.0"))
GRID_COUNT = int(os.getenv("GRID_COUNT", "2"))
LEVERAGE = int(os.getenv("LEVERAGE", "10"))
INVESTMENT_USDT = float(os.getenv("INVESTMENT_USDT", "5.0"))
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "5"))

# Total purchasing power = Margin (5 USDT) * Leverage (10X) = 50 USDT
TOTAL_POSITION_VALUE = INVESTMENT_USDT * LEVERAGE

# Running flag for graceful termination
RUNNING = True


def signal_handler(signum, frame):
    global RUNNING
    logger.info(f"إشارة إيقاف مستلمة ({signum}). جاري إنهاء التشغيل بسلاسة...")
    RUNNING = False


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# ==========================================
# EXCHANGE INITIALIZATION
# ==========================================
def create_exchange_client() -> ccxt.mexc:
    if not API_KEY or not API_SECRET:
        logger.warning("تحذير: مفاتيح MEXC_API_KEY و MEXC_API_SECRET غير محددة! يرجى ضبطها في متغيرات Railway.")

    client = ccxt.mexc({
        'apiKey': API_KEY,
        'secret': API_SECRET,
        'enableRateLimit': True,
        'options': {
            'defaultType': 'swap',  # Futures / Swap mode
            'adjustForTimeDifference': True,
        }
    })
    return client


exchange = create_exchange_client()


def init_bot():
    logger.info("==================================================")
    logger.info("   بدء تشغيل البوت الشبكي السحابي (MEXC Futures)   ")
    logger.info(f"   الزوج المستهدف: {SYMBOL}")
    logger.info(f"   الاستراتيجية: Long (شراء منخفض / بيع مرتفع)")
    logger.info(f"   نطاق الشبكة: {LOWER_PRICE} USDT -> {UPPER_PRICE} USDT")
    logger.info(f"   عدد الشبكات: {GRID_COUNT}")
    logger.info(f"   الرافعة المالية: {LEVERAGE}X")
    logger.info(f"   الهامش المستخدم: {INVESTMENT_USDT} USDT")
    logger.info(f"   إجمالي القوة الشرائية: {TOTAL_POSITION_VALUE} USDT")
    logger.info("==================================================")

    try:
        exchange.load_markets()
        logger.info("تم تحميل بيانات الأسواق بنجاح.")
    except Exception as e:
        logger.error(f"خطأ أثناء تحميل الأسواق: {e}")

    try:
        # ضبط الرافعة المالية على زوج العقود
        exchange.set_leverage(LEVERAGE, SYMBOL)
        logger.info(f"تم ضبط الرافعة المالية بنجاح على {LEVERAGE}X")
    except Exception as e:
        logger.warning(f"ملاحظة عند ضبط الرافعة المالية: {e}")


def get_contract_amount(price: float) -> float:
    """
    حساب كمية البيتكوين المقابلة لنصيب كل مستوى شبكة
    grid_allocation_usdt = 50 / 2 = 25 USDT
    amount = 25 / price
    """
    grid_allocation_usdt = TOTAL_POSITION_VALUE / GRID_COUNT
    raw_amount = grid_allocation_usdt / price
    
    # تنسيق الكمية لتوافق دقة المنصة
    try:
        if SYMBOL in exchange.markets:
            formatted = exchange.amount_to_precision(SYMBOL, raw_amount)
            return float(formatted)
    except Exception:
        pass
    
    return round(raw_amount, 4)


def format_price(price: float) -> float:
    try:
        if SYMBOL in exchange.markets:
            formatted = exchange.price_to_precision(SYMBOL, price)
            return float(formatted)
    except Exception:
        pass
    return round(price, 2)


def run_grid():
    global RUNNING
    init_bot()

    buy_price = format_price(LOWER_PRICE)
    sell_price = format_price(UPPER_PRICE)
    amount = get_contract_amount(buy_price)

    logger.info(f"مستوى الشراء (القاع): {buy_price} USDT")
    logger.info(f"مستوى البيع (القمة): {sell_price} USDT")
    logger.info(f"حجم العقد لكل أمر شبكة: {amount} BTC")

    # active_orders: { price: order_id }
    active_orders: Dict[float, str] = {}
    consecutive_errors = 0

    while RUNNING:
        try:
            ticker = exchange.fetch_ticker(SYMBOL)
            current_price = ticker.get('last') or ticker.get('close')
            
            if current_price is None:
                logger.warning("تعذر استرجاع السعر اللحظي، إعادة المحاولة...")
                time.sleep(CHECK_INTERVAL_SECONDS)
                continue

            consecutive_errors = 0
            logger.info(
                f"السعر اللحظي: {current_price:.2f} USDT | "
                f"الأوامر النشطة: {len(active_orders)} | "
                f"نطاق الشراء/البيع: [{buy_price} / {sell_price}]"
            )

            # 1. متابعة وفحص حالة الأوامر القائمة
            for target_p, order_id in list(active_orders.items()):
                try:
                    order = exchange.fetch_order(order_id, SYMBOL)
                    order_status = order.get('status')
                    order_side = order.get('side')

                    # إذا تم تنفيذ أمر الشراء عند القاع (84000)
                    if order_status == 'closed' and order_side == 'buy':
                        logger.info(f"🟢 تم تنفيذ أمر الشراء بنجاح عند {target_p} USDT!")
                        logger.info(f"فتح أمر جني الأرباح (Sell Limit) عند {sell_price} USDT...")
                        sell_amount = get_contract_amount(sell_price)
                        take_profit_order = exchange.create_limit_sell_order(SYMBOL, sell_amount, sell_price)
                        active_orders[sell_price] = take_profit_order['id']
                        del active_orders[target_p]
                        logger.info(f"✅ تم وضع أمر جني الأرباح بنجاح (Order ID: {take_profit_order['id']})")

                    # إذا تم تنفيذ أمر البيع عند القمة (84700)
                    elif order_status == 'closed' and order_side == 'sell':
                        logger.info(f"🎯 تم جني الربح بنجاح عند {target_p} USDT!")
                        logger.info(f"إعادة فتح أمر الشراء عند القاع {buy_price} USDT...")
                        re_buy_amount = get_contract_amount(buy_price)
                        re_buy_order = exchange.create_limit_buy_order(SYMBOL, re_buy_amount, buy_price)
                        active_orders[buy_price] = re_buy_order['id']
                        del active_orders[target_p]
                        logger.info(f"✅ تم وضع أمر الشراء من جديد (Order ID: {re_buy_order['id']})")

                    # في حال إلغاء الأمر لأي سبب خارجي
                    elif order_status in ['canceled', 'rejected', 'expired']:
                        logger.warning(f"الأمر عند السعر {target_p} أصبح بحالة: {order_status}. إزالته من المتابعة.")
                        del active_orders[target_p]

                except Exception as order_err:
                    logger.error(f"خطأ أثناء فحص حالة الأمر {order_id}: {order_err}")

            # 2. إنشاء أمر الشراء الأولي عند القاع إذا لم يكن هناك أمر شراء أو بيع قائم
            if buy_price not in active_orders and sell_price not in active_orders:
                if current_price >= buy_price:
                    logger.info(f"وضع أمر الشراء الأولي عند القاع: {buy_price} USDT بحجم: {amount} BTC")
                    try:
                        order = exchange.create_limit_buy_order(SYMBOL, amount, buy_price)
                        active_orders[buy_price] = order['id']
                        logger.info(f"✅ تم وضع أمر الشراء الأولي بنجاح (Order ID: {order['id']})")
                    except Exception as place_err:
                        logger.error(f"فشل وضع أمر الشراء الأولي: {place_err}")
                else:
                    logger.info(f"السعر اللحظي ({current_price}) أقل من قاع الشبكة ({buy_price}). في انتظار ارتداد السعر...")

            time.sleep(CHECK_INTERVAL_SECONDS)

        except ccxt.RateLimitExceeded as rle:
            logger.warning(f"تجاوز حد الطلبات (Rate Limit): {rle}. الانتظار لمدة 15 ثانية...")
            time.sleep(15)
        except ccxt.NetworkError as ne:
            logger.warning(f"خطأ في الاتصال بالشبكة: {ne}. إعادة المحاولة بعد 10 ثوانٍ...")
            time.sleep(10)
        except Exception as err:
            consecutive_errors += 1
            backoff = min(60, 5 * consecutive_errors)
            logger.error(f"خطأ غير متوقع أثناء المتابعة: {err}. إعادة المحاولة بعد {backoff} ثوانٍ...")
            time.sleep(backoff)

    logger.info("تم إنهاء حلقة التداول الشبكي بأمان.")


if __name__ == "__main__":
    run_grid()
