"""
Prototype: add technical analysis to the options BUY decision, and backtest it
against the trades the bot actually made at Alpaca.

This file is standalone on purpose: it does not modify OptionsBuyStrategy. It
answers "if this technical filter had been in place, which of the real trades
would the bot have taken, skipped, or flipped to the other direction?"

For each real trade it rebuilds the stock's daily chart using only bars from
BEFORE the entry day (no look-ahead), then scores the trade two ways:

  1. TechnicalEntryFilter (new): trend + momentum + MACD + RSI, with a gate.
  2. The RSI + momentum points of the bot's _calculate_opportunity_score: what the
     live bot used for every entry before the Oct 10 breakout regime, and still
     uses for range (non-breakout) entries.

Usage:
  python backtest_technical_entry.py                    # trades bought in the last 30 days
  python backtest_technical_entry.py --since 2026-10-19
  python backtest_technical_entry.py --min-score 3

Read-only: it only sends GET requests to Alpaca (orders, positions, stock bars).
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from investment_strategies.alpaca_data_fetcher import AlpacaDataFetcher

OPTION_SYMBOL_RE = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")


# --------------------------------------------------------------------------- #
# Indicators
# --------------------------------------------------------------------------- #
def sma(values: Sequence[float], period: int) -> float:
    return float(np.mean(values[-period:]))


def ema_series(values: Sequence[float], period: int) -> np.ndarray:
    alpha = 2 / (period + 1)
    out = np.empty(len(values))
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def rsi(values: Sequence[float], period: int = 14) -> float:
    """Wilder's RSI (smoothed), the standard definition."""
    if len(values) < period + 1:
        return 50.0
    deltas = np.diff(values)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
    if avg_loss == 0:
        return 100.0
    return float(100 - 100 / (1 + avg_gain / avg_loss))


def macd_histogram(values: Sequence[float]) -> float:
    macd = ema_series(values, 12) - ema_series(values, 26)
    signal = ema_series(macd, 9)
    return float(macd[-1] - signal[-1])


def pct_change(values: Sequence[float], period: int) -> float:
    return float(values[-1] / values[-period - 1] - 1)


# --------------------------------------------------------------------------- #
# New technical entry filter
# --------------------------------------------------------------------------- #
@dataclass
class TechnicalEntryConfig:
    min_bars: int = 60
    fast_sma: int = 20
    slow_sma: int = 50
    momentum_period: int = 20
    rsi_period: int = 14
    # Calls: want strength that isn't already stretched. Puts: the mirror.
    call_rsi_range: tuple = (45.0, 70.0)
    put_rsi_range: tuple = (30.0, 55.0)
    min_score: int = 4  # out of 5 signals
    require_trend: bool = True  # trend signal is mandatory, not just one vote


@dataclass
class TechnicalDecision:
    option_type: str
    take: bool
    score: int
    signals: Dict[str, bool] = field(default_factory=dict)
    reason: str = ""


class TechnicalEntryFilter:
    """Scores a call or put against the underlying's daily closes."""

    def __init__(self, config: Optional[TechnicalEntryConfig] = None):
        self.config = config or TechnicalEntryConfig()

    def indicators(self, closes: Sequence[float]) -> Dict[str, float]:
        c = self.config
        return {
            "close": float(closes[-1]),
            "sma_fast": sma(closes, c.fast_sma),
            "sma_slow": sma(closes, c.slow_sma),
            "rsi": rsi(closes, c.rsi_period),
            "momentum": pct_change(closes, c.momentum_period),
            "macd_hist": macd_histogram(closes),
        }

    def evaluate(self, closes: Sequence[float], option_type: str) -> TechnicalDecision:
        c = self.config
        option_type = option_type.lower()
        if len(closes) < c.min_bars:
            return TechnicalDecision(option_type, False, 0, reason=f"only {len(closes)} bars")

        ind = self.indicators(closes)
        bullish = option_type == "call"
        sign = 1 if bullish else -1
        lo, hi = c.call_rsi_range if bullish else c.put_rsi_range

        signals = {
            "trend": sign * (ind["sma_fast"] - ind["sma_slow"]) > 0,
            "price_vs_sma": sign * (ind["close"] - ind["sma_fast"]) > 0,
            "momentum": sign * ind["momentum"] > 0,
            "macd": sign * ind["macd_hist"] > 0,
            "rsi_zone": lo <= ind["rsi"] <= hi,
        }
        score = sum(signals.values())
        failed = [k for k, ok in signals.items() if not ok]

        if c.require_trend and not signals["trend"]:
            return TechnicalDecision(option_type, False, score, signals, "against trend")
        if score < c.min_score:
            return TechnicalDecision(
                option_type, False, score, signals, "weak: " + ",".join(failed)
            )
        return TechnicalDecision(option_type, True, score, signals, "ok")

    def best_direction(self, closes: Sequence[float]) -> Optional[str]:
        """Which side, if any, the filter would have bought."""
        call, put = self.evaluate(closes, "call"), self.evaluate(closes, "put")
        if call.take and (not put.take or call.score >= put.score):
            return "call"
        if put.take:
            return "put"
        return None


