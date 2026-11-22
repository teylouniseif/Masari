"""Unit tests for the prototype technical entry filter (no network)."""

import numpy as np
import pytest

from backtest_technical_entry import (
    TechnicalEntryConfig,
    TechnicalEntryFilter,
    macd_histogram,
    original_technical_points,
    parse_option_symbol,
    rsi,
)


def trend(n=120, start=100.0, daily=0.004, noise=0.004, seed=1):
    rng = np.random.default_rng(seed)
    return list(start * np.cumprod(1 + daily + rng.normal(0, noise, n)))


def trend_with_pullback(daily, n=120):
    """Steady trend, an 8-day pullback, then the trend resumes for the last 12 bars."""
    r = np.full(n, daily)
    r[100:108] = -daily
    return list(100 * np.cumprod(1 + r))


@pytest.fixture
def flt():
    return TechnicalEntryFilter()


def test_uptrend_takes_call_and_skips_put(flt):
    closes = trend_with_pullback(0.004)
    assert flt.evaluate(closes, "call").take
    put = flt.evaluate(closes, "put")
    assert not put.take and put.reason == "against trend"
    assert flt.best_direction(closes) == "call"


def test_downtrend_takes_put_and_skips_call(flt):
    closes = trend_with_pullback(-0.004)
    assert flt.evaluate(closes, "put").take
    assert not flt.evaluate(closes, "call").take
    assert flt.best_direction(closes) == "put"


def test_flat_market_takes_nothing(flt):
    closes = [100.0 + (i % 2) * 0.5 for i in range(120)]
    assert flt.best_direction(closes) is None


def test_overbought_uptrend_fails_call_rsi_zone():
    closes = trend(daily=0.01, noise=0.001)  # straight up: RSI near 100
    dec = TechnicalEntryFilter().evaluate(closes, "call")
    assert not dec.signals["rsi_zone"]
    assert dec.score == 4  # still passes 4/5 at the default threshold
    strict = TechnicalEntryFilter(TechnicalEntryConfig(min_score=5))
    assert not strict.evaluate(closes, "call").take


def test_not_enough_history_is_skipped(flt):
    dec = flt.evaluate(trend(n=30), "call")
    assert not dec.take and "bars" in dec.reason


def test_trend_gate_can_be_disabled():
    closes = trend_with_pullback(-0.004)
    gated = TechnicalEntryFilter(TechnicalEntryConfig(min_score=1))
    ungated = TechnicalEntryFilter(TechnicalEntryConfig(min_score=1, require_trend=False))
    call_gated, call_ungated = gated.evaluate(closes, "call"), ungated.evaluate(closes, "call")
    assert not call_gated.take
    assert call_ungated.take == (call_ungated.score >= 1)


def test_indicator_sanity():
    up = trend(daily=0.005, noise=0.002)
    down = trend(daily=-0.005, noise=0.002)
    assert rsi(up) > 70 and rsi(down) < 30
    assert macd_histogram(trend_with_pullback(-0.004)) < 0
    assert macd_histogram(trend_with_pullback(0.004)) > 0

def test_original_score_is_contrarian():
    """The bot's existing RSI scoring buys calls into oversold downtrends (mean reversion)."""
    down = trend(daily=-0.005, noise=0.002)
    assert original_technical_points(down, "call") > original_technical_points(down, "put")
    assert TechnicalEntryFilter().best_direction(down) != "call"


def test_parse_option_symbol():
    und, exp, typ, strike = parse_option_symbol("CMG261218C00034800")
    assert (und, str(exp), typ, strike) == ("CMG", "2026-12-18", "call", 34.8)
    assert parse_option_symbol("AAPL") is None
