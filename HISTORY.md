# HISTORY.md — what actually happened

A chronological record of the Alpaca options-buying bot: every phase of the strategy,
every parameter change and why it was made, every incident, and the trade-by-trade
ledger. `AGENTS.md` describes the system **as it is now**; this file describes **how it
got there**, including the mistakes, because most of the useful information in this
project is in the mistakes.

Sources: `git log`, `logs/alpaca_sandbox_cron.log` (every run since 2026-08-11),
`alpaca_sandbox_positions.json`, and the account-status reports from the Claude Code
sessions that supervised it. Where the broker and the tracking file disagree, the
broker wins; see `AGENTS.md` §8.

---

## Contents

1. [Timeline by phase](#1-timeline-by-phase)
2. [Equity curve](#2-equity-curve)
3. [Incident log](#3-incident-log)
4. [Parameter change log](#4-parameter-change-log)
5. [Operational statistics](#5-operational-statistics)
6. [What has never been tried](#6-what-has-never-been-tried)
7. [Appendix: every closed trade](#7-appendix-every-closed-trade)

---

## 1. Timeline by phase

### Phase 0 — Feb to Jul 2026: prototype, nothing live

`99eaaaf` (2026-02-15) through `0add138` (2026-05-20). The repo starts as a grab-bag of
strategy sketches: iron condors, Questrade integration, growth/value/momentum screens.
`OptionsBuyStrategy` is written in this period. Two things happen that matter much later:

- **`01cee69` "adding sandbox" (2026-03-25)** — a 1,200-line commit with no explanation bundles in two hardcoded overrides that **silently disable opportunity scoring**: `score = 0` in `analyze_stock_for_options`, and the scorer overwriting its own result with `score = 80` on its last line. This goes unnoticed for five months.
- **`5a19db6` (2026-05-10)** — first option opened in a paper account, by hand.

`0add138` fixes Greeks and IV in option snapshots and adds a weekly options fetcher.
Nothing is scheduled; nothing trades unattended.

### Phase 1 — Aug 11 to Aug 19: live on cron, and completely broken

The hourly cron job starts. For nine days it runs 500 times and **never fills a single
order.** Five independent defects stack up:

1. **Scoring was disabled** (from March, above). Every scan bought "first suitable contract found", not the best.
2. **The suitability filter was commented out**, with a dead `return True` at the top of `_is_option_suitable` — spread, volume, open interest and IV filtered nothing. Fixed same day by `573c233`.
3. **`min_days_to_expiry` was 0**, so the scanner bought 0–1 DTE contracts which instantly tripped the time-decay exit on the next monitor check. Fixed by `bd7b37a` (set to 7, with a comment to raise it to 21 before going live — still 7 today).
4. **`quantity` was hardcoded to 1** regardless of premium or account size; `max_risk_per_trade` was defined and unused. Fixed by `bd7b37a`.
5. **The symbol list was hardcoded to `AAPL MSFT NVDA TSLA`** — a leftover testing default the cron job never overrode.

The visible symptom was absurd: with nothing constraining moneyness, the scanner picked
the **AAPL $110 call at ~$105 of premium with the stock near $310** — almost pure
intrinsic value, no leverage — and submitted it hour after hour. The broker rejected
every one: **180 `403 Forbidden` responses on `/v2/orders` between Aug 11 and Aug 19.**
Two duplicate AAPL orders did get through before the duplicate-exposure guard existed,
and had to be cancelled by hand.

### Phase 2 — Aug 20: unblocked

Three changes in one day:

- **`74041f3`** removes both scoring overrides. Scoring works for the first time since March.
- **`b50fb6c`** switches the universe from four hardcoded tickers to the full S&P 500, scraped from Wikipedia (503 symbols).
- **The paper account is replaced**: `PA3B7G8PO8U2` → **`PA3J6RJ2RMI1`**, funded at $50,000. The 403s stop immediately and never recur. The log doesn't record the reason; the timing says the first account couldn't place option orders.

Scans now run clean but find nothing fillable for eleven days — the newly re-enabled
filters reject everything, because open interest is broken (next phase).

### Phase 3 — Aug 29: the infrastructure day

Four commits, no strategy changes, all plumbing — and the one that actually unblocked
trading:

- **`d9c1341`** — **open interest was always 0.** Alpaca's option *snapshots* endpoint does not return it on any feed, so every contract failed `min_open_interest ≥ 200`. It's now fetched from `v2/options/contracts` and merged into the chain, scoped to the expiries actually present (an unbounded contracts query silently returns a truncated single page with no continuation token). The same commit adds the **0.30–0.70 delta band**, which is what finally stopped the deep-ITM picks.
- **`91d8a8f`** — rate limiting. A full scan issues ~1,800 data + ~760 trading requests and was measured at **~240 data req/min against a 200/min limit.** Nothing surfaced the 429s, because per-symbol chain errors are caught and silently dropped — throttled symbols just vanished from the scan. Now a 190/min sliding window per host, backoff with jitter, `Retry-After` honored, and a 429 parks the whole bucket.
- **`e83c848`, `2850614`, `1e026f8`** — the Claude Remote Control watchdog, unrelated to trading. Notable only because it took three attempts: the background session inherited the `flock` descriptor and held the lock for its whole lifetime, making every later run exit before any health check; cron's `PATH` excluded `~/.local/bin`; and "a pid exists" turned out not to mean "connected", so a transient handshake failure latched into a 25-minute silent outage that the watchdog reported as healthy throughout.

### Phase 4 — Aug 31 to Sep 20: the first real trading, and the only good stretch

**Rule set v1:** 2× profit target, +50% "high profit exit", support/resistance exits,
50% stop, hourly monitor.

The first fills land Aug 31. September opens strong — **equity peaks at $54,247 on
Sep 9, up 8.5%** — on a handful of large winners: INTC +$2,260, ORCL +$1,302,
INTC puts +$1,044, WBD +$1,020, PLTR +$884. Then it gives the gains back.

**30 trades, −$369, 30% win rate.** Roughly break-even, and the best any rule set has
done. Three structural problems surfaced during it:

- **The support/resistance exit was self-defeating.** Levels were computed only in `create_position`, *after* the buy was chosen, from a 20-day window whose loop structurally excludes the latest bar. On a trending stock price sits far past those stale levels, so **the position satisfied its own exit condition the moment it opened.** Measured live, 9% of (symbol, direction) pairs were un-enterable this way. It is what churned GOOGL puts twice in two days.
- **`High Profit Exit` fired at +50% while the profit target sat at +100%,** making the target reachable only on a gap straight past both. Realized winners clustered at +52% and below or +112% and above, with nothing in between.
- **`78b99ec` (Sep 10)** — `get_position()` returning `None` was treated as "closed", which mishandled positions whose entry order simply hadn't filled yet. Now the entry order's own status disambiguates, and the actual closing fill price is looked up from order history.

### Phase 5 — Sep 21 to Sep 28: v2, the best-measuring era

Four commits in two days:

- **`9502dce`** — stop tightened **50% → 30%**, and the monitor split into its own script running **every 15 minutes** instead of hourly.
- **`0d979ae`** — the IV-richness gate. `volatility_percentile_min` existed but nothing read it, leaving only an absolute 15–80% IV band that passes nearly everything. Near-the-money IV is now ranked against the name's own trailing year of realized volatility, rejecting above the 65th percentile — adaptive, because 66% IV is normal for INTC and extreme for NKE. **The same commit switches historical bars from raw to split/dividend adjusted**: raw bars had turned NFLX's split into a 231% one-day move and an 800% volatility reading, corrupting RSI and momentum too.
- **`6b20fef`** — the support/resistance exit and `High Profit Exit` are removed, replaced by **scaling out to a free ride**: at 1.5× entry, sell only the contracts needed to recover the entry cost and let the rest run with no target and no stop. Replayed against hourly bars this turned −$1,011 realized into +$854, with VZ (1.80× peak), T (1.52×) and BAC (1.57×) each flipping from about −$500 to +$200.
- **`6bae410`** — **the 30% stop is reverted to 50% two days after being introduced.** Replaying all 32 closed trades against hourly bars put 30% about **$3k behind** 50%: it forced exits on positions that dipped and then recovered into the largest winners, turning WBD from +$1,644 into −$434 and INTC from +$1,082 into −$282. A sweep of stop levels peaked at 0.5; tighter, looser, and no stop at all all measured worse.

**15 trades, +$634, 40% win rate** — the only profitable era. On 15 trades, that is
roughly one good trade's worth of noise.

### Phase 6 — Sep 29 to Oct 9: v3, and the worst stretch of the project

**`76f1a7f`** — scaling out needs 3+ contracts to sell some and keep a runner, but at
2% risk on a $6–9 premium you can buy one contract, so the free-ride mechanism never
engaged and those positions rode completely unprotected. GOOG ran to +59% and gave it
all back for −$299. Single/small positions now close in full at the scale-out trigger.
The commit message is honest that this **measured about $2k worse** than holding,
because it also caps the single-contract moonshots (INTC peaked at 4.91×, MSFT 3.78×) —
the trade was accepted deliberately, to stop watching +60% round-trip to a loss.

**16 trades, −$5,450, 12% win rate.** Eleven of the sixteen hit the 50% stop. This era
is 60% of the entire project's loss. It is also where the put problem becomes visible:
NVDA puts twice, AVGO, TSLA, AMZN, WMT — all stopped out in a rising market.

### Phase 7 — Oct 10 to Oct 13: v4, breakouts

**`2bd69a8`**, the largest strategy change so far:

- **The breakout regime.** The RSI signal is mean reversion — it buys puts on overbought names — and a stock clearing resistance is overbought by construction, so **the scanner was systematically fading every breakout**, including two NVDA puts in one week at RSI 80+. Replayed over the 31 entries since Sep 21, four were directly against a breakout. Now a name that closed through its 20-day high/low within 5 sessions and still holds beyond it is traded **only with** the break; the opposing side isn't scored, RSI is ignored, and the RSI reversal exit is skipped (it would close the trade on entry). The exit becomes price falling back >1% through the broken level.
- **Broker Greeks.** The feed had been returning delta/gamma/theta/vega per contract all along while the strategy used its own closed-form approximations. Delta now comes from the broker for the moneyness filter and as the probability-of-profit proxy; theta as a fraction of premium is scored so fast-decaying short-dated contracts rank lower.
- **Bollinger Bands(20, 2σ).** Adds to a call near the lower band in a range, and to a breakout trade priced outside the band on the break side. **A put near the upper band earns nothing — that setup lost on all eight entries since late September.**
- Contracts too expensive to size under the risk limit are dropped before the order slot is spent, and every position now records score, regime, band position and Greeks at entry.

**3 trades, +$717.** Far too few to mean anything.

### Phase 8 — Oct 14 to Oct 15: v5, no profit target

**`120e6b1`** removes all three price-based profit exits — the 2× target, the scale-out,
and the full exit for positions too small to scale out. Under 5 contracts that last one
was simply a fixed gain cap, and most positions are 1–3 contracts. **Winners now run
until a signal closes them**; only the 50% stop and the 5-day time-decay exit guard the
downside. In exchange, four Greek exits are added off the live snapshot: delta < 0.20,
delta > 0.85, daily theta > 5% of price, IV down 25% from entry.

Three fixes ride along:

- **Contracts without delta and theta are no longer bought** — the Greek exits read them live, so a contract the feed can't price would be held with those exits blind.
- **The Black-Scholes delta fallback** (for contracts where the feed drops Greeks, which is exactly deep ITM/far OTM where the delta exits matter) approximated N(d1) with `tanh`, about 0.04 off near 0.85. It now uses `erf` and lands within 0.01 of the broker's delta.
- **The RSI reversal exit was comparing RSI against RSI of the last three closes**, which needs 15 prices and therefore always returned 50 — so it fired the instant RSI crossed 70 (calls) or 30 (puts). It now requires RSI two sessions back to have been at the extreme and since turned.

### Phase 9 — Oct 16: the market-hours gate

**`ae9b78f`**, the current HEAD. Cron had been running the scan hourly and the monitor
every 15 minutes **around the clock, with nothing checking the market was open.**
Consequences, all of them real:

- **Orders were queued overnight and at weekends, priced off stale quotes.** KR was bought from a 02:09 scan; CSCO from a Saturday one. CVNA, INTC and GLW likewise.
- **The monitor spent whole weekends failing to sell.** Market sell-to-close orders outside hours are rejected: **1,252 `422 Unprocessable Entity` responses across 13 days**, peaking at 288 in a single day (Sat Sep 13 and Sat Oct 11 — 96 monitor runs × 3 positions, every one rejected). After this commit: **zero.**
- **A false IV-crush exit.** KR was scanned at 02:09 at IV 0.28 and filled at 13:32; its first live IV read was low enough to trip the 25% crush rule **13 minutes after the fill**, for −$120. `entry_iv` is now the first live reading after the fill.
- **A position lost its stop loss.** A queued order fills in the first seconds after the open. The 09:30 monitor found no CVNA position, read the entry order as filled a moment later, and recorded the position as closed — leaving a **live contract with no stop and no exits**. A filled order with no position now re-checks for the position before concluding it closed.

### Phase 10 — Oct 16 to Nov 6: frozen code, continued bleeding

No commits. **24 trades, −$4,601, 17% win rate.** Equity falls from $45,767 to $42,455,
declining every single session in the week of Nov 2–6.

What this phase produced was not a fix but a diagnosis, arrived at on Oct 26 and
restated since: **every change in Phases 5–9 was to how trades exit, while the losses
come from which trades get entered.** 42% of all trades hit the 50% stop; no exit rule
rescues a position entered in the wrong direction. And the per-trade swing of about
±$530 over 8–24 trades per era means the era table cannot distinguish any of those
changes from chance. The stop went 50 → 30 → 50% and the profit exit went 2× →
scale-out → +30% → nothing, each on a handful of trades.

Three more problems were identified in this window and are **still unfixed**:

- **The scan and the monitor overwrite each other's saves** (see incident log).
- **No cooldown.** A contract can be stopped out and re-bought the same morning. On Oct 27 a NFLX put stopped out at the open and the scan bought the identical contract back 40 minutes later. On Nov 4 INTC was sold on momentum divergence at 09:45, re-bought at 11:11, sold again at 12:30 on the same signal, and re-bought at 13:10. **The scan scores INTC as a buy while the exit rule says its momentum is gone** — entries and exits are judged by different criteria, and each round trip pays the spread.
- **Position count grew with no cap.** Seven open at once by Nov 6, each sized to 2% of equity — ~14% exposed, the most ever, which is why Fri Nov 6 cost −$527 with only one trade closing.

---

## 2. Equity curve

Account `PA3J6RJ2RMI1`, funded $50,000 on 2026-08-20. Values are from live account
queries recorded in the supervising sessions; gaps are where no reading was taken.

| Date | Equity | vs start | Note |
|---|---:|---:|---|
| 2026-08-20 | $50,000 | — | account opened; 11 days with no fills |
| 2026-08-31 | ~$50,000 | 0% | first fills |
| **2026-09-09** | **$54,247** | **+8.5%** | **peak** |
| 2026-10-09 (Fri) | $46,542 | −6.9% | |
| 2026-10-11 (Sun) | $46,490 | −7.0% | |
| 2026-10-12 (Mon) | $45,852 | −8.3% | |
| 2026-10-13 (Tue) | $45,832 | −8.3% | |
| 2026-10-14 (Wed) | $46,068 | −7.9% | |
| 2026-10-15 (Thu) | $45,767 | −8.5% | |
| 2026-10-19 (Mon) | $45,686 | −8.6% | |
| 2026-10-23 (Fri) | $45,354 | −9.3% | |
| 2026-10-26 (Mon) | $45,068 | −9.9% | |
| 2026-10-27 (Tue) | $44,606 | −10.8% | |
| 2026-10-28 (Wed) | $44,157 | −11.7% | |
| 2026-10-29 (Thu) | $43,463 | −13.1% | |
| 2026-10-30 (Fri) | $43,357 | −13.3% | |
| 2026-11-02 (Mon) | $43,642 | −12.7% | WMT closed +$1,310 |
| 2026-11-03 (Tue) | $43,478 | −13.0% | |
| 2026-11-04 (Wed) | $43,200 | −13.6% | |
| 2026-11-05 (Thu) | $42,983 | −14.0% | |
| 2026-11-06 (Fri) | $42,455 | −15.1% | |
| **2026-11-09 (current)** | **$42,455** | **−15.1%** | 7 positions open, −$157 unrealized |

Peak-to-current drawdown: **−21.7%**.

---

## 3. Incident log

Each of these was found in production, not in testing. Status is as of 2026-11-09.

| # | Date found | Incident | Cost / evidence | Status |
|---|---|---|---|---|
| 1 | 2026-08-20 | **Scoring disabled for 5 months** by two hardcoded overrides buried in an unrelated 1,200-line commit. Every scan from 2026-03-25 bought the first suitable contract, not the best. | No fills in that window, so no direct P&L cost | Fixed `74041f3` |
| 2 | 2026-08-11 | **Suitability filter commented out** with a dead `return True`. Spread, volume, OI and IV filtered nothing. | — | Fixed `573c233` |
| 3 | 2026-08-19 | **`min_days_to_expiry = 0`** let the scanner buy 0–1 DTE contracts that instantly tripped the time-decay exit. | — | Fixed `bd7b37a` (to 7; **still a placeholder**) |
| 4 | 2026-08-19 | **`quantity` hardcoded to 1**; `max_risk_per_trade` defined and unused. | — | Fixed `bd7b37a` |
| 5 | 2026-08-19 | **Universe hardcoded to 4 tickers** — a testing default cron never overrode. | 9 days of scans on AAPL/MSFT/NVDA/TSLA | Fixed `b50fb6c` |
| 6 | 2026-08-19 | **Deep-ITM selection.** Nothing constrained moneyness; the scanner repeatedly chose the AAPL $110 call at ~$105 premium with the stock near $310. | **180 × 403 Forbidden**, Aug 11–19 | Fixed `d9c1341` (delta band) |
| 7 | 2026-08-19 | **Duplicate orders on one underlying.** Two AAPL calls got through before any guard existed; cancelled by hand. | — | Fixed `bd7b37a` (`_has_open_exposure`) |
| 8 | 2026-08-29 | **Open interest always 0** — Alpaca's snapshots endpoint doesn't return it on any feed, so every contract failed `min_open_interest`. | 11 days of "no opportunities" | Fixed `d9c1341` |
| 9 | 2026-08-29 | **Scan exceeded the API rate limit** (~240 vs 200 req/min) and 429s were invisible: throttled symbols silently vanished from results. | A scan covering 300 of 503 symbols looked identical to a clean one | Fixed `91d8a8f` |
| 10 | 2026-09-10 | **`get_position() is None` treated as "closed"**, mishandling unfilled entry orders. | — | Fixed `78b99ec` |
| 11 | 2026-09-21 | **Raw (unadjusted) price bars.** NFLX's split read as a 231% one-day move and an 800% volatility reading, corrupting RSI and momentum. | — | Fixed `0d979ae` |
| 12 | 2026-09-23 | **Support/resistance exit was self-triggering.** Levels computed after the buy, from a window excluding the latest bar, so trending positions met their own exit on open. **9% of (symbol, direction) pairs were un-enterable.** | Churned GOOGL puts twice in two days | Fixed `6b20fef` (removed) |
| 13 | 2026-09-23 | **`High Profit Exit` at +50% under a +100% target** made the target reachable only on a gap past both. | Winners clustered ≤+52% or ≥+112%, nothing between | Fixed `6b20fef` |
| 14 | 2026-09-29 | **Scale-out never engaged on small positions.** Needs 3+ contracts; 2% risk on a $6–9 premium buys one. Those positions rode unprotected. | GOOG +59% → −$299 | Fixed `76f1a7f`, then superseded by `120e6b1` |
| 15 | 2026-10-10 | **The scanner systematically faded breakouts** — RSI mean reversion buys puts on overbought names, and a breakout is overbought by construction. | Two NVDA puts in a week at RSI 80+; 4 of 31 entries since Sep 21 were against a live breakout | Fixed `2bd69a8` |
| 16 | 2026-10-14 | **RSI reversal exit always compared against a constant 50** (it needed 15 prices and got 3), so it fired the instant RSI crossed 70/30. | — | Fixed `120e6b1` |
| 17 | 2026-10-14 | **`tanh` approximation of N(d1)** was ~0.04 off near delta 0.85 — right at the "deep ITM" exit threshold. | — | Fixed `120e6b1` (`erf`) |
| 18 | 2026-10-16 | **No market-hours gate.** Orders queued overnight/weekends off stale quotes (KR, CSCO, CVNA, INTC, GLW); the monitor's market sell orders were rejected outside hours. | **1,252 × 422 Unprocessable Entity** over 13 days, peaking at 288 on a Saturday | Fixed `ae9b78f` |
| 19 | 2026-10-16 | **False IV-crush exit.** `entry_iv` came from the scan: KR scanned 02:09 at IV 0.28, filled 13:32, exited on "IV crush" 13 minutes later. | −$120 | Fixed `ae9b78f` |
| 20 | 2026-10-16 | **A live position lost its stop.** The 09:30 monitor saw no CVNA position, then read the entry order as filled, and recorded it closed — leaving a real contract with no exits. | Position ran unprotected until repaired by hand | Fixed `ae9b78f` |
| 21 | 2026-10-26 | **The scan and the monitor overwrite each other's saves.** Both start on the hour; the scan loads state at :00 and saves ~10 min later, clobbering the monitor's writes. The exit is resurrected as open, then reconciled at :15. | P&L survives, **exit reasons do not: 10 of 88 closed trades read only "reconciled from fill history"** | **OPEN** |
| 22 | 2026-10-27 | **No cooldown after an exit.** Stopped-out contracts get re-bought the same morning; INTC was sold and re-bought three times in one day on contradicting signals. | Pays the spread each round trip | **OPEN** |
| 23 | 2026-10-29 | **Overnight gaps defeat the 15-minute stop.** NFLX put 2.62 → 1.08 overnight; DOW call 2.40 → 0.68 in two sessions. | **52% of all exits land in the 09:30/09:45 run, totalling −$6,376** | **OPEN** (structural) |
| 24 | 2026-10-20 | **Local P&L is wrong by design.** Entries recorded at the limit price, exits at the price the monitor last read, not fills. | EL: −$352 local vs −$295 broker; file total −$9,069 vs ≈−$7,400 realized | **OPEN** |
| 25 | 2026-11-07 | **No portfolio-level risk cap.** `--max-orders 1` caps new positions per scan, not total exposure. | 7 positions × 2% ≈ 14% at risk | **OPEN** |

---

## 4. Parameter change log

| Date | Parameter | From → To | Rationale | Verdict |
|---|---|---|---|---|
| 2026-08-19 | `min_days_to_expiry` | 0 → 7 | 0-DTE contracts tripped the time-decay exit immediately | Still a placeholder; comment says 21 |
| 2026-08-19 | time-decay exit | 7 → 5 days | keep a runway gap above the new DTE floor | Held |
| 2026-08-29 | `min_delta` / `max_delta` | — → 0.30 / 0.70 | stop deep-ITM selection | Held |
| 2026-08-29 | `requests_per_minute` | ∞ → 190/host | 429s were silently dropping symbols | Held |
| 2026-09-21 | `stop_loss_multiplier` | 0.5 → 0.3 | losers ran half their value down | **Reverted in 2 days** |
| 2026-09-21 | monitor cadence | hourly → 15 min | faster stop checks | Held |
| 2026-09-21 | `max_iv_percentile` | — → 65 | long options bleed on IV decline; absolute 15–80% band passed everything | Held |
| 2026-09-21 | price bars | raw → adjusted | splits corrupted RSI/momentum/vol | Held |
| 2026-09-23 | profit exit | 2× target + 50% high-profit → scale-out at 1.5× | the two rules were mutually unreachable | Superseded Oct 14 |
| 2026-09-23 | support/resistance exit | present → removed | self-triggering on entry | Held |
| 2026-09-23 | `stop_loss_multiplier` | 0.3 → 0.5 | 30% measured ~$3k worse over 32 replayed trades; a level sweep peaked at 0.5 | Held; the one well-tested parameter |
| 2026-09-29 | small-position exit | ride → close in full at 1.5× | the free ride never engaged below 3 contracts | Superseded Oct 14 |
| 2026-10-10 | breakout regime | — → lookback 20 / memory 5 / failure buffer 1% | the scanner was fading every breakout | Held |
| 2026-10-10 | delta source | Black-Scholes → broker | the feed had Greeks all along | Held |
| 2026-10-10 | Bollinger Bands | — → 20 / 2σ | confirm range expansion; put-near-upper-band earns 0 (0/8 historically) | Held |
| 2026-10-14 | all profit exits | present → **none** | a fixed cap kills the rare winners expectancy depends on | Current |
| 2026-10-14 | Greek exits | — → delta <0.20 / >0.85, theta >5%, IV −25% | replace price targets with signal-based exits | Current |
| 2026-10-14 | Greeks required at entry | optional → mandatory | otherwise the Greek exits run blind | Current |
| 2026-10-16 | market-hours gate | none → Alpaca clock, checked twice | stale-quote orders and 422 storms | Current |
| 2026-10-16 | `entry_iv` source | scan → first live read after fill | false IV-crush exits | Current |

---

## 5. Operational statistics

From `logs/alpaca_sandbox_cron.log`, 2026-08-11 to 2026-11-09 (~7,600 runs, 14 MB):

| Month | Runs | Orders placed | Close attempts | "No opportunities" | "Market closed" skips |
|---|---:|---:|---:|---:|---:|
| 2026-08 | 500 | 35 (all rejected) | 0 | 231 | 0 |
| 2026-09 | 2,443 | 91 | 741 | 0 | 0 |
| 2026-10 | 3,720 | 84 | 596 | 0 | 1,519 |
| 2026-11 | 980 | 16 | 6 | 0 | 820 |

Close-attempt counts are inflated by repeated failures: before the market-hours gate the
monitor retried the same rejected sell every 15 minutes, all night and all weekend.

Error census, whole log, excluding the duplicate-exposure guard (which is working as
intended and fires constantly):

| Count | Error |
|---:|---|
| 1,252 | `422 Unprocessable Entity` on `/v2/orders` — sell-to-close outside market hours (none after Oct 16) |
| 180 | `403 Forbidden` on `/v2/orders` — first paper account, Aug 11–19 only |
| 165 | `Sizing to 2.0% max risk per trade yields 0 contracts` — premium too high for the budget |
| ~25 | transient network: connection pool, gateway timeout, read timeout, one 503 |

Since Oct 16 the duplicate guard has blocked **INTC 19 times, WMT 10, NFLX 10, NVDA 8,
FISV/CVNA/BA 5 each** — the scanner keeps re-selecting the same names.

Breadth: **47 distinct symbols traded out of 503 scanned.**

---

## 6. What has never been tried

Worth stating explicitly, because three months of work has circled the same few knobs:

- **A backtest.** There is no backtest harness in this repo. Every parameter was validated, at best, by replaying a few dozen *already-closed* trades against their hourly bars — which cannot evaluate an entry rule, only an exit rule applied to trades the old entry rule chose.
- **A market-regime filter.** Nothing consults the index. Puts are bought on single-name RSI in a market that has been rising, which is the single clearest pattern in the data (15% win rate over 34 put trades).
- **Anything other than long single-leg options.** No spreads, which would cut the theta and IV-crush exposure that currently works against every position.
- **A frozen evaluation window.** No rule set has been left alone for a pre-committed number of trades. The longest any configuration has survived is 30 trades (v1), and that only because nobody was watching closely yet.
- **Entry/exit symmetry.** The scan's momentum score and the monitor's momentum-divergence rule are computed on different windows and disagree in production (see incident 22).
- **Per-position review before the fact.** Every trade since Aug 31 was placed unattended, and reviewed only after it closed.

---

## 7. Appendix: every closed trade

88 records from `alpaca_sandbox_positions.json`, oldest first. P&L is the tracking
file's figure — approximate, per incident 24. "reconciled" means the exit reason was
lost to incident 21.

| # | Entry | Exit | Symbol | Type | Strike | Qty | Entry $ | Exit $ | P&L | Exit reason |
|---:|---|---|---|---|---:|---:|---:|---:|---:|---|
| 1 | 2026-08-31 | 2026-09-09 | AMZN | call | 270 | 1 | 8.13 | 3.95 | −$418 | Stop Loss Hit |
| 2 | 2026-08-31 | 2026-09-14 | BAC | call | 62.5 | 5 | 1.87 | 0.81 | −$530 | Stop Loss Hit |
| 3 | 2026-08-31 | 2026-09-09 | MCD | call | 270 | 1 | 5.55 | 2.38 | −$317 | Stop Loss Hit |
| 4 | 2026-08-31 | 2026-09-09 | INTC | call | 90 | 2 | 4.35 | 15.65 | +$2,260 | Profit Target Hit |
| 5 | 2026-09-01 | 2026-09-08 | ORCL | call | 145 | 1 | 9.28 | 22.30 | +$1,302 | Profit Target Hit |
| 6 | 2026-09-01 | 2026-09-08 | APO | call | 135 | 3 | 2.67 | 2.25 | −$126 | Momentum Divergence — Call Exit |
| 7 | 2026-09-02 | 2026-09-11 | VZ | put | 50 | 14 | 0.66 | 0.30 | −$504 | Stop Loss Hit |
| 8 | 2026-09-02 | 2026-09-03 | PLTR | call | 175 | 1 | 7.91 | 16.75 | +$884 | Profit Target Hit |
| 9 | 2026-09-03 | 2026-09-04 | WMT | call | 110 | 11 | 0.90 | 0.36 | −$594 | Stop Loss Hit |
| 10 | 2026-09-04 | 2026-09-09 | HPE | call | 55 | 5 | 1.76 | 2.60 | +$420 | Time Decay Exit |
| 11 | 2026-09-08 | 2026-09-14 | T | put | 25 | 17 | 0.61 | 0.30 | −$527 | Stop Loss Hit |
| 12 | 2026-09-08 | 2026-09-14 | NOW | put | 130 | 3 | 3.07 | 0.40 | −$801 | Stop Loss Hit |
| 13 | 2026-09-09 | 2026-09-14 | INTC | put | 105 | 2 | 3.98 | 9.20 | +$1,044 | Profit Target Hit |
| 14 | 2026-09-09 | 2026-09-10 | GOOGL | call | 330 | 1 | 6.33 | 5.10 | −$123 | Support Break — Call Exit |
| 15 | 2026-09-09 | 2026-09-14 | NFLX | call | 77 | 7 | 1.39 | 2.11 | +$504 | Time Decay Exit |
| 16 | 2026-09-10 | 2026-09-11 | EIX | call | 60 | 3 | 2.82 | 1.35 | −$441 | Stop Loss Hit |
| 17 | 2026-09-11 | 2026-09-14 | WBD | call | 29 | 14 | 0.72 | 0.25 | −$658 | Stop Loss Hit |
| 18 | 2026-09-11 | 2026-09-28 | ABBV | call | 260 | 1 | 6.73 | 7.60 | +$87 | RSI Reversal Signal |
| 19 | 2026-09-11 | 2026-09-14 | AAPL | put | 325 | 1 | 6.13 | 5.15 | −$98 | reconciled (reason lost) |
| 20 | 2026-09-11 | 2026-09-14 | FCX | call | 75 | 3 | 2.85 | 1.41 | −$432 | Stop Loss Hit |
| 21 | 2026-09-11 | 2026-09-22 | KHC | call | 25 | 15 | 0.66 | 0.32 | −$510 | Stop Loss Hit |
| 22 | 2026-09-14 | 2026-09-15 | INTC | put | 95 | 3 | 3.36 | 2.10 | −$378 | Stop Loss Hit |
| 23 | 2026-09-15 | 2026-09-16 | NKE | call | 38 | 5 | 1.79 | 1.00 | −$395 | reconciled (reason lost) |
| 24 | 2026-09-15 | 2026-09-16 | NFLX | call | 80 | 4 | 2.43 | 1.70 | −$292 | Stop Loss Hit |
| 25 | 2026-09-15 | 2026-09-18 | FCX | call | 70 | 2 | 3.46 | 3.75 | +$58 | Momentum Divergence — Call Exit |
| 26 | 2026-09-16 | 2026-09-17 | CRWD | put | 237.5 | 1 | 7.28 | 5.05 | −$223 | Stop Loss Hit |
| 27 | 2026-09-17 | 2026-09-18 | AAPL | put | 330 | 1 | 7.58 | 4.05 | −$353 | Stop Loss Hit |
| 28 | 2026-09-17 | 2026-09-21 | WBD | call | 29 | 15 | 0.63 | 1.31 | +$1,020 | Profit Target Hit |
| 29 | 2026-09-18 | 2026-09-18 | NFLX | call | 72.5 | 3 | 2.53 | 2.50 | −$9 | Support Break — Call Exit |
| 30 | 2026-09-18 | 2026-09-23 | AMZN | call | 260 | 1 | 6.59 | 4.40 | −$219 | Stop Loss Hit |
| 31 | 2026-09-21 | 2026-09-21 | HOOD | put | 115 | 1 | 5.72 | 3.95 | −$177 | Stop Loss Hit |
| 32 | 2026-09-21 | 2026-09-28 | PFE | call | 28 | 7 | 0.55 | 0.94 | +$735 | RSI Reversal Signal |
| 33 | 2026-09-21 | 2026-09-22 | CRM | call | 240 | 1 | 9.38 | 6.00 | −$338 | Stop Loss Hit |
| 34 | 2026-09-21 | 2026-09-21 | GOOGL | put | 350 | 1 | 8.06 | 7.65 | −$41 | Resistance Break — Put Exit |
| 35 | 2026-09-21 | 2026-09-22 | GOOGL | put | 350 | 1 | 8.18 | 6.00 | −$218 | Resistance Break — Put Exit |
| 36 | 2026-09-22 | 2026-09-25 | GOOG | put | 340 | 1 | 7.94 | 4.95 | −$299 | Stop Loss Hit |
| 37 | 2026-09-22 | 2026-09-24 | CRM | call | 240 | 1 | 6.96 | 7.95 | +$99 | Momentum Divergence — Call Exit |
| 38 | 2026-09-24 | 2026-09-25 | MSFT | call | 495 | 1 | 6.63 | 19.20 | +$1,257 | Profit Target Hit |
| 39 | 2026-09-24 | 2026-09-28 | AMZN | call | 250 | 1 | 2.85 | 2.06 | +$211 | Time Decay Exit |
| 40 | 2026-09-24 | 2026-09-28 | ORCL | call | 140 | 1 | 6.62 | 3.30 | −$332 | reconciled (reason lost) |
| 41 | 2026-09-25 | 2026-09-30 | PLTR | put | 190 | 1 | 6.18 | 5.65 | −$53 | Momentum Divergence — Put Exit |
| 42 | 2026-09-25 | 2026-10-01 | AAPL | put | 335 | 1 | 5.49 | 8.65 | +$316 | Profit Exit — position too small to scale out |
| 43 | 2026-09-25 | 2026-10-02 | TGT | call | 160 | 2 | 3.62 | 1.80 | −$364 | reconciled (reason lost) |
| 44 | 2026-09-28 | 2026-09-30 | VRT | call | 260 | 1 | 8.95 | 4.45 | −$450 | Stop Loss Hit |
| 45 | 2026-09-28 | 2026-10-06 | AMZN | call | 250 | 1 | 5.42 | 8.30 | +$288 | Profit Exit — position too small to scale out |
| 46 | 2026-09-29 | 2026-09-30 | ORCL | call | 145 | 2 | 4.93 | 2.57 | −$472 | Momentum Divergence — Call Exit |
| 47 | 2026-09-29 | 2026-10-02 | TSLA | call | 360 | 1 | 9.90 | 16.80 | +$690 | Profit Exit — position too small to scale out |
| 48 | 2026-09-30 | 2026-10-01 | NFLX | call | 70 | 6 | 1.48 | 0.71 | −$462 | reconciled (reason lost) |
| 49 | 2026-09-30 | 2026-10-02 | NVDA | put | 230 | 2 | 4.00 | 1.51 | −$498 | Stop Loss Hit |
| 50 | 2026-10-02 | 2026-10-06 | NVDA | put | 235 | 2 | 4.51 | 1.71 | −$560 | Stop Loss Hit |
| 51 | 2026-10-05 | 2026-10-06 | AVGO | put | 350 | 1 | 7.67 | 3.80 | −$387 | Stop Loss Hit |
| 52 | 2026-10-05 | 2026-10-09 | TSLA | put | 370 | 1 | 6.49 | 2.63 | −$386 | Stop Loss Hit |
| 53 | 2026-10-06 | 2026-10-07 | COF | call | 200 | 2 | 3.38 | 0.05 | −$666 | Stop Loss Hit |
| 54 | 2026-10-06 | 2026-10-09 | AMZN | put | 255 | 2 | 4.42 | 2.17 | −$450 | Stop Loss Hit |
| 55 | 2026-10-07 | 2026-10-12 | NVDA | put | 235 | 1 | 2.74 | 6.40 | +$648 | Time Decay Exit |
| 56 | 2026-10-07 | 2026-10-09 | T | call | 25 | 12 | 0.79 | 0.21 | −$696 | Stop Loss Hit |
| 57 | 2026-10-07 | 2026-10-08 | UBER | call | 69 | 8 | 1.21 | 0.56 | −$520 | Stop Loss Hit |
| 58 | 2026-10-08 | 2026-10-09 | COIN | call | 180 | 2 | 4.50 | 2.59 | −$382 | Momentum Divergence — Call Exit |
| 59 | 2026-10-08 | 2026-10-12 | HOOD | call | 110 | 3 | 3.00 | 1.85 | −$345 | Time Decay Exit |
| 60 | 2026-10-08 | 2026-10-12 | WMT | put | 110 | 8 | 1.19 | 0.40 | −$632 | Stop Loss Hit |
| 61 | 2026-10-09 | 2026-10-12 | INTC | call | 110 | 2 | 3.18 | 1.52 | −$332 | Stop Loss Hit |
| 62 | 2026-10-10 | 2026-10-15 | CSCO | call | 120 | 1 | 6.06 | 3.00 | −$306 | Stop Loss Hit |
| 63 | 2026-10-12 | 2026-10-14 | INTC | call | 105 | 1 | 6.97 | 6.40 | −$57 | Momentum Divergence — Call Exit |
| 64 | 2026-10-12 | 2026-11-02 | WMT | call | 115 | 2 | 3.55 | 8.95 | +$1,080 | Deep In The Money — leverage spent |
| 65 | 2026-10-14 | 2026-10-15 | HOOD | put | 100 | 1 | 7.69 | 6.75 | −$94 | Failed Breakout — back above support |
| 66 | 2026-10-14 | 2026-10-16 | CVNA | put | 56 | 1 | 4.93 | 4.00 | −$93 | Failed Breakout — back above support |
| 67 | 2026-10-15 | 2026-10-15 | KR | call | 61 | 2 | 3.85 | 3.25 | −$120 | IV Crush Exit |
| 68 | 2026-10-15 | 2026-10-20 | PG | call | 155 | 1 | 6.58 | 5.00 | −$158 | IV Crush Exit |
| 69 | 2026-10-16 | 2026-10-19 | EL | call | 102 | 1 | 6.12 | 2.60 | −$352 | Stop Loss Hit |
| 70 | 2026-10-19 | 2026-10-30 | NVDA | call | 220 | 1 | 8.36 | 4.00 | −$436 | Stop Loss Hit |
| 71 | 2026-10-19 | 2026-10-21 | SMCI | put | 35 | 2 | 3.07 | 1.16 | −$382 | Stop Loss Hit |
| 72 | 2026-10-20 | 2026-10-26 | IBM | call | 250 | 1 | 8.20 | 10.00 | +$180 | IV Crush Exit |
| 73 | 2026-10-21 | 2026-10-22 | NFLX | put | 65 | 4 | 1.96 | 1.32 | −$256 | reconciled (reason lost) |
| 74 | 2026-10-22 | 2026-10-26 | KR | call | 65 | 2 | 3.23 | 2.45 | −$156 | Theta Burn Exit |
| 75 | 2026-10-22 | 2026-10-26 | DOW | call | 30 | 3 | 2.33 | 2.90 | +$171 | Deep In The Money — leverage spent |
| 76 | 2026-10-26 | 2026-10-28 | FCX | put | 69 | 1 | 6.45 | 4.65 | −$180 | Momentum Divergence — Put Exit |
| 77 | 2026-10-26 | 2026-10-28 | DOW | call | 32.5 | 3 | 2.40 | 0.71 | −$507 | Stop Loss Hit |
| 78 | 2026-10-26 | 2026-10-27 | NFLX | put | 65 | 3 | 2.62 | 1.08 | −$462 | Stop Loss Hit |
| 79 | 2026-10-27 | 2026-10-28 | NFLX | put | 65 | 3 | 2.42 | 3.20 | +$234 | reconciled (reason lost) |
| 80 | 2026-10-27 | 2026-10-29 | BA | put | 170 | 1 | 6.44 | 3.05 | −$339 | reconciled (reason lost) |
| 81 | 2026-10-28 | 2026-10-30 | T | put | 21.5 | 8 | 1.03 | 0.52 | −$408 | Momentum Divergence — Put Exit |
| 82 | 2026-10-29 | 2026-10-29 | GEN | call | 28 | 7 | 1.13 | 0.55 | −$406 | reconciled (reason lost) |
| 83 | 2026-10-29 | 2026-11-04 | GOOG | put | 280 | 1 | 6.87 | 4.30 | −$257 | Theta Burn Exit |
| 84 | 2026-10-29 | 2026-10-30 | SYF | call | 75 | 2 | 3.76 | 2.15 | −$322 | Failed Breakout — back below resistance |
| 85 | 2026-10-30 | 2026-11-03 | INTC | put | 70 | 1 | 6.52 | 10.35 | +$383 | reconciled (reason lost) |
| 86 | 2026-11-03 | 2026-11-04 | INTC | put | 60 | 1 | 5.00 | 4.80 | −$20 | Momentum Divergence — Put Exit |
| 87 | 2026-11-04 | 2026-11-04 | INTC | put | 60 | 1 | 4.64 | 3.45 | −$119 | Momentum Divergence — Put Exit |
| 88 | 2026-11-04 | 2026-11-06 | GM | call | 95 | 2 | 3.76 | 1.25 | −$502 | Stop Loss Hit |

**Total: 88 trades, −$9,069.** 22 winners (+$13,871), 66 losers (−$22,940).