# --------------------------------------------------------------------------- #
# Bot's RSI + momentum scoring (range entries; all entries before Oct 10)
# --------------------------------------------------------------------------- #
def strategy_rsi(prices: Sequence[float], period: int = 14) -> float:
    """Same simple-average RSI as OptionsBuyStrategy._calculate_rsi."""
    if len(prices) < period + 1:
        return 50.0
    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain, avg_loss = np.mean(gains[-period:]), np.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    return float(100 - 100 / (1 + avg_gain / avg_loss))


def strategy_momentum(prices: Sequence[float], period: int = 20) -> float:
    """Same sigmoid momentum as OptionsBuyStrategy._calculate_momentum."""
    if len(prices) < period:
        return 0.5
    m = (prices[-1] - prices[-period]) / prices[-period]
    return float(1 / (1 + np.exp(-m * 10)))


def original_technical_points(prices: Sequence[float], option_type: str) -> float:
    """RSI + momentum points from _calculate_opportunity_score (range about -20..+50)."""
    r, m = strategy_rsi(prices), strategy_momentum(prices)
    pts = 0.0
    if option_type == "call":
        pts += 30 if r < 40 else (-20 if r > 60 else 0)
        pts += m * 20
    else:
        pts += 30 if r > 60 else (-20 if r < 40 else 0)
        pts += (1 - m) * 20
    return pts


# --------------------------------------------------------------------------- #
# Real trades from Alpaca
# --------------------------------------------------------------------------- #
@dataclass
class Trade:
    option_symbol: str
    underlying: str
    option_type: str
    strike: float
    expiry: date
    qty: int
    entry_time: datetime
    entry_price: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    unrealized_pnl: Optional[float] = None

    @property
    def pnl(self) -> Optional[float]:
        if self.exit_price is not None:
            return (self.exit_price - self.entry_price) * self.qty * 100
        return self.unrealized_pnl


def parse_option_symbol(symbol: str):
    m = OPTION_SYMBOL_RE.match(symbol)
    if not m:
        return None
    return (
        m[1],
        datetime.strptime(m[2], "%y%m%d").date(),
        "call" if m[3] == "C" else "put",
        int(m[4]) / 1000,
    )


def _ts(value: str) -> datetime:
    # pandas copes with Alpaca's variable-length fractional seconds; 3.10's fromisoformat doesn't.
    return pd.Timestamp(value).to_pydatetime()


def load_trades(fetcher: AlpacaDataFetcher, since: date) -> List[Trade]:
    # Look back further than `since` so every buy has its matching sell.
    params = {
        "status": "closed",
        "after": f"{since - timedelta(days=60)}T00:00:00Z",
        "limit": 500,
        "direction": "asc",
    }
    orders = [o for o in fetcher._request("GET", "v2/orders", params=params)
              if o.get("status") == "filled" and float(o.get("filled_qty") or 0) > 0]
    orders.sort(key=lambda o: o["filled_at"] or o["submitted_at"])

    open_by_symbol: Dict[str, Trade] = {}
    trades: List[Trade] = []
    for o in orders:
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

    positions = {p["symbol"]: p for p in fetcher.get_positions()}
    for sym, t in open_by_symbol.items():
        if sym in positions:
            t.unrealized_pnl = float(positions[sym]["unrealized_pl"])

    return [t for t in trades if t.entry_time.date() >= since]


def load_closes(fetcher: AlpacaDataFetcher, symbol: str, start: date, end: date):
    bars = fetcher.get_historical_data(symbol, start_date=str(start), end_date=str(end))
    return [(_ts(b["start"]).date(), float(b["close"])) for b in bars]


