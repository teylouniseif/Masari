"""
Run OptionsBuyStrategy and route decisions to Alpaca paper-trading options orders.

Usage:
  python3 test_alpaca_optionsplay_sandbox_execution.py             # scan and preview
  python3 test_alpaca_optionsplay_sandbox_execution.py --submit    # scan and buy
  python3 test_alpaca_optionsplay_sandbox_execution.py --monitor --submit
      # check previously-bought positions against live prices and sell-to-close any
      # that hit an exit condition (stop loss, time decay, technical or Greek signal)

With --submit, both modes do nothing while the market is closed.
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
from investment_strategies.stock_screener import StockScreener


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
        default=None,
        help="Symbols to scan. Defaults to the full S&P 500 (fetched from Wikipedia).",
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

    # Outside market hours quotes, IV and Greeks are stale, and any order only
    # queues for the open at a price picked from them. Previews still run.
    if args.submit and not alpaca_fetcher.is_market_open():
        print("Market is closed; not trading on stale quotes.")
        return

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

    symbols = args.symbols
    if symbols is None:
        sp500 = StockScreener(alpaca_fetcher).get_sp500_stocks()
        if sp500.empty:
            print("Could not fetch S&P 500 list; falling back to a small default set.")
            symbols = ["AAPL", "MSFT", "NVDA", "TSLA"]
        else:
            symbols = sp500["Symbol"].tolist()
    print(f"Scanning {len(symbols)} symbol(s).")

    opportunities = strategy.find_best_opportunities(symbols)
    if not opportunities:
        print("No opportunities found.")
        strategy.save_positions(args.positions_file)
        return

    # Drop contracts too expensive to buy even one of under the risk limit, so
    # the top slot is not wasted on an order that can only fail sizing.
    max_premium = executor.max_affordable_premium()
    opportunities = [opp for opp in opportunities if opp["option"]["ask"] <= max_premium]
    if not opportunities:
        print(f"No opportunities at or under ${max_premium:.2f} premium.")
        strategy.save_positions(args.positions_file)
        return

    # A full S&P 500 scan takes about ten minutes, so the 15:00 run can finish
    # after the close.
    if args.submit and not alpaca_fetcher.is_market_open():
        print("Market closed during the scan; not placing orders.")
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
        structure, metrics = opp.get("structure") or {}, opp["metrics"]
        print(
            f"  Score: {opp['score']:.0f} | regime: {structure.get('regime')} "
            f"(level {structure.get('breakout_level')}) | %B: {structure.get('percent_b')} | "
            f"RSI: {opp['rsi']:.1f} | delta: {metrics.get('delta')} | theta: {metrics.get('theta')} | "
            f"gamma: {metrics.get('gamma')} | vega: {metrics.get('vega')}"
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
