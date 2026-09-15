import time
import traceback

from config import (
    LIVE_TRADING,
    LOOP_INTERVAL_SECONDS,
    SYMBOL,
    TIMEFRAME,
)

from exchange_client import MEXCClient


def run():
    print("=" * 60)
    print("wife_2 - MEXC SPOT CLOUD TRADING WORKER")
    print("=" * 60)

    print(f"Symbol: {SYMBOL}")
    print(f"Timeframe: {TIMEFRAME}")
    print(f"Live trading: {LIVE_TRADING}")
    print(f"Loop interval: {LOOP_INTERVAL_SECONDS}s")

    client = MEXCClient()

    print("MEXC connection: OK")
    print("MEXC market type: SPOT")

    ticker = client.ticker()

    print(
        f"Current price: {ticker.get('last')}"
    )

    if not LIVE_TRADING:
        print(
            "LIVE_TRADING=false"
        )
        print(
            "Trading orders are DISABLED."
        )

    while True:
        try:
            ticker = client.ticker()

            last = ticker.get("last")

            print(
                f"[MEXC] {SYMBOL} last={last}"
            )

            # =====================================================
            # استراتيجية التداول توضع هنا بعد اختبار الاتصال.
            #
            # لا يتم تنفيذ أمر حقيقي ما دام:
            # LIVE_TRADING=false
            # =====================================================

            time.sleep(LOOP_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            print("Worker stopped.")
            break

        except Exception as exc:
            print(
                f"Worker error: {type(exc).__name__}: {exc}"
            )

            traceback.print_exc()

            time.sleep(
                LOOP_INTERVAL_SECONDS
            )


if __name__ == "__main__":
    run()
