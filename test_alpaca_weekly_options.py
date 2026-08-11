#!/usr/bin/env python3
"""
Test script to verify Alpaca weekly options utilities.

Usage:
  python3 test_alpaca_weekly_options.py
  python3 test_alpaca_weekly_options.py --symbol AAPL --days 7
"""

import argparse
import json
from pathlib import Path

from investment_strategies.alpaca_data_fetcher import AlpacaDataFetcher

GREEKS = ("delta", "gamma", "theta", "vega", "rho")


def test_alpaca_weekly_options(symbol: str, days: int) -> bool:
    try:
        fetcher = AlpacaDataFetcher()
        account = fetcher.ensure_paper_account()
        print("✓ Alpaca data fetcher initialized")
        print(
            f"  Paper account: {account.get('account_number')} "
            f"(status={account.get('status')})"
        )

        print(f"\nFetching weekly option window for {symbol}...")
        weekly = fetcher.get_weekly_option_window(
            underlying_symbol=symbol,
            days=days,
        )

        contracts = weekly.get("contracts", [])
        snapshots = weekly.get("snapshots", {})

        print("✓ Weekly option window retrieved")
        print(f"  Window : {weekly.get('start_date')} -> {weekly.get('end_date')}")
        print(f"  Contracts : {len(contracts)}")
        print(f"  Snapshots : {len(snapshots)}")

        if not contracts:
            print("✗ No option contracts returned for the requested weekly window")
            return False

        # ── save raw API response to JSON ────────────────────────────────────
        output_path = Path(f"{symbol.lower()}_weekly_options_raw_api.json")
        with output_path.open("w", encoding="utf-8") as fh:
            json.dump(
                {
                    "requested_underlying": symbol,
                    "start_date": weekly.get("start_date"),
                    "end_date": weekly.get("end_date"),
                    "contracts_response": {"option_contracts": contracts},
                    "snapshots_response": snapshots,
                },
                fh,
                indent=2,
            )
        print(f"\n✓ Raw API response saved to {output_path}")

        # ── normalize through the chain builder ──────────────────────────────
        normalized = fetcher._normalize_option_snapshots(snapshots)
        chain = normalized.get("optionChain", [])
        print(f"\n✓ Normalized option chain: {len(chain)} expiry bucket(s)")

        # Flatten all strike entries across all expiries
        all_entries: list = []
        for expiry_bucket in chain:
            for root in expiry_bucket.get("chainPerRoot", []):
                all_entries.extend(root.get("chainPerStrikePrice", []))

        print(f"  Total strike rows : {len(all_entries)}")

        # ── verify IV fix ────────────────────────────────────────────────────
        # Find a snapshot that actually has impliedVolatility from the raw data
        raw_iv_symbols = [
            sym for sym, snap in snapshots.items() if snap.get("impliedVolatility")
        ]
        iv_ok = False
        if raw_iv_symbols:
            # Check that at least one normalized entry carries a non-zero IV
            for entry in all_entries:
                for side in ("call", "put"):
                    opt = entry.get(side, {})
                    if opt.get("impliedVolatility"):
                        iv_ok = True
                        break
                if iv_ok:
                    break
            if iv_ok:
                print("\n✓ impliedVolatility fix confirmed — IV values are present")
            else:
                print("\n✗ impliedVolatility fix FAILED — all IV values are zero/missing")
                return False
        else:
            print("\n~ No IV in raw snapshots for this window; IV check skipped")

        # ── verify Greeks fix ────────────────────────────────────────────────
        raw_greeks_symbols = [
            sym for sym, snap in snapshots.items() if snap.get("greeks")
        ]
        greeks_ok = False
        if raw_greeks_symbols:
            for entry in all_entries:
                for side in ("call", "put"):
                    opt = entry.get(side, {})
                    if any(opt.get(g) is not None for g in GREEKS):
                        greeks_ok = True
                        break
                if greeks_ok:
                    break
            if greeks_ok:
                print("✓ Greeks fix confirmed — Greek values are present")
            else:
                print("✗ Greeks fix FAILED — all Greek values are None/missing")
                return False
        else:
            print("~ No greeks in raw snapshots for this window; Greeks check skipped")

        # ── print a sample entry with the new fields ─────────────────────────
        sample_contract = contracts[0]
        sample_symbol = sample_contract.get("symbol", "")
        print(f"\nSample contract : {sample_symbol}")
        print(f"  Expiration    : {sample_contract.get('expiration_date')}")
        print(f"  Strike        : {sample_contract.get('strike_price')}")
        print(f"  Type          : {sample_contract.get('type')}")

        # Find the normalized entry for the sample symbol
        parsed = fetcher._parse_option_symbol(sample_symbol)
        if parsed and snapshots.get(sample_symbol):
            snap = snapshots[sample_symbol]
            quote = snap.get("latestQuote", {})
            raw_greeks = snap.get("greeks", {})
            print("\nSample snapshot (raw):")
            print(f"  Bid              : {quote.get('bp')}")
            print(f"  Ask              : {quote.get('ap')}")
            print(f"  impliedVolatility: {snap.get('impliedVolatility')}")
            for g in GREEKS:
                print(f"  {g:<5}            : {raw_greeks.get(g)}")

            # Find matching normalized entry
            for entry in all_entries:
                side = parsed["type"]
                opt = entry.get(side, {})
                if opt.get("symbol") == sample_symbol:
                    print("\nSample normalized option:")
                    print(f"  bid              : {opt.get('bid')}")
                    print(f"  ask              : {opt.get('ask')}")
                    print(f"  impliedVolatility: {opt.get('impliedVolatility')}")
                    for g in GREEKS:
                        print(f"  {g:<5}            : {opt.get(g)}")
                    break

        # ── summary of Greeks coverage across the chain ──────────────────────
        entries_with_greeks = 0
        entries_with_iv = 0
        total_sides = 0
        for entry in all_entries:
            for side in ("call", "put"):
                opt = entry.get(side)
                if not opt:
                    continue
                total_sides += 1
                if opt.get("impliedVolatility"):
                    entries_with_iv += 1
                if any(opt.get(g) is not None for g in GREEKS):
                    entries_with_greeks += 1

        print(f"\nCoverage across {total_sides} option sides:")
        print(f"  With IV     : {entries_with_iv}")
        print(f"  With Greeks : {entries_with_greeks}")

        # ── option bars ──────────────────────────────────────────────────────
        print("\nFetching 1-hour option bars for up to 10 contracts...")
        sample_symbols = [c["symbol"] for c in contracts[:10] if c.get("symbol")]
        bars_path = Path(f"{symbol.lower()}_weekly_options_bars.json")
        try:
            bars = fetcher.get_option_bars(sample_symbols, timeframe="1Hour")
            bars_found = {sym: b for sym, b in bars.items() if b}
            print(f"  Symbols requested : {len(sample_symbols)}")
            print(f"  Symbols with bars : {len(bars_found)}")

            with bars_path.open("w", encoding="utf-8") as fh:
                json.dump(bars, fh, indent=2)
            print(f"✓ Option bars saved to {bars_path}")

            if bars_found:
                sample_sym, sample_bars = next(iter(bars_found.items()))
                print(f"\nSample bars for {sample_sym} ({len(sample_bars)} bar(s)):")
                for bar in sample_bars[:3]:
                    print(
                        f"  {bar['timestamp']}  O={bar['open']}  H={bar['high']}"
                        f"  L={bar['low']}  C={bar['close']}  V={bar['volume']}"
                    )
            else:
                print("~ No bars returned (contracts may not have traded in the last 24h)")
        except PermissionError as exc:
            print(f"~ Option bars skipped: {exc}")
            with bars_path.open("w", encoding="utf-8") as fh:
                json.dump({"error": str(exc), "symbols_requested": sample_symbols}, fh, indent=2)
            print(f"✓ Bars error note saved to {bars_path}")

        print("\n✓ All checks passed.")
        return True

    except Exception as exc:
        print(f"✗ Error during weekly options test: {exc}")
        import traceback
        traceback.print_exc()
        return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test Alpaca weekly options utility functions."
    )
    parser.add_argument("--symbol", default="AAPL", help="Underlying symbol to inspect.")
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of calendar days in the weekly options window.",
    )
    args = parser.parse_args()

    print("Alpaca Weekly Options Test")
    print("=" * 40)

    success = test_alpaca_weekly_options(symbol=args.symbol, days=args.days)
    if not success:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