# --------------------------------------------------------------------------- #
# Backtest
# --------------------------------------------------------------------------- #
def run(since: date, config: TechnicalEntryConfig) -> None:
    fetcher = AlpacaDataFetcher()
    flt = TechnicalEntryFilter(config)
    trades = load_trades(fetcher, since)
    if not trades:
        print(f"No option trades bought since {since}.")
        return

    first = min(t.entry_time.date() for t in trades) - timedelta(days=150)
    last = max(t.entry_time.date() for t in trades)
    bar_cache = {u: load_closes(fetcher, u, first, last) for u in {t.underlying for t in trades}}

    hdr = (f"{'#':>2} {'Bought':<6} {'Contract':<24} {'P&L':>7}  {'New filter':<10} "
           f"{'Score':<5} {'Prefers':<7} {'Why':<26} {'Old pts C/P':<11} Old prefers")
    print(hdr)
    print("-" * len(hdr))

    rows = []
    for i, t in enumerate(sorted(trades, key=lambda x: x.entry_time), 1):
        # Only bars that closed before the entry day: no look-ahead.
        closes = [c for d, c in bar_cache[t.underlying] if d < t.entry_time.date()]
        dec = flt.evaluate(closes, t.option_type)
        prefers = flt.best_direction(closes) or "none"
        old_c = original_technical_points(closes, "call")
        old_p = original_technical_points(closes, "put")
        old_pref = "call" if old_c > old_p else "put"
        rows.append((t, dec, prefers, old_pref))

        contract = f"{t.underlying} {t.option_type[0].upper()} {t.strike:g} {t.expiry:%m-%d}"
        pnl = "open" if t.pnl is None else f"{t.pnl:+.0f}"
        if t.exit_price is None and t.pnl is not None:
            pnl += "*"
        print(f"{i:>2} {t.entry_time:%m-%d} {contract:<24} {pnl:>7}  "
              f"{'TAKE' if dec.take else 'skip':<10} {dec.score}/5   {prefers:<7} "
              f"{dec.reason[:26]:<26} {old_c:>4.0f}/{old_p:<4.0f}  {old_pref}")

    def total(sel):
        vals = [t.pnl for t, *_ in sel if t.pnl is not None]
        return len(sel), sum(vals), sum(v > 0 for v in vals)

    print("\n* = still open, unrealized P&L\n")
    n, pnl, wins = total(rows)
    print(f"Actual (no technical filter): {n} trades, P&L {pnl:+,.0f}, {wins} winners")
    taken = [r for r in rows if r[1].take]
    skipped = [r for r in rows if not r[1].take]
    n, pnl, wins = total(taken)
    print(f"New filter TAKES:  {n} trades, P&L {pnl:+,.0f}, {wins} winners")
    n, pnl, wins = total(skipped)
    print(f"New filter SKIPS:  {n} trades, P&L {pnl:+,.0f}, {wins} winners  (losses avoided if negative)")

    flips = [r for r in rows if not r[1].take and r[2] not in ("none", r[0].option_type)]
    print(f"\nTrades where the filter would have bought the OTHER side instead: {len(flips)}")
    print("  (their P&L can't be measured here: the opposite contract was never traded)")

    agree = [r for r in rows if r[3] == r[0].option_type]
    n, pnl, _ = total(agree)
    print(f"\nBot's RSI/momentum score agrees with actual direction on {n}/{len(rows)} trades, "
          f"P&L of those {pnl:+,.0f}")
    n, pnl, _ = total([r for r in rows if r[3] != r[0].option_type])
    print(f"Bot's RSI/momentum score alone would pick the other side on {n} trades, "
          f"P&L of those {pnl:+,.0f}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--since", type=date.fromisoformat,
                   default=(datetime.now(timezone.utc) - timedelta(days=30)).date(),
                   help="Only trades bought on/after this date (default: 30 days ago).")
    p.add_argument("--min-score", type=int, default=TechnicalEntryConfig.min_score,
                   help="Signals (of 5) needed to take a trade.")
    p.add_argument("--no-trend-gate", action="store_true",
                   help="Don't require the trend signal; score alone decides.")
    args = p.parse_args()
    run(args.since, TechnicalEntryConfig(min_score=args.min_score,
                                         require_trend=not args.no_trend_gate))


if __name__ == "__main__":
    main()
