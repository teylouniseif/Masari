"""
Combined backtest: technical entry filter + reworked exits, over the account's
ENTIRE Alpaca trade history.

Scenarios, all on the same real entries (contract, fill price, time):
  ACTUAL         what really happened
  filter only    entry filter applied, actual exits kept for the trades it takes
  exits only     every trade, exits replayed under each reworked rule set
  filter + exits entry filter, then reworked exits  <- the proposed strategy
  SPY put gate   puts only taken while SPY closed below its --spy-sma-day
                 average the day before entry (calls untouched): the
                 market-regime gate suggested in AGENTS.md section 10

Exits are replayed on hourly option bars and filled at the bar close minus
--slippage, which models limit orders near the mid instead of market orders at
the open (see backtest_exits.py). The entry filter only sees daily stock bars
from before the entry day (see backtest_technical_entry.py).

The filter and the exit rules were designed after looking at trades from
--design-start onward, so results are split into an out-of-sample period
(before that date) and the in-sample period (from that date).

Read-only: GET requests to Alpaca only. Nothing in investment_strategies/ is changed.

Usage:
  python backtest_combined.py
  python backtest_combined.py --slippage 0.05
  python backtest_combined.py --detail
  python backtest_combined.py --spy-sma 20
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

import numpy as np

from backtest_exits import RULE_SETS, SimResult, load_option_bars, simulate, with_retries
from backtest_technical_entry import (
    TechnicalEntryFilter,
    Trade,
    _ts,
    load_closes,
    parse_option_symbol,
)
from investment_strategies.alpaca_data_fetcher import AlpacaDataFetcher

# Exit rule sets worth comparing once execution is fixed (from backtest_exits.py).
CANDIDATE_RULES = ["live_now", "target_2x", "stock_stop", "scale_trail", "stock_scale"]
# Rule sets shown with the SPY put gate (kept short so the report stays readable).
SPY_RULES = ["live_now", "target_2x"]


def spy_below_sma(spy: List[tuple], day: date, sma: int) -> Optional[bool]:
    """True if SPY's last close before `day` was below its `sma`-day average."""
    closes = [c for d, c in spy if d < day]
    if len(closes) < sma:
        return None
    return closes[-1] < float(np.mean(closes[-sma:]))


# --------------------------------------------------------------------------- #
# Full trade history
# --------------------------------------------------------------------------- #
def load_all_orders(fetcher: AlpacaDataFetcher) -> List[dict]:
    seen, orders, after = set(), [], "2015-01-01T00:00:00Z"
    while True:
        page = with_retries(lambda: fetcher._request(
            "GET", "v2/orders",
            params={"status": "all", "after": after, "limit": 500, "direction": "asc"}))
        new = [o for o in page if o["id"] not in seen]
        if not new:
            return orders
        seen.update(o["id"] for o in new)
        orders += new
        after = page[-1]["submitted_at"]


def pair_trades(fetcher: AlpacaDataFetcher, orders: List[dict]):
    filled = [o for o in orders
              if o["status"] == "filled" and float(o.get("filled_qty") or 0) > 0]
    filled.sort(key=lambda o: o["filled_at"] or o["submitted_at"])
    open_by_symbol: Dict[str, Trade] = {}
    trades: List[Trade] = []
    orphan_sells = 0
    for o in filled:
        parsed = parse_option_symbol(o["symbol"])
        if not parsed:
            continue
        und, expiry, typ, strike = parsed
        qty, price = int(float(o["filled_qty"])), float(o["filled_avg_price"])
        when = _ts(o["filled_at"] or o["submitted_at"])
        if o["side"] == "buy":
            t = Trade(o["symbol"], und, typ, strike, expiry, qty, when, price)
            open_by_symbol[o["symbol"]] = t
            trades.append(t)
        elif o["symbol"] in open_by_symbol:
            t = open_by_symbol.pop(o["symbol"])
            t.exit_time, t.exit_price = when, price
        else:
            orphan_sells += 1

    positions = {p["symbol"]: p for p in fetcher.get_positions()}
    expired_worthless = 0
    today = datetime.now(timezone.utc).date()
    for sym, t in open_by_symbol.items():
        if sym in positions:
            t.unrealized_pnl = float(positions[sym]["unrealized_pl"])
        elif t.expiry < today:
            # Bought, never sold, no longer held: expired (worthless if OTM).
            t.exit_time = datetime.combine(t.expiry, datetime.min.time(), timezone.utc)
            t.exit_price = 0.0
            expired_worthless += 1
    return trades, orphan_sells, expired_worthless


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
@dataclass
class Row:
    trade: Trade
    taken: bool
    sims: Dict[str, SimResult]
    spy_down: Optional[bool] = None  # SPY below its SMA on the last close before entry

    @property
    def spy_ok(self) -> bool:
        """Passes the SPY put gate: calls always, puts only in a market downtrend."""
        return self.trade.option_type == "call" or bool(self.spy_down)


