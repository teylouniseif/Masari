# AGENTS.md — Masari

Orientation for anyone (human or agent) picking up this repo. It covers what the
project does, the trading strategy and its algorithm, how the live system is wired,
and what the results have actually been. Read this before changing strategy
parameters: the last three months of history is mostly a record of parameter changes
made on too little data, and that is the main trap here.

Companion docs:

- **`HISTORY.md`** — the long-form record: every phase of the strategy and why it changed, a 25-entry incident log, the full parameter change log, operational statistics, and the trade-by-trade ledger of all 88 closed trades. Read it before changing a parameter; most of these knobs have been turned before, and several have been turned and reverted.
- `ALPACA_SANDBOX_STRATEGY.md` — narrower, code-level spec of the live path.
- `README.md` — repo overview.
- `OPTIONS_ANALYSIS_README.md` — older aspirational design. **Does not match the running code**; do not trust its numbers.

---

## 1. What this project is

An autonomous, long-only **directional options buying** bot. It scans the whole
S&P 500 every hour, picks the single best-scoring call or put, sizes it to 2% of
account equity, buys it at the ask on the **Alpaca paper (sandbox) account**, and
then manages the position every 15 minutes until an exit rule fires.

It is live, unattended, and has been running continuously since 2026-08-20. There
is no real money in it — Alpaca paper account `PA3J6RJ2RMI1`, options level 3,
funded at a notional $50,000.

The rest of the repo (`etf_iron_condor_strategy.py`, `iron_condor_analyzer.py`,
`questrade_*`, `growth_investing.py`, `value_investing.py`, `momentum_investing.py`,
`directional_spread_analyzer.py`) is older or exploratory and **is not wired to
anything running**. Only the Alpaca options-buying path is live.

---

## 2. Repo map (what actually matters)

| Path | Role |
|---|---|
| `investment_strategies/options_buy_strategy.py` | **The strategy.** Criteria, indicators, scoring, exit rules, position store. 1100 lines; the core of the system. |
| `investment_strategies/alpaca_optionsplay_sandbox.py` | **The executor.** Contract resolution, position sizing, order payloads, duplicate guard, monitor/close loop, broker reconciliation. |
| `investment_strategies/alpaca_data_fetcher.py` | Alpaca REST client: quotes, bars, option chains/snapshots, account, orders, clock. Also the rate limiter and retry/backoff. |
| `investment_strategies/stock_screener.py` | S&P 500 universe, scraped from Wikipedia. |
| `test_alpaca_optionsplay_sandbox_execution.py` | **The entry point** (despite the `test_` name — it is the production CLI). `--submit`, `--monitor`, `--symbols`, `--max-orders`. |
| `scripts/run_alpaca_sandbox_hourly.sh` | Cron wrapper: hourly scan + buy. |
| `scripts/run_alpaca_sandbox_monitor.sh` | Cron wrapper: 15-minute position monitor. |
| `scripts/claude_rc_watchdog.sh` | Unrelated to trading: keeps a Claude Remote Control session alive. |
| `alpaca_sandbox_positions.json` | **The state file.** `open_positions` / `closed_positions`, with entry Greeks, regime, breakout level and realized P&L. |
| `alpaca_credentials.json` | API key/secret (gitignored). Not env vars — the code reads this file. |
| `logs/alpaca_sandbox_cron.log` | Append-only log of every run since 2026-08-11. ~14 MB. The only record of why a trade was taken. |
| `HISTORY.md` | Long-form history: phases, incidents, parameter changes, full trade ledger. |

---

## 3. Running it

```bash
cd /home/saif/AI/Masari
source .venv/bin/activate

# Dry run: scan and print what it would buy, no orders. Safe any time.
python test_alpaca_optionsplay_sandbox_execution.py

# Fast dry run on a few names (a full 503-symbol scan takes ~10 minutes)
python test_alpaca_optionsplay_sandbox_execution.py --symbols AAPL MSFT NVDA

# Live paper buy (what cron runs hourly)
python test_alpaca_optionsplay_sandbox_execution.py --submit

# Live paper exit check (what cron runs every 15 minutes)
python test_alpaca_optionsplay_sandbox_execution.py --monitor --submit
```

