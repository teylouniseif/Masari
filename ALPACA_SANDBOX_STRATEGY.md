# Alpaca Sandbox Options Strategy

What actually runs hourly via cron (`scripts/run_alpaca_sandbox_hourly.sh` → `test_alpaca_optionsplay_sandbox_execution.py --submit`) against the Alpaca paper account. This describes the live code path, not the aspirational design in `OPTIONS_ANALYSIS_README.md` (some numbers below differ from that doc — this one reflects `investment_strategies/options_buy_strategy.py` as currently configured).

## Pipeline

1. **Scan** (`OptionsBuyStrategy.find_best_opportunities`, symbols default `AAPL MSFT NVDA TSLA`, overridable via `--symbols`)
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

⚠️ `min_days_to_expiry = 7` is marked in-code as a **testing value**; the comment says raise to 21 before going live. It must stay above 5, since the time-decay exit fires at `dte_at_entry - days_held <= 5` — anything entered at ≤5 DTE exits almost immediately.

Both calls and puts are evaluated per strike; RSI(14) and a 20-day momentum score feed the scoring function below.

## Scoring

`_calculate_opportunity_score` scores each candidate from RSI alignment, momentum, risk/reward, probability of profit, time-value ratio, and volume/OI. It was silently disabled from 2026-03-25 (commit `01cee69`, "adding sandbox") through 2026-08-20 via two hardcoded overrides bundled into an unrelated 1200-line commit with no explanation: `score = 0` in `analyze_stock_for_options`, and the scorer itself overwriting its own result with `score = 80` on its last line. For ~5 months, every hourly run picked "first suitable contract found," not the best one. **Re-enabled 2026-08-20** — both overrides removed.

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
1. **Profit target** — price ≥ `entry_price × 2.0`
2. **Stop loss** — price ≤ `entry_price × 0.5`
3. **Time decay** — `dte_at_entry - days_held ≤ 5`
4. **High profit** — unrealized P&L ≥ 50% of max risk
5. **Technical exit** — any of:
   - RSI reversal (crosses back from >70 for calls / <30 for puts)
   - Support/resistance break against the position (2% threshold)
   - Momentum divergence from entry momentum (30% relative move)

If a tracked position is no longer held at Alpaca at all (expired, exercised, or closed outside this tool), it's reconciled as closed with `final_pnl = None` rather than treated as an exit signal.

## Risk parameters

- Max risk per trade: 2% of account equity (premium paid = full risk on a long option)
- Profit target: 2:1 (200% of premium)
- Stop loss: 50% of premium
- No portfolio-level risk cap — each hourly scan can open one new position (`--max-orders`) independent of how many are already open, aside from the per-underlying duplicate guard

## Known gaps

- `min_market_cap` criterion is defined but never checked.
- No hard cap on total concurrent open positions or aggregate portfolio risk — only per-underlying dedup.
- `min_days_to_expiry=7` is explicitly a placeholder per its own code comment.
