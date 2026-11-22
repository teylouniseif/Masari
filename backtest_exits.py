"""
Exit-rule backtest for the options bot, using the trades it actually made at Alpaca.

Every real entry (same contract, same fill price, same time) is replayed against
Alpaca's historical HOURLY option bars under several exit rule sets, so you can
see how each set would have done on identical trades. Nothing in the live
strategy is used or changed; this file is standalone and read-only (GET only).

Fill model: an exit triggers when an hourly bar's close crosses a level, and
fills at that close minus --slippage (default 2%), a stand-in for the bid/ask
spread. Stock-based rules use daily closes known BEFORE the bar's day (no
look-ahead). Trades still open at the end of the data are marked to the last
bar and flagged as open.

Usage:
  python backtest_exits.py --since 2026-10-19
  python backtest_exits.py --since 2026-10-19 --detail      # per-trade grid
  python backtest_exits.py --slippage 0.04
"""

from __future__ import annotations

import argparse
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import requests

from backtest_technical_entry import (
    TechnicalEntryFilter,
    Trade,
    _ts,
    load_closes,
    load_trades,
)
from investment_strategies.alpaca_data_fetcher import AlpacaDataFetcher


# --------------------------------------------------------------------------- #
# Rule sets
# --------------------------------------------------------------------------- #
@dataclass
class ExitRules:
    name: str
    description: str
    stop_loss: Optional[float] = None        # exit if price <= entry * stop_loss
    profit_target: Optional[float] = None    # exit all if price >= entry * profit_target
    time_exit_dte: Optional[int] = None      # exit when calendar days to expiry <= this
    scale_out_at: Optional[float] = None     # sell `scale_out_fraction` at entry * this
    scale_out_fraction: float = 0.5
    trail_from_peak: Optional[float] = None  # after scale-out, exit rest if price <= peak*(1-x)
    stock_sma_stop: Optional[int] = None     # exit when stock closes through this SMA vs position
    min_hold_days: int = 0                   # stock/option stops ignored before this


RULE_SETS: List[ExitRules] = [
    ExitRules(
        "live_now",
        "Live bot since Oct 14 (price part only): -50% stop, exit at 5 DTE, no profit "
        "target (its technical and Greek exits are not modelled)",
        stop_loss=0.5, time_exit_dte=5,
    ),
    ExitRules(
        "target_2x",
        "Sep v1 levels: 2x target, -50% stop, exit at 5 DTE (no technical exits)",
        stop_loss=0.5, profit_target=2.0, time_exit_dte=5,
    ),
    ExitRules(
        "wide_stop",
        "Same, but -70% stop and exit at 10 DTE",
        stop_loss=0.3, profit_target=2.0, time_exit_dte=10,
    ),
    ExitRules(
        "stock_stop",
        "Exit when stock closes through its 20-day SMA against us; +100% target; "
        "-80% disaster stop; exit at 10 DTE",
        stop_loss=0.2, profit_target=2.0, time_exit_dte=10, stock_sma_stop=20,
    ),
    ExitRules(
        "scale_trail",
        "-50% stop; sell half at +50%, trail the rest 25% off its peak; exit at 10 DTE",
        stop_loss=0.5, time_exit_dte=10, scale_out_at=1.5, trail_from_peak=0.25,
    ),
    ExitRules(
        "stock_scale",
        "Stock 20-SMA stop + -80% disaster stop; half at +50%, trail rest 25%; exit at 10 DTE",
        stop_loss=0.2, time_exit_dte=10, scale_out_at=1.5, trail_from_peak=0.25,
        stock_sma_stop=20,
    ),
    ExitRules(
        "no_stop",
        "No stop at all: 2x target or exit at 10 DTE (shows what the stops cost/save)",
        profit_target=2.0, time_exit_dte=10,
    ),
]


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
Bar = Tuple[datetime, float]


def with_retries(call: Callable, attempts: int = 4):
    for n in range(attempts):
        try:
            return call()
        except (requests.Timeout, requests.ConnectionError):
            if n == attempts - 1:
                raise
            time.sleep(2 ** n)