def stats(pnls: List[float]) -> str:
    if not pnls:
        return f"{'0':>4} {'-':>9} {'-':>6} {'-':>9}"
    wins = sum(p > 0 for p in pnls)
    return (f"{len(pnls):>4} {sum(pnls):>+9,.0f} {wins / len(pnls):>6.0%} "
            f"{np.mean(pnls):>+9.0f}")


def report(title: str, rows: List[Row]) -> None:
    print(f"\n{title}")
    print(f"  {'Scenario':<34} {'N':>4} {'P&L':>9} {'Win %':>6} {'Avg/trade':>9}")
    print("  " + "-" * 66)
    actual = lambda rs: [r.trade.pnl for r in rs if r.trade.pnl is not None]
    print(f"  {'ACTUAL (as traded)':<34} {stats(actual(rows))}")
    print(f"  {'Entry filter only (actual exits)':<34} {stats(actual([r for r in rows if r.taken]))}")
    for name in CANDIDATE_RULES:
        print(f"  {'Exits only: ' + name:<34} {stats([r.sims[name].pnl for r in rows])}")
    for name in CANDIDATE_RULES:
        print(f"  {'FILTER + EXITS: ' + name:<34} "
              f"{stats([r.sims[name].pnl for r in rows if r.taken])}")
    print(f"  {'SPY put gate (actual exits)':<34} {stats(actual([r for r in rows if r.spy_ok]))}")
    for name in SPY_RULES:
        print(f"  {'SPY gate + exits: ' + name:<34} "
              f"{stats([r.sims[name].pnl for r in rows if r.spy_ok])}")
    for name in SPY_RULES:
        print(f"  {'FILTER + SPY gate: ' + name:<34} "
              f"{stats([r.sims[name].pnl for r in rows if r.taken and r.spy_ok])}")


def puts_by_market(rows: List[Row], sma: int) -> None:
    """Puts split by SPY regime at entry, with and without the stock-trend filter."""
    puts = [r for r in rows if r.trade.option_type == "put"]
    print(f"\nPUTS BY MARKET REGIME (SPY vs its {sma}-day SMA, last close before entry)")
    print(f"  {'Group':<40} {'N':>4} {'P&L':>9} {'Win %':>6} {'Avg/trade':>9}")
    print("  " + "-" * 72)
    for label, down in (("SPY below SMA", True), ("SPY above SMA", False)):
        grp = [r for r in puts if bool(r.spy_down) == down]
        print(f"  {label + ': actual':<40} "
              f"{stats([r.trade.pnl for r in grp if r.trade.pnl is not None])}")
        for name in SPY_RULES:
            print(f"  {label + ': ' + name:<40} {stats([r.sims[name].pnl for r in grp])}")
            print(f"  {label + ': filter + ' + name:<40} "
                  f"{stats([r.sims[name].pnl for r in grp if r.taken])}")


def monthly(rows: List[Row], rule: str) -> None:
    months = sorted({r.trade.entry_time.strftime("%Y-%m") for r in rows})
    print(f"\nBy entry month (filter + exits: {rule})")
    print(f"  {'Month':<8} {'Actual N':>8} {'Actual P&L':>11} {'Taken':>6} {'New P&L':>9} {'Skipped P&L':>12}")
    for m in months:
        rs = [r for r in rows if r.trade.entry_time.strftime("%Y-%m") == m]
        taken = [r for r in rs if r.taken]
        act = sum(r.trade.pnl or 0 for r in rs)
        new = sum(r.sims[rule].pnl for r in taken)
        skipped = sum(r.trade.pnl or 0 for r in rs if not r.taken)
        print(f"  {m:<8} {len(rs):>8} {act:>+11,.0f} {len(taken):>6} {new:>+9,.0f} {skipped:>+12,.0f}")


