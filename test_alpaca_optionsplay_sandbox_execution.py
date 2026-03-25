"""
Run OptionsBuyStrategy and route decisions to Alpaca paper-trading options orders.

Usage:
  python3 test_alpaca_optionsplay_sandbox_execution.py
  python3 test_alpaca_optionsplay_sandbox_execution.py --submit
"""

import argparse

from investment_strategies.alpaca_data_fetcher import AlpacaDataFetcher
from investment_strategies.alpaca_optionsplay_sandbox import (
    AlpacaOptionsPlaySandboxExecutor,
)
from investment_strategies.options_buy_strategy import OptionsBuyStrategy
from investment_strategies.questrade_data_fetcher import (
    PRACTICE_QUESTRADE_TOKEN_URL,
    QuestradeDataFetcher,
)
from investment_strategies.questrade_optionsplay_sandbox import ensure_practice_token_url


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Execute options strategy decisions in Alpaca paper trading."
    )
    parser.add_argument(
        "--submit",
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
    args = parser.parse_args()

    # Use Questrade for data fetch/scan, Alpaca for order execution.
    questrade_fetcher = QuestradeDataFetcher()
    if not ensure_practice_token_url(questrade_fetcher):
        print(
            "Warning: credentials token_url is not set to practice login.\n"
            f"Expected: {PRACTICE_QUESTRADE_TOKEN_URL}\n"
            "Set token_url in questrade_credentials.json to force practice auth."
        )

    alpaca_fetcher = AlpacaDataFetcher()
    account = alpaca_fetcher.ensure_paper_account()
    print(
        f"Connected to Alpaca paper account {account.get('account_number')} "
        f"(status={account.get('status')})."
    )

    strategy = OptionsBuyStrategy(questrade_fetcher)
    executor = AlpacaOptionsPlaySandboxExecutor(alpaca_fetcher)

    opportunities = strategy.find_best_opportunities(args.symbols)
    if not opportunities:
        print("No opportunities found.")
        return

    print(f"Found {len(opportunities)} opportunities. Executing top {args.max_orders}.")
    for idx, opp in enumerate(opportunities[: args.max_orders], start=1):
        result = executor.execute_opportunity(
            opp,
            quantity=1,
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


if __name__ == "__main__":
    main()
