# Alpaca Sandbox Options Strategy

What actually runs hourly via cron (`scripts/run_alpaca_sandbox_hourly.sh` → `test_alpaca_optionsplay_sandbox_execution.py --submit`) against the Alpaca paper account. This describes the live code path, not the aspirational design in `OPTIONS_ANALYSIS_README.md` (some numbers below differ from that doc — this one reflects `investment_strategies/options_buy_strategy.py` as currently configured).

Cron fires the scan hourly and the monitor every 15 minutes around the clock, but with `--submit` both exit immediately unless Alpaca's clock says the market is open; the scan checks again before placing orders, since the 15:00 scan can finish after the close. Before this, scans ran overnight and on weekends and queued orders for the open priced off stale quotes (KR bought from a 02:09 scan, CSCO from a Saturday one).

## Pipeline

1. **Scan** (`OptionsBuyStrategy.find_best_opportunities`, symbols default to the full S&P 500 via `StockScreener.get_sp500_stocks()` scraped from Wikipedia; overridable via `--symbols`). Previously hardcoded to `AAPL MSFT NVDA TSLA` — that was a leftover testing default the cron job never overrode, fixed 2026-08-20. A full 503-symbol scan takes ~10 minutes.
2. **Filter** each symbol's stock data and full option chain against entry criteria
3. **Score** surviving candidates
4. **Size & submit** the top `--max-orders` (default 1) via `AlpacaOptionsPlaySandboxExecutor`
5. **Track** the resulting position in `alpaca_sandbox_positions.json`
6. Separately, `--monitor` mode checks tracked open positions against live prices and sells-to-close on an exit signal

## Entry criteria (`OptionsBuyCriteria`)

**Stock filter** — applied before touching the option chain:
| Criterion | Value |
|---|---|
| Price | $20–$500 |
| Daily volume | ≥ 10,000 shares |
| Market cap | ≥ $1B (defined but not currently checked in code) |

**Option filter** — applied per contract:
| Criterion | Value |
|---|---|
| Days to expiry (DTE) | 7–45 |
| Option volume | ≥ 100 |
| Open interest | ≥ 200 |
| Bid/ask spread | ≤ 20% of ask |
| Implied volatility | 15%–80% |
| Delta (magnitude) | 0.30–0.70 |
| Greeks | Feed must supply delta and theta (checked at scan and again on the contract resolved for the order) |

⚠️ `min_days_to_expiry = 7` is marked in-code as a **testing value**; the comment says raise to 21 before going live. It must stay above 5, since the time-decay exit fires at `dte_at_entry - days_held <= 5` — anything entered at ≤5 DTE exits almost immediately.

Both calls and puts are evaluated per strike; RSI(14) and a 20-day momentum score feed the scoring function below.

**Delta/moneyness filter** (added 2026-08-20): before this, nothing constrained how far the strike could be from spot. In practice the scanner kept picking deep-ITM contracts (e.g. an AAPL $110 call priced at ~$200 while the stock traded around $310) — almost pure intrinsic value, little real leverage, and often too expensive to size even a single contract under the 2% max-risk-per-trade budget. Now filtered to a 0.30–0.70 delta band (call delta directly; `-put_delta` for puts, since `_calculate_put_delta` returns a negative value) using the existing simplified Black-Scholes delta approximation.

## Scoring

`_calculate_opportunity_score` scores each candidate from RSI alignment, momentum, risk/reward, probability of profit, time-value ratio, and volume/OI. It was silently disabled from 2026-03-25 (commit `01cee69`, "adding sandbox") through 2026-08-20 via two hardcoded overrides bundled into an unrelated 1200-line commit with no explanation: `score = 0` in `analyze_stock_for_options`, and the scorer itself overwriting its own result with `score = 80` on its last line. For ~5 months, every hourly run picked "first suitable contract found," not the best one. **Re-enabled 2026-08-20** — both overrides removed.

## Rate limiting

Alpaca allows **200 requests/min per API key**, metered separately for the trading API (`paper-api.alpaca.markets`) and the market data API (`data.alpaca.markets`) — both 200/min on the free Basic plan.

A full 503-symbol scan issues roughly **1,800 data requests** (quote + 100-day bars + 1–4 pages of option snapshots per symbol) and **760 trading requests** (1–4 pages of `v2/options/contracts` for the open-interest merge). Measured on a 60-symbol sample, the unthrottled scan ran at ~240 data req/min — *over* the limit. Because `_get_option_chain_analysis` catches every exception and `find_best_opportunities` silently drops symbols that return an error, a 429 does not surface anywhere: the symbol just vanishes from the scan.

`AlpacaDataFetcher` now paces itself in `_request`, the single choke point every call goes through:

- **Sliding-window limiter, one bucket per host** (`_RateLimiter`), default 190/min — a small margin under 200 to absorb clock skew between our window and Alpaca's. Trading and data hosts each get their own bucket, matching how Alpaca meters them.
- **Retry with exponential backoff + jitter** on 429 and 5xx (a 503 on `v2/account` has been seen in the cron log), plus connection errors/timeouts. Defaults: 5 retries, 1s base, doubling, capped at 60s. Jitter prevents a throttled batch from retrying in lockstep.
- **Server-directed delays win** — `Retry-After` (seconds or HTTP date) and `X-RateLimit-Reset` (epoch seconds) are honored over the computed backoff.
- **A 429 parks the whole host bucket**, not just the calling request, so queued work doesn't walk straight into another 429.
- 401 still raises the credential-hint `ValueError` immediately, and non-retryable 4xx (e.g. 404 from `get_position`) still raise on the first attempt — the retry loop doesn't mask them.

All four knobs are constructor arguments (`requests_per_minute`, `max_retries`, `backoff_base_seconds`, `backoff_cap_seconds`); raising `requests_per_minute` toward 10,000 is the one change needed on an Algo Trader Plus data plan.

**Runtime cost:** the data bucket is the binding constraint at ~1,800 requests. At 190/min that is a **~9.5 minute floor**, versus ~7.7 min unthrottled — the scan gets a couple of minutes slower but stops silently losing symbols. A measured 60-symbol run held a peak of 190 in any rolling 60s window and projected to **~9 minutes** for the full 503. That still fits inside the hourly cron window, but the margin is now ~50 minutes rather than ~52 — worth remembering if the universe grows past the S&P 500 or per-symbol request counts rise. Scoping the snapshot fetch to the 7–45 DTE window the strategy actually uses (instead of pulling every expiry, ~22 for the megacaps) would cut the request count several-fold if more headroom is ever needed.

## Position sizing

Long options only (`buy_to_open`), sized by `AlpacaOptionsPlaySandboxExecutor._calculate_quantity`:

```
risk_budget   = account_equity * (max_risk_per_trade / 100)   # 2% of equity
by_risk       = risk_budget // (limit_price * 100)
by_buying_power = buying_power // (limit_price * 100)
quantity      = min(by_risk, by_buying_power)
```

Order is a **limit** order at the contract's current ask (falls back to scan-time mid price if ask is unavailable), `time_in_force=day`.

## Duplicate-exposure guard

Before submitting, `_has_open_exposure(symbol)` checks both current Alpaca positions and open orders for *any* contract on the same underlying, and skips the trade if one exists — this exists specifically to stop the strategy from piling into the same name across consecutive hourly scans before a prior order has filled. (This guard was added in a same-day fix; two duplicate `AAPL` call orders got through before it existed and had to be cancelled manually.)

## Exit logic (`--monitor` mode)

Runs independently of scanning, evaluated against live bid/ask per open tracked position, in this priority order:
1. **Stop loss** — price ≤ `entry_price × 0.5`
2. **Time decay** — `dte_at_entry - days_held ≤ 5`
3. **Technical exit** — any of:
   - Failed breakout (breakout entries only): price falls back through the breakout level
   - RSI reversal (range entries only): 14-day RSI two sessions ago was >70 for calls / <30 for puts, and has since turned back
   - Momentum divergence: 19-day momentum score below 0.7× entry for calls, above 1.3× entry for puts
4. **Greek exit**, from the live option snapshot — any of:
   - Delta collapse: |delta| < 0.20
   - Deep in the money: |delta| > 0.85
   - Theta burn: daily theta > 5% of the option's current price
   - IV crush: implied volatility ≤ 75% of `entry_iv`, the first IV reading after the fill (not the scan's, which can be hours stale by the time a limit order fills)

   The feed drops Greeks for contracts deep in or far out of the money. Delta then falls back to Black-Scholes, priced with the snapshot IV, else the last IV seen on that position (`last_iv`, refreshed every check), else `entry_iv`. Theta burn and IV crush have no fallback and are skipped.

There is no price-based profit exit: winners run until a technical or Greek signal closes them.

If a tracked position is no longer held at Alpaca at all (expired, exercised, or closed outside this tool), it's reconciled as closed with `final_pnl = None` rather than treated as an exit signal.

## Risk parameters

- Max risk per trade: 2% of account equity (premium paid = full risk on a long option)
- Stop loss: 50% of premium
- No portfolio-level risk cap — each hourly scan can open one new position (`--max-orders`) independent of how many are already open, aside from the per-underlying duplicate guard

## Known gaps

- `min_market_cap` criterion is defined but never checked.
- No hard cap on total concurrent open positions or aggregate portfolio risk — only per-underlying dedup.
- `min_days_to_expiry=7` is explicitly a placeholder per its own code comment.
- Per-symbol scan failures are swallowed: `_get_option_chain_analysis` returns `{'error': ...}` for any exception and `find_best_opportunities` drops those symbols without logging. A scan that silently covered 300 of 503 symbols looks identical to a clean one.
- `BRK-B` (and any other dotted-class ticker) 400s against the data API, which expects `BRK.B`. The Wikipedia S&P 500 scrape yields the hyphenated form, so these symbols are silently dropped from every scan.