def run(slippage: float, design_start: date, detail: bool, spy_sma: int = 50) -> None:
    fetcher = AlpacaDataFetcher()
    trades, orphans, expired = pair_trades(fetcher, load_all_orders(fetcher))
    trades.sort(key=lambda t: t.entry_time)
    if not trades:
        print("No option trades in this account.")
        return

    now = datetime.now(timezone.utc)
    first = min(t.entry_time.date() for t in trades) - timedelta(days=150)
    stock = {u: with_retries(lambda u=u: load_closes(fetcher, u, first, now.date()))
             for u in {t.underlying for t in trades}}
    spy = with_retries(lambda: load_closes(fetcher, "SPY", first, now.date()))

    rules = {r.name: r for r in RULE_SETS}
    flt = TechnicalEntryFilter()
    rows: List[Row] = []
    for t in trades:
        end = min(now - timedelta(minutes=20),
                  datetime.combine(t.expiry, datetime.min.time(), timezone.utc)
                  + timedelta(hours=21))
        bars = [b for b in with_retries(
                    lambda: load_option_bars(fetcher, t.option_symbol, t.entry_time, end))
                if b[0] > t.entry_time]
        pre_entry = [c for d, c in stock[t.underlying] if d < t.entry_time.date()]
        rows.append(Row(
            trade=t,
            taken=flt.evaluate(pre_entry, t.option_type).take,
            sims={n: simulate(t, bars, stock[t.underlying], rules[n], slippage)
                  for n in CANDIDATE_RULES},
            spy_down=spy_below_sma(spy, t.entry_time.date(), spy_sma),
        ))

    span = f"{trades[0].entry_time:%Y-%m-%d} to {trades[-1].entry_time:%Y-%m-%d}"
    print(f"Full account history: {len(trades)} trades bought {span}")
    print(f"  still open: {sum(t.exit_price is None for t in trades)}, "
          f"expired unsold (counted at $0): {expired}, sells with no matching buy: {orphans}")
    print(f"  simulated exits filled {slippage:.0%} below the hourly close")
    known = [r for r in rows if r.spy_down is not None]
    print(f"  SPY below its {spy_sma}-day SMA at {sum(r.spy_down for r in known)} of "
          f"{len(known)} entries; the SPY put gate blocks "
          f"{sum(not r.spy_ok for r in rows)} of "
          f"{sum(r.trade.option_type == 'put' for r in rows)} puts")

    if detail:
        print(f"\n{'#':>3} {'Bought':<10} {'Contract':<22} {'Filter':<6} {'SPY':<4} {'Actual':>7} " +
              " ".join(f"{n:>12}" for n in CANDIDATE_RULES))
        for i, r in enumerate(rows, 1):
            t = r.trade
            c = f"{t.underlying} {t.option_type[0].upper()} {t.strike:g} {t.expiry:%m-%d}"
            act = f"{t.pnl:+.0f}" if t.pnl is not None else "-"
            cells = " ".join(
                f"{r.sims[n].pnl:>+7.0f}{'*' if r.sims[n].still_open else ' '}"
                f"{r.sims[n].reason.split(' + ')[-1][:4]:>4}" for n in CANDIDATE_RULES)
            spy_col = "-" if r.spy_down is None else ("dn" if r.spy_down else "up")
            print(f"{i:>3} {t.entry_time:%Y-%m-%d} {c:<22} {'TAKE' if r.taken else 'skip':<6} "
                  f"{spy_col:<4} {act:>7} {cells}")
        print("* = still open, marked to the last hourly bar")

    before = [r for r in rows if r.trade.entry_time.date() < design_start]
    after = [r for r in rows if r.trade.entry_time.date() >= design_start]
    report(f"ENTIRE HISTORY ({len(rows)} trades)", rows)
    if before:
        report(f"OUT-OF-SAMPLE: bought before {design_start} ({len(before)} trades; "
               "rules were NOT tuned on these)", before)
    if after:
        report(f"IN-SAMPLE: bought from {design_start} ({len(after)} trades; "
               "rules were designed looking at these)", after)

    puts_by_market(rows, spy_sma)

    best = max(CANDIDATE_RULES, key=lambda n: sum(r.sims[n].pnl for r in rows if r.taken))
    monthly(rows, best)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--slippage", type=float, default=0.02)
    p.add_argument("--design-start", type=date.fromisoformat, default=date(2026, 10, 19),
                   help="First entry date the rules were designed on (default 2026-10-19).")
    p.add_argument("--detail", action="store_true", help="Print every trade.")
    p.add_argument("--spy-sma", type=int, default=50,
                   help="SMA length for the SPY put gate (default 50).")
    a = p.parse_args()
    run(a.slippage, a.design_start, a.detail, a.spy_sma)


if __name__ == "__main__":
    main()