Rules of engagement:

- **Without `--submit` nothing is ever sent to the broker.** Use that for anything exploratory.
- **With `--submit`, both modes exit immediately when Alpaca's clock says the market is closed.** The scan re-checks after the scan completes, because a 15:00 scan can finish after the close.
- **The broker is the source of truth, not `alpaca_sandbox_positions.json`.** See §8 for why they disagree.
- Don't run a full scan concurrently with cron; both write the same state file and the last writer wins (§9, bug #1).

Cron, as installed:

```
0 * * * *   scripts/run_alpaca_sandbox_hourly.sh     # scan + buy
*/15 * * * * scripts/run_alpaca_sandbox_monitor.sh   # exits
*/10 * * * * scripts/claude_rc_watchdog.sh           # unrelated
```

---

## 4. The strategy, in one paragraph

Buy single-leg options — never sell, never spread — on S&P 500 names showing a
directional signal, holding 1–6 weeks, with no profit target. Expectancy is
expected to come from **a few large winners paying for many small losses**:
risk per trade is capped at 2% of equity and the premium paid is the entire
downside, while the upside is uncapped. There are two entry theses, and the
scanner picks whichever the price structure says applies:

1. **Breakout continuation** — the stock has closed beyond its 20-day high (or low) within the last 5 sessions and is still beyond that level. Trade *with* it: calls on upside breaks, puts on downside breaks. The opposing side is not even scored.
2. **Range mean reversion** — no break in force. Buy calls on oversold (RSI < 40) and puts on overbought (RSI > 60), with a bonus for calls near the lower Bollinger band.

The breakout branch exists because the mean-reversion branch alone fades every
breakout by construction (a stock clearing resistance *is* overbought), which
produced things like three NVDA puts in a week at RSI 66–84.

---

## 5. The algorithm, step by step

### Step 1 — Universe
`StockScreener.get_sp500_stocks()` scrapes the S&P 500 list from Wikipedia (503
symbols). `--symbols` overrides. Dotted-class tickers like `BRK-B` 400 against the
data API (which wants `BRK.B`) and are silently dropped.

### Step 2 — Stock-level filter (`analyze_stock_for_options`)
Applied before touching the option chain, so a rejected name costs one quote call:

| Check | Value |
|---|---|
| Last price | $20 – $500 |
| Daily volume | ≥ 10,000 shares |
| Market cap | ≥ $1B — **defined but never checked in code** |
| History | 100 days of daily bars required |

### Step 3 — Indicators
From those bars, with the live price substituted for the in-progress session's close:

- **RSI(14)** — Wilder-less simple average of gains/losses.
- **Momentum** — `_calculate_momentum`, a 20-day normalized score in roughly 0–1 (0.5 is neutral). Also recomputed at exit time against `entry_momentum`.
- **Bollinger Bands(20, 2σ)** and **%B** — 0 at the lower band, 1 at the upper, outside 0–1 beyond them.
- **Price structure / regime** (`_price_structure`) — walks the last `breakout_memory = 5` bars oldest-first. A close above the prior `breakout_lookback = 20` bars' high sets `regime = breakout_up` and fixes `breakout_level` at that high; a close below the prior 20 bars' low sets `breakout_down`. A later break the other way replaces it. The regime only survives if the **live price is still beyond the level**; otherwise it degrades to `range`. The level is stored on the position and is what the failed-breakout exit later tests against.

### Step 4 — Option chain and per-contract suitability (`_is_option_suitable`)
Every strike/expiry on both sides is tested:

| Criterion | Value |
|---|---|
| Days to expiry | 7 – 45 |
| Bid and ask | both > 0 |
| Bid/ask spread | ≤ 20% of ask |
| Contract volume | ≥ 100 |
| Open interest | ≥ 200 (merged in from `v2/options/contracts`) |
| Implied volatility | 15% – 80% |
| Greeks present | **delta and theta must both be non-null** — the Greek exits read them live, so a contract the feed can't price would be held with those exits blind |
| \|delta\| | 0.30 – 0.70 (broker delta; Black-Scholes approximation only as fallback) |

