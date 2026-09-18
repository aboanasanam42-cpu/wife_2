from __future__ import annotations

import sys
import time

from config import Config
from exchange_client import ExchangeClient
from strategy import TradingStrategy


def main() -> None:

    print("=" * 70)
    print("MEXC SPOT TRADING WORKER")
    print("SYMBOLS=ALL_USDT")
    print("=" * 70)

    try:
        Config.validate()

    except ValueError as exc:

        print(
            f"[CONFIG ERROR] {exc}"
        )

        sys.exit(1)

    client = ExchangeClient()

    strategy = TradingStrategy(
        client
    )

    print(
        "[CONFIG] "
        f"symbols={Config.SYMBOLS}"
    )

    print(
        "[CONFIG] "
        f"trade_amount="
        f"{Config.TRADE_AMOUNT_USDT:.2f} USDT"
    )

    print(
        "[CONFIG] "
        f"max_positions="
        f"{Config.MAX_OPEN_POSITIONS}"
    )

    print(
        "[CONFIG] "
        f"take_profit="
        f"{Config.TAKE_PROFIT_PCT}%"
    )

    print(
        "[CONFIG] "
        f"stop_loss="
        f"{Config.STOP_LOSS_PCT}%"
    )

    print(
        "[CONFIG] "
        f"mode="
        f"{'LIVE' if Config.LIVE_TRADING else 'DRY-RUN'}"
    )

    while True:

        cycle_started = time.time()

        try:

            # -------------------------------------------------
            # BALANCE
            # -------------------------------------------------

            usdt_balance = (
                client.get_usdt_balance()
            )

            # -------------------------------------------------
            # SYMBOLS
            # -------------------------------------------------

            symbols = (
                client.get_target_symbols()
            )

            if not symbols:

                print(
                    "[MEXC] No active "
                    "USDT Spot symbols found."
                )

                time.sleep(30)

                continue

            # -------------------------------------------------
            # ROTATING SCAN
            # -------------------------------------------------

            total_symbols = len(
                symbols
            )

            if (
                total_symbols
                <= Config.MAX_SCAN_SYMBOLS
            ):

                scan_symbols = symbols

            else:

                rotation = int(
                    time.time()
                    // max(
                        Config.POLL_INTERVAL,
                        1,
                    )
                )

                start = (
                    rotation
                    % total_symbols
                )

                ordered = (
                    symbols[start:]
                    + symbols[:start]
                )

                scan_symbols = ordered[
                    : Config.MAX_SCAN_SYMBOLS
                ]

            print(
                "\n[CYCLE] "
                f"USDT={usdt_balance:.4f} "
                f"universe={total_symbols} "
                f"scanning={len(scan_symbols)} "
                f"open={len(strategy.open_positions)} "
                f"mode="
                f"{'LIVE' if Config.LIVE_TRADING else 'DRY-RUN'}"
            )

            # -------------------------------------------------
            # PROCESS SYMBOLS
            # -------------------------------------------------

            for symbol in scan_symbols:

                try:

                    usdt_balance = (
                        strategy.run_cycle_for_symbol(
                            symbol,
                            usdt_balance,
                        )
                    )

                except Exception as exc:

                    print(
                        f"[SYMBOL ERROR] "
                        f"{symbol}: {exc}"
                    )

                time.sleep(
                    Config.REQUEST_DELAY
                )

            # -------------------------------------------------
            # WAIT
            # -------------------------------------------------

            elapsed = (
                time.time()
                - cycle_started
            )

            sleep_for = max(
                0.0,
                Config.POLL_INTERVAL
                - elapsed,
            )

            print(
                "[CYCLE COMPLETE] "
                f"open="
                f"{len(strategy.open_positions)} "
                f"sleep="
                f"{sleep_for:.1f}s"
            )

            time.sleep(
                sleep_for
            )

        except KeyboardInterrupt:

            print(
                "\nBot stopped by user."
            )

            return

        except Exception as exc:

            print(
                f"[WORKER ERROR] {exc}"
            )

            time.sleep(15)


if __name__ == "__main__":
    main()
