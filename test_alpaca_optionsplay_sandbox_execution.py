"""
Run OptionsBuyStrategy and route decisions to Alpaca paper-trading options orders.

Usage:
  python3 test_alpaca_optionsplay_sandbox_execution.py             # scan and preview
  python3 test_alpaca_optionsplay_sandbox_execution.py --submit    # scan and buy
  python3 test_alpaca_optionsplay_sandbox_execution.py --monitor --submit
      # check previously-bought positions against live prices and sell-to-close any
      # that hit an exit condition (profit target, stop loss, time decay, technical signal)
"""

import argparse

from investment_strategies.alpaca_data_fetcher import AlpacaDataFetcher
from investment_strategies.alpaca_optionsplay_sandbox import (
    AlpacaOptionsPlaySandboxExecutor,
)
from investment_strategies.options_buy_strategy import (
    DEFAULT_POSITIONS_STORE,
    OptionsBuyStrategy,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan and execute options strategy decisions with Alpaca paper trading."
    )
    parser.add_argument(
        "--submit",
        default=False,
        action="store_true",
        help="Actually submit paper orders (default: preview impact only).",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["AAPL", "MSFT", "NVDA", "TSLA"],
        help="Symbols to scan.",
    )
    parser.add_argument(
        "--max-orders",
        type=int,
        default=1,
        help="Max number of top opportunities to execute.",
    )
    parser.add_argument(
        "--monitor",
        action="store_true",
        help="Instead of scanning for new entries, check tracked open positions against "
        "live Alpaca prices and sell-to-close any that hit an exit condition.",
    )
    parser.add_argument(
        "--positions-file",
        default=DEFAULT_POSITIONS_STORE,
        help="Where open/closed position tracking is persisted between runs.",
    )
    args = parser.parse_args()

    alpaca_fetcher = AlpacaDataFetcher()
    account = alpaca_fetcher.ensure_paper_account()
    print(
        f"Connected to Alpaca paper account {account.get('account_number')} "
        f"(status={account.get('status')})."
    )

    strategy = OptionsBuyStrategy(alpaca_fetcher)
    strategy.load_positions(args.positions_file)
    executor = AlpacaOptionsPlaySandboxExecutor(alpaca_fetcher, strategy=strategy)

    if args.monitor:
        print(f"Monitoring {len(strategy.open_positions)} open position(s)...")
        results = executor.monitor_and_close(submit_order=args.submit)
        if not results:
            print("No open positions met an exit condition.")
        for result in results:
            print(f"\nClosing {result.option_symbol}")
            if result.error:
                print(f"  Error: {result.error}")
                continue
            print(f"  Payload: {result.payload}")
            if result.submitted:
                print(f"  Order response: {result.response}")
            else:
                print("  Not submitted (--submit not set).")

        if strategy.closed_positions:
            print("\nClosed positions:")
            for position in strategy.closed_positions:
                pnl = position.get("final_pnl")
                pnl_str = f"${pnl:.2f}" if isinstance(pnl, (int, float)) else "unknown"
                print(
                    f"  {position['id']}: {position.get('exit_reason')} -> P&L {pnl_str}"
                )

        strategy.save_positions(args.positions_file)
        return

    opportunities = strategy.find_best_opportunities(args.symbols)
    if not opportunities:
        print("No opportunities found.")
        strategy.save_positions(args.positions_file)
        return

    print(f"Found {len(opportunities)} opportunities. Executing top {args.max_orders}.")
    for idx, opp in enumerate(opportunities[: args.max_orders], start=1):
        result = executor.execute_opportunity(
            opp,
            submit_order=args.submit,
            preview_impact=True,
        )

        print(
            f"\n[{idx}] {opp['symbol']} {opp['option']['type'].upper()} "
            f"{opp['option']['strike']}"
        )
        if result.error:
            print(f"  Error: {result.error}")
            continue

        print(f"  Account: {result.account_id}")
        print(f"  Option symbol: {result.option_symbol}")
        print(f"  Payload: {result.payload}")
        if result.impact:
            print(f"  Impact: {result.impact}")
        if result.submitted:
            print(f"  Order response: {result.response}")
        else:
            print("  Not submitted (--submit not set).")

    strategy.save_positions(args.positions_file)


if __name__ == "__main__":
    main()