The delta band is the moneyness filter. Without it the scanner used to pick deep-ITM
contracts that were nearly pure intrinsic value — an AAPL $110 call at ~$200 with
the stock near $310 — carrying no real leverage and too expensive to size at all.

### Step 5 — Symbol-level IV-richness gate
Median IV of the 6 nearest-the-money contracts is ranked against **the name's own
trailing 1-year rolling 20-day realized volatility** (`_realized_volatility_distribution`).
If that rank exceeds `max_iv_percentile = 65`, the whole symbol is rejected:
*"Premium too rich: IV x% ranks at the Nth percentile of this name's realized volatility."*

This is a stand-in for a true IV percentile, which would need a year of past IV
readings the feed does not provide. Implied normally sits above realized, biasing the
rank upward; the 65 threshold is calibrated for that bias against a 30-name sample
(median rank 56, p75 68) and keeps roughly 70% of candidates.

### Step 6 — Directional pruning
If a breakout is in force, only the matching side survives (`breakout_up` → calls,
`breakout_down` → puts). In `range`, both sides are scored.

### Step 7 — Scoring (`_calculate_opportunity_score`)
Additive, floored at 0:

| Component | Points |
|---|---|
| Breakout regime (trading with it) | +30, and RSI is ignored entirely |
| %B outside the band on the breakout side | +15 |
| *Range only:* RSI < 40 for a call / RSI > 60 for a put | +30 |
| *Range only:* RSI > 60 for a call / RSI < 40 for a put | −20 |
| *Range only:* call with %B < 0.2 | +15 (the mirror case — a put near the upper band — lost all 8 entries since late September and earns nothing) |
| Theta burn > 5% of premium per day | −15 |
| Theta burn < 2.5% per day | +10 |
| Momentum | `+momentum × 20` for calls, `+(1 − momentum) × 20` for puts |
| Risk/reward > 2.0 / > 1.5 / < 1.0 | +25 / +15 / −15 |
| Probability of profit > 0.6 / < 0.4 | +20 / −15 |
| Time value < 50% of mid price | +15 |
| Volume > 1000 | +10 |
| Open interest > 1000 | +10 |

Top 5 per symbol are kept, all symbols' candidates are pooled and sorted by score.
Scoring was **silently disabled from 2026-03-25 to 2026-08-20** by two hardcoded
overrides (`score = 0` in the analyzer and `score = 80` at the end of the scorer).
For five months the bot bought the first suitable contract it found, not the best one.

### Step 8 — Affordability prune
`max_affordable_premium()` = `min(2% of equity, buying power) / 100`. Candidates
priced above it are dropped so the single order slot isn't spent on an order that
can only fail sizing.

### Step 9 — Duplicate-exposure guard
`_has_open_exposure(symbol)` scans current broker positions *and* open orders for any
contract on the same underlying and skips the trade if one exists. This fires
constantly — since Oct 16 it has blocked INTC 19 times, WMT 10, NFLX 10, NVDA 8 —
which is itself a finding: the scanner keeps re-selecting the same handful of names.

### Step 10 — Contract resolution and sizing
`_resolve_option_contract` re-fetches the chain and picks the live contract nearest
the chosen strike, tie-broken on DTE. Greeks are re-checked here. Then:

```
risk_budget     = equity × 2%
by_risk         = risk_budget // (limit_price × 100)
by_buying_power = buying_power // (limit_price × 100)
quantity        = max(min(by_risk, by_buying_power), 0)
```

Order: **limit at the current ask** (falling back to scan-time mid), `time_in_force=day`,
`position_intent=buy_to_open`, `order_class=simple`. Day orders that don't fill simply die.

### Step 11 — Tracking
`create_position` writes a record to `open_positions`: entry price, size, max risk,
stop at 50% of entry, DTE, entry RSI / momentum / score / regime / breakout level /
%B, entry delta/theta/gamma/vega/IV, the Alpaca `option_symbol` and `order_id`.

### Step 12 — Monitor and exit (every 15 minutes, `monitor_and_close`)
For each tracked open position: fetch the broker position, the live price, and the
option snapshot (Greeks + IV), then evaluate in **strict priority order**:

1. **Stop loss** — current price ≤ `entry_price × 0.5`.
2. **Time decay** — `dte_at_entry − days_held ≤ 5`.
3. **Technical** (recomputed from fresh daily bars):
   - *Failed breakout* (breakout entries only) — price back through `breakout_level` by more than 1% (a plain retest is tolerated).
   - *RSI reversal* (range entries only) — RSI two sessions ago was > 70 for a call / < 30 for a put, and has since turned back.
   - *Momentum divergence* — momentum < 0.7 × `entry_momentum` for a call, > 1.3 × for a put.
4. **Greeks** (from the live snapshot):
   - *Delta collapse* — \|delta\| < 0.20 (too far OTM).
   - *Deep in the money* — \|delta\| > 0.85 (behaves like stock; leverage spent).
   - *Theta burn* — daily theta > 5% of the option's current price.
   - *IV crush* — IV ≤ 75% of `entry_iv`.

`entry_iv` is the **first live IV reading after the fill**, not the scan's. That
matters: the scan can run hours before the fill, and a stale overnight IV once
triggered a false IV-crush exit on KR 13 minutes after it filled.

When the feed drops Greeks (which it does deep ITM/far OTM — exactly where the delta
exits matter), delta falls back to Black-Scholes priced with the snapshot IV, else
`last_iv` (refreshed every check), else `entry_iv`. Theta burn and IV crush have no
fallback and are simply skipped.

Exits are **market sell-to-close** orders. **There is no price-based profit exit** —
winners run until a technical or Greek signal closes them.

### Step 13 — Reconciliation
If a tracked position isn't at the broker, `_reconcile_missing_position` checks the
entry order's status before concluding anything: still working → leave it; terminal
unfilled → drop the record; filled but no position → look up the closing fill price
from history and close it as `"Closed at broker (reconciled from fill history)"` with
`final_pnl = None` if unrecoverable. This ambiguity was a real bug once: a monitor run
checked a few seconds before a queued order filled, decided the position had opened
and closed, and dropped CVNA out of tracking — leaving a real position live with no
stop loss.

### Rate limiting
Alpaca allows 200 req/min per key, metered separately per host. A full scan issues
~1,800 data requests and ~760 trading requests; unthrottled it ran at ~240 data
req/min — over the limit, and because per-symbol errors are swallowed, a 429 just
made symbols vanish from the scan. `AlpacaDataFetcher._request` now runs a
sliding-window limiter at **190/min per host**, with exponential backoff + jitter on
429/5xx, honoring `Retry-After` and `X-RateLimit-Reset`, and parking the whole host
bucket on a 429. Cost: a scan floor of ~9.5 minutes instead of ~7.7, still inside the
hourly window.

---

## 6. Parameter reference (`OptionsBuyCriteria`)

```
min_price 20          max_price 500         min_volume 10,000     min_market_cap 1e9 (unused)
min_days_to_expiry 7  max_days_to_expiry 45 min_option_volume 100 min_open_interest 200
max_bid_ask_spread 0.20
min_implied_volatility 0.15  max_implied_volatility 0.80  max_iv_percentile 65
min_delta 0.30        max_delta 0.70
momentum_period 20    rsi_period 14         min_rsi 30            max_rsi 70
breakout_lookback 20  breakout_memory 5     breakout_failure_buffer 0.01
bollinger_period 20   bollinger_std 2.0
max_theta_burn 0.05   low_theta_burn 0.025
max_risk_per_trade 2.0  stop_loss_multiplier 0.5
exit_theta_burn 0.05  exit_min_delta 0.20   exit_max_delta 0.85   exit_iv_crush 0.25
```

Two notes carried in the code comments and worth repeating:

- **`min_days_to_expiry = 7` is a testing value**, flagged in-code to be raised to 21 before any live use. It must stay above 5, or the time-decay exit fires on entry.
- **The 50% stop was measured, not guessed.** Replaying closed trades against their hourly bars put a 30% stop about $3k *behind* 50%, because it forced exits on positions that dipped and went on to be the biggest winners. Tighter and looser both measured worse, including no stop at all.