def load_option_bars(fetcher: AlpacaDataFetcher, symbol: str, start: datetime,
                     end: datetime) -> List[Bar]:
    bars, token = [], None
    while True:
        params = {
            "symbols": symbol, "timeframe": "1Hour", "limit": 10000,
            "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if token:
            params["page_token"] = token
        data = with_retries(lambda: fetcher._data_request("GET", "v1beta1/options/bars",
                                                          params=params))
        bars += [(_ts(b["t"]), float(b["c"])) for b in data.get("bars", {}).get(symbol, [])]
        token = data.get("next_page_token")
        if not token:
            return bars


# --------------------------------------------------------------------------- #
# Simulation
# --------------------------------------------------------------------------- #
@dataclass
class SimResult:
    pnl: float
    reason: str
    exit_time: Optional[datetime]
    still_open: bool = False


def stock_against_position(closes: List[float], sma_period: int, option_type: str) -> bool:
    if len(closes) < sma_period:
        return False
    avg = float(np.mean(closes[-sma_period:]))
    return closes[-1] < avg if option_type == "call" else closes[-1] > avg


def simulate(trade: Trade, bars: List[Bar], stock_daily: List[Tuple[date, float]],
             rules: ExitRules, slippage: float) -> SimResult:
    entry, qty = trade.entry_price, trade.qty
    remaining, realized = 1.0, 0.0      # fraction of position still held, $ banked
    peak, scaled = entry, False
    reasons = []

    def fill(price: float) -> float:
        return price * (1 - slippage)

    def close_rest(price: float, why: str, when: datetime) -> SimResult:
        nonlocal realized
        realized += (fill(price) - entry) * qty * 100 * remaining
        reasons.append(why)
        return SimResult(realized, " + ".join(reasons), when)

    for when, price in bars:
        days_held = (when.date() - trade.entry_time.date()).days
        dte = (trade.expiry - when.date()).days
        peak = max(peak, price)
        can_stop = days_held >= rules.min_hold_days

        if rules.time_exit_dte is not None and dte <= rules.time_exit_dte:
            return close_rest(price, "time", when)
        if rules.profit_target and price >= entry * rules.profit_target:
            return close_rest(price, "target", when)
        if can_stop and rules.stop_loss and price <= entry * rules.stop_loss:
            return close_rest(price, "stop", when)
        if can_stop and rules.stock_sma_stop:
            known = [c for d, c in stock_daily if d < when.date()]
            entry_known = [c for d, c in stock_daily if d < trade.entry_time.date()]
            # Only fire on a fresh cross after entry, not a condition that already held.
            if (stock_against_position(known, rules.stock_sma_stop, trade.option_type)
                    and len(known) > len(entry_known)):
                return close_rest(price, "stock<SMA" if trade.option_type == "call"
                                  else "stock>SMA", when)
        if rules.scale_out_at and not scaled and price >= entry * rules.scale_out_at:
            sold = rules.scale_out_fraction
            realized += (fill(price) - entry) * qty * 100 * sold
            remaining -= sold
            scaled = True
            peak = price
            reasons.append(f"half@+{rules.scale_out_at - 1:.0%}")
            continue
        if scaled and rules.trail_from_peak and price <= peak * (1 - rules.trail_from_peak):
            return close_rest(price, "trail", when)

    # Ran out of bars: expired (last print before expiry) or still open today.
    if not bars:
        return SimResult(0.0, "no data", None, still_open=True)
    last_time, last_price = bars[-1]
    expired = trade.expiry <= datetime.now(timezone.utc).date()
    res = close_rest(last_price, "expiry" if expired else "open", last_time)
    res.still_open = not expired
    return res


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def summarize(label: str, trades: List[Trade], results: Dict[str, List[SimResult]],
              idx: List[int]) -> None:
    actual = [trades[i].pnl or 0.0 for i in idx]
    print(f"\n{label} ({len(idx)} trades)")
    hdr = f"  {'Rule set':<12} {'P&L':>9} {'Wins':>5} {'Avg win':>8} {'Avg loss':>9} {'Held d':>7}  Exit reasons"
    print(hdr)
    print("  " + "-" * (len(hdr) + 18))

    def line(name, pnls, reasons, held):
        w = [p for p in pnls if p > 0]
        l = [p for p in pnls if p <= 0]
        print(f"  {name:<12} {sum(pnls):>+9,.0f} {len(w):>5} {np.mean(w) if w else 0:>+8.0f} "
              f"{np.mean(l) if l else 0:>+9.0f} {held:>7}  {reasons}")

    line("ACTUAL", actual, "what really happened (incl. bot's technical exits & real fills)", "")
    for rules in RULE_SETS:
        rs = [results[rules.name][i] for i in idx]
        held = [(r.exit_time.date() - trades[i].entry_time.date()).days
                for r, i in zip(rs, idx) if r.exit_time]
        top = Counter(r.reason.split(" + ")[-1] for r in rs).most_common(4)
        line(rules.name, [r.pnl for r in rs], ", ".join(f"{k} {v}" for k, v in top),
             f"{np.mean(held):.1f}" if held else "-")


def run(since: date, slippage: float, detail: bool) -> None:
    fetcher = AlpacaDataFetcher()
    trades = sorted(load_trades(fetcher, since), key=lambda t: t.entry_time)
    if not trades:
        print(f"No option trades bought since {since}.")
        return

    now = datetime.now(timezone.utc)
    first = min(t.entry_time.date() for t in trades) - timedelta(days=150)
    stock = {u: with_retries(lambda u=u: load_closes(fetcher, u, first, now.date())) for u in {t.underlying for t in trades}}

    results: Dict[str, List[SimResult]] = {r.name: [] for r in RULE_SETS}
    flt = TechnicalEntryFilter()
    taken_idx = []
    for i, t in enumerate(trades):
        # The free data plan refuses the most recent 15 minutes (403), so stop short of now.
        end = min(now - timedelta(minutes=20), datetime.combine(t.expiry, datetime.min.time(), timezone.utc)
                  + timedelta(hours=21))
        # Bars that START after the fill, so the entry hour's earlier prints are excluded.
        bars = [b for b in load_option_bars(fetcher, t.option_symbol, t.entry_time, end)
                if b[0] > t.entry_time]
        for rules in RULE_SETS:
            results[rules.name].append(simulate(t, bars, stock[t.underlying], rules, slippage))
        pre_entry = [c for d, c in stock[t.underlying] if d < t.entry_time.date()]
        if flt.evaluate(pre_entry, t.option_type).take:
            taken_idx.append(i)

    if detail:
        names = [r.name for r in RULE_SETS]
        print(f"{'#':>2} {'Bought':<6} {'Contract':<22} {'ACTUAL':>7} " +
              " ".join(f"{n:>12}" for n in names))
        for i, t in enumerate(trades):
            c = f"{t.underlying} {t.option_type[0].upper()} {t.strike:g} {t.expiry:%m-%d}"
            act = f"{t.pnl:+.0f}" if t.pnl is not None else "-"
            cells = []
            for n in names:
                r = results[n][i]
                cells.append(f"{r.pnl:+.0f}{'*' if r.still_open else ''} {r.reason.split(' + ')[-1][:5]}")
            print(f"{i + 1:>2} {t.entry_time:%m-%d} {c:<22} {act:>7} " +
                  " ".join(f"{x:>12}" for x in cells))
        print("* = still open, marked to last hourly bar")

    print(f"\nSlippage assumed on simulated exits: {slippage:.0%} below the bar close.")
    for r in RULE_SETS:
        print(f"  {r.name:<12} {r.description}")
    summarize("ALL TRADES", trades, results, list(range(len(trades))))
    summarize("ONLY TRADES THE TECHNICAL ENTRY FILTER WOULD TAKE", trades, results, taken_idx)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--since", type=date.fromisoformat,
                   default=(datetime.now(timezone.utc) - timedelta(days=30)).date())
    p.add_argument("--slippage", type=float, default=0.02,
                   help="Fraction knocked off each simulated exit price (default 0.02).")
    p.add_argument("--detail", action="store_true", help="Print the per-trade grid.")
    a = p.parse_args()
    run(a.since, a.slippage, a.detail)


if __name__ == "__main__":
    main()