---

## 7. Results so far

### Equity

Alpaca paper account `PA3J6RJ2RMI1`, started at **$50,000** around 2026-08-20.

| Date | Equity | vs start |
|---|---|---|
| 2026-08-20 (start) | $50,000 | — |
| 2026-09-09 (**peak**) | $54,247 | +8.5% |
| 2026-10-11 | $46,490 | −7.0% |
| 2026-10-15 | $45,767 | −8.5% |
| 2026-10-23 | $45,354 | −9.3% |
| 2026-10-30 | $43,357 | −13.3% |
| 2026-11-05 | $42,983 | −14.0% |
| **2026-11-09 (current)** | **$42,455** | **−15.1%** |

Peak-to-current drawdown: **−21.7%**. Equity fell every single session in the week of
Nov 2–6. Against roughly flat-to-up index behaviour over the same stretch, this is not
a market-beta story.

### Closed trades (88 records in `alpaca_sandbox_positions.json`, 2026-08-31 → 2026-11-06)

| Metric | Value |
|---|---|
| Net P&L (tracking file) | **−$9,069** |
| Trades | 88 |
| Win rate | **25%** (22 / 88) |
| Average win / average loss | **+$630 / −$348** |
| Win rate needed to break even at that ratio | **36%** |
| Median trade | −$292 |
| Gross wins / gross losses | +$13,871 / −$22,940 |
| Total premium deployed | $66,972 |
| Return on premium | **−13.5%** |
| Average hold | 3.7 days (winners 5.8, losers 3.1) |
| Average DTE at entry | 22.6 |
| Top 3 winners as share of gross wins | 35% |

### By exit reason

| Reason | n | Total | Avg |
|---|---:|---:|---:|
| Profit Target Hit *(rule since removed)* | 6 | +$7,767 | +$1,294 |
| Time Decay Exit | 5 | +$1,438 | +$288 |
| Profit Exit – too small to scale out *(removed)* | 3 | +$1,294 | +$431 |
| Deep In The Money – leverage spent | 2 | +$1,251 | +$626 |
| RSI Reversal Signal | 2 | +$822 | +$411 |
| IV Crush Exit | 3 | −$98 | −$33 |
| Support Break – Call Exit | 2 | −$132 | −$66 |
| Failed Breakout – back above support | 2 | −$187 | −$94 |
| Resistance Break – Put Exit | 2 | −$259 | −$130 |
| Failed Breakout – back below resistance | 1 | −$322 | −$322 |
| Theta Burn Exit | 2 | −$413 | −$206 |
| Momentum Divergence – Put Exit | 5 | −$780 | −$156 |
| Momentum Divergence – Call Exit | 6 | −$880 | −$147 |
| Closed at broker (reconciled, reason lost) | 10 | −$2,035 | −$204 |
| **Stop Loss Hit** | **37** | **−$16,535** | **−$447** |

**42% of all trades hit the 50% stop**, and they account for the entire loss.

### Calls vs puts

| | Trades | Net | Win rate |
|---|---:|---:|---:|
| Calls | 54 | −$2,499 | 31% |
| Puts | 34 | −$6,570 | **15%** |

Puts are most of the damage. At a 15% win rate over 34 trades, "puts are as good as
calls" is not a comfortable hypothesis. The straightforward reading: the bot keeps
betting on drops in a market that has been grinding up.

### By entry regime

| Regime | n | Net | Win rate |
|---|---:|---:|---:|
| (pre-breakout era, no regime recorded) | 61 | −$5,185 | 28% |
| `breakout_up` | 12 | −$1,398 | 25% |
| `breakout_down` | 13 | −$1,993 | 15% |
| `range` | 2 | −$493 | 0% |

### By month of entry

| Month | n | Net | Win rate |
|---|---:|---:|---:|
| Aug 2026 | 4 | +$995 | 25% |
| Sep 2026 | 45 | −$1,472 | 33% |
| Oct 2026 | 36 | −$7,951 | 17% |
| Nov 2026 | 3 | −$641 | 0% |

### By rule-set era

Each era is a different exit configuration. Note the sample sizes. `HISTORY.md` §1
covers what changed in each and why, with the measurements behind each decision.

| Era | Dates | n | Net | Win rate |
|---|---|---:|---:|---:|
| v1 — 2× profit target, 50% stop | 8/31 – 9/20 | 30 | −$369 | 30% |
| v2 — 30% stop, scale-out to free ride | 9/21 – 9/28 | 15 | +$634 | 40% |
| v3 — 50% stop restored, bank small positions | 9/29 – 10/9 | 16 | −$5,450 | 12% |
| v4 — breakouts, broker Greeks, Bollinger | 10/10 – 10/13 | 3 | +$717 | 33% |
| v5 — technical + Greek exits, no profit target | 10/14 – now | 24 | −$4,601 | 17% |

### Concentration

**47 distinct symbols** traded out of 503 scanned. INTC 8 times, NFLX 7, AMZN 5,
NVDA 4. Best names: INTC +$2,781, MSFT +$1,257, PLTR +$831. Worst: T −$1,631,
NVDA −$846, NOW −$801, NFLX −$743.

### Currently open (7 positions, unrealized −$157)

| Contract | Qty | Entry | Last | Unrealized |
|---|---:|---:|---:|---:|
| INTC Nov 20 62.5 put | 1 | 5.75 | 7.35 | +$160 |
| DVN Dec 18 42.5 put | 4 | 2.07 | 2.19 | +$48 |
| CVNA Dec 18 75 call | 1 | 5.00 | 4.85 | −$15 |
| KDP Dec 18 37 call | 7 | 1.15 | 1.10 | −$35 |
| FISV Nov 20 60 call | 2 | 3.80 | 3.50 | −$60 |
| XYZ Dec 18 90 call | 2 | 2.98 | 2.58 | −$80 |
| MGM Dec 18 33 call | 5 | 1.70 | 1.35 | −$175 |

Seven at once, each sized to 2% of equity, is ~14% of the account at risk with no
portfolio-level cap. That is the most exposure the bot has ever carried, and it is why
a single day (Fri Nov 6) could cost −$527 with only one trade closing.

---

## 8. Why the tracking file and the broker disagree

The file says −$9,069 over 88 trades; broker equity implies roughly −$7,400 realized.
Three causes, all known, none fixed:

1. **Entries are recorded at the limit price, not the fill.** CSCO recorded 6.06 against a 5.95 fill; WMT 3.55 against 3.50.
2. **Exits are recorded at the price the monitor last read, not the fill.** EL shows −$352 in the file against −$295 at the broker.
3. **Scan/monitor save collisions** lose whole exit records, which then get re-added as reconciled trades (§9, bug #1).

**Always treat Alpaca as authoritative for P&L.** The tracking file is useful for
*why* a trade happened — entry regime, score, entry Greeks — which the broker does
not have.

---

## 9. Known bugs and gaps, in the order worth fixing

These are the ones still open. `HISTORY.md` §3 has the full incident log — 25 entries,
including the 21 already fixed, with dates, evidence and the commit that closed each.

1. **The scan and the monitor overwrite each other's saves.** Both start on the hour. The scan loads the state file at :00 and saves it ~10 minutes later, clobbering anything the monitor wrote in between. The :00 monitor's exit gets resurrected as an open position, then reconciled at :15 — P&L comes out right, nothing is sold twice, but **the exit reason and that run's IV updates are lost**. 10 of 88 closed trades read only "reconciled from fill history", so the exit-rule statistics in §7 are incomplete by construction. Fix: have the scan reload and merge before saving, or put a file lock around both.
2. **No cooldown after an exit.** A contract can be stopped out and re-bought the same morning. A NFLX put stopped out Tuesday at the open and the scan bought the identical contract back 40 minutes later. INTC was sold on momentum divergence at 09:45, re-bought at 11:11, sold again at 12:30 on the same signal, and re-bought at 13:10 — the scan scores INTC as a buy while the exit rule says its momentum is gone. **Entries and exits are judged by different criteria**, and each round trip pays the spread.
3. **No portfolio-level risk cap.** `--max-orders 1` limits new positions per scan, not total exposure. Only the per-underlying duplicate guard constrains anything.
4. **Overnight gaps defeat the stop.** A stop checked every 15 minutes cannot protect against an opening gap: the NFLX put went 2.62 → 1.08 overnight, the DOW call 2.40 → 0.68 in two sessions. **52% of all exits land in the 09:30 or 09:45 run**, totalling −$6,376.
5. **Local P&L inaccuracy** — §8.
6. **Per-symbol scan failures are swallowed.** `_get_option_chain_analysis` returns `{'error': ...}` for any exception and `find_best_opportunities` drops the symbol without logging. A scan that silently covered 300 of 503 symbols looks identical to a clean one.
7. **`min_days_to_expiry = 7`** is a placeholder per its own comment (should be 21).
8. **`min_market_cap`** is defined and never checked.
9. **`BRK-B` and other dotted-class tickers** 400 against the data API and are dropped from every scan.
10. **Division-by-zero `RuntimeWarning`s** spam the log from the Black-Scholes approximations when `dte` or `iv` is 0. Cosmetic, but they bury real errors.

---

## 10. The actual diagnosis — read this before tuning anything

Every change since September has been to **how trades exit**. The losses come from
**which trades get entered**. Exit rules can cap damage or let a winner run; they
cannot rescue a position entered in the wrong direction, and 42% of trades hit the
50% stop.

The sample sizes do not support the tuning that has been done. **One trade's P&L swings
by about ±$530.** Over the 8–24 trades each rule set has run, that alone moves an era's
total by ±$1,500 or more. In six weeks the stop went 50% → 30% → 50%, and the profit
exit went 2× target → scale-out → +30% → nothing. Each of those decisions was made on
a handful of trades (`HISTORY.md` §4 lists every one with its rationale and whether it
survived). **That is fitting noise**, and the era table in §7 cannot
distinguish any of those changes from chance.

If you are going to change this system, the useful order is:

1. **Freeze the exit rules** for a pre-committed number of trades — 40 is a reasonable floor — before judging any of them.
2. **Fix bug #1 first.** Until exit reasons stop being lost, there is no clean data to judge exits on at all.
3. **Work on entries, starting with puts.** Either stop buying them, or gate them on a market-level downtrend (e.g. SPY below its 50-day) rather than on single-name RSI.
4. **Add the cooldown** (bug #2) — it is cheap and the re-buy pattern is visible in the log.
5. **Backtest before shipping.** There is no backtest harness in this repo; every parameter has been validated, if at all, by replaying a few dozen closed trades against hourly bars. Anything claimed about a parameter without that replay is a guess.
6. **Remember the structural drag.** Every trade buys at the ask, sells at market, and pays theta daily. The underlying has to move the right way *and quickly* just to break even. A 25% win rate with a 1.8:1 win/loss ratio needs 36% to break even — the gap is the whole problem.

---

## 11. Conventions for agents working in this repo

- **Do not place live orders to test something.** Run without `--submit`, or with `--symbols` on a short list.
- **Never commit `alpaca_credentials.json`** or any log containing a key. It is gitignored; keep it that way. Logs contain the account number and positions, which is fine, but not keys.
- **State the sample size** whenever you report on a parameter's performance. "The 30% stop did worse" over 15 trades is not a finding.
- **Check `HISTORY.md` before proposing a change.** The 30% stop, the 2× profit target, the scale-out, the support/resistance exit and the +30% profit exit have all been tried and removed, each for a documented reason. Append new phases and incidents there as they happen rather than growing this file.
- **When asked for account status**, query Alpaca live and reconcile against the tracking file rather than reading the file alone — they diverge (§8), and the divergences have repeatedly been the actual bug.
- **Prefer the strategy spec in `ALPACA_SANDBOX_STRATEGY.md` and this file over `OPTIONS_ANALYSIS_README.md`**, which describes a design that was never what ran.
- The `test_*.py` files at the repo root are a mix of real tests and production entry points. `test_alpaca_optionsplay_sandbox_execution.py` is the live CLI, not a test.
- Code comments in `OptionsBuyCriteria` record *why* a parameter has its value, often including a measurement. Read them before overriding, and update them if you change a value.
