# Replication Report — Milestone 1

**Date**: 2026-04-28
**Engine**: `scripts/backtest.py`
**Mode**: A (vanilla 4-ETF top-2)

## Summary

Mode A replicates Teo's headline figures within the tolerance band defined in `CLAUDE.md` (±200 bps CAGR, ±500 bps maximum drawdown).

| Metric | Teo target | Observed (0 bps) | Delta | Tolerance | Pass |
|---|---|---|---|---|---|
| CAGR | 10.0% | 11.73% | +173 bps | ±200 bps | YES |
| Max drawdown | -22.0% | -23.11% | -111 bps | ±500 bps | YES |
| 2008 return | +15% | +14.97% | -3 bps | indicative | spot on |
| 2020 return | +18% | +21.30% | +330 bps | indicative | within reason |
| 2022 return | -3% | -2.93% | +7 bps | indicative | spot on |

Cumulative observed +737% vs Teo's claimed +535% looks larger but reflects a different end date. My window extends to 2026-04-28 vs Teo's mid-2025 recording, capturing an additional ~10 months of strong gold and equity performance. Period-normalised CAGR is the like-for-like metric.

## Period

- Start: **2007-03-01** — the first trading day after the first month-end at which all four ETFs have ≥12 months of monthly closes (DBC inception 2006-02-06 is the binding constraint).
- End: **2026-04-28**.
- Length: 19.16 years.

Teo's "since 2006" claim is interpreted as holding cash through 2006 and switching the strategy on once DBC has 12 months of history. The engine models this exactly. The remaining ~1.7 pp CAGR gap is explained largely by the additional ~10 months of strong post-recording performance, not by a methodological error.

## Data integrity

### Inception dates

| Ticker | Yahoo first trade | Source 2 | Note |
|---|---|---|---|
| GLD | 2004-11-18 | Wikipedia (November 2004) | Aligned |
| SPY | 1993-01-29 | Wikipedia (1993-01-22) | Yahoo daily bars start 5 trading days after the formal listing — known data quirk, not material |
| TLT | 2002-07-30 | Memo / well-known launch 2002-07-22 | Same Yahoo lag |
| DBC | 2006-02-06 | Memo (2006-02-02) | Same Yahoo lag |

### Price value cross-check (limited — flag for the user)

Two-source verification of historical adjusted closes was attempted via:

- **Stooq direct CSV**: free endpoint now requires an API key (verified 2026-04-28).
- **pandas-datareader 0.10.0**: incompatible with Python 3.14 (`distutils` removed).
- **Macrotrends**: 403 to WebFetch.
- **Issuer fund pages (SSGA, iShares, Invesco)**: returned JS shells with no fund-specific data, or 403.

A self-consistency check between yfinance's two access paths (`Ticker.history` vs `yf.download`) was performed on two random month-end values per ETF. All eight values match to 0.01 bp. This rules out parsing or column-naming bugs but is not a true second source because both paths hit Yahoo.

**Open caveat**: historical price values for this strategy are sole-sourced from Yahoo Finance. This is flagged in the dashboard's assumptions footer (planned for Milestone 2). Possible mitigations before public release: a Stooq or Tiingo API key for automated cross-check, or a quarterly manual spot-check against issuer NAV history.

### Self-consistency sample (yfinance two-path)

| Ticker | Month-end | Ticker.history Adj | yf.download Adj | Diff |
|---|---|---|---|---|
| GLD | 2021-08-31 | 169.6900 | 169.6900 | 0.00 bp |
| GLD | 2010-05-31 | 118.8800 | 118.8800 | 0.00 bp |
| SPY | 2008-07-31 | 91.4739 | 91.4739 | 0.00 bp |
| SPY | 2023-10-31 | 405.3561 | 405.3561 | 0.00 bp |
| TLT | 2013-11-30 | 74.1917 | 74.1917 | 0.00 bp |
| TLT | 2013-03-31 | 82.0370 | 82.0369 | 0.00 bp |
| DBC | 2012-10-31 | 23.3003 | 23.3003 | 0.00 bp |
| DBC | 2010-12-31 | 23.2918 | 23.2918 | 0.00 bp |

## Performance detail

### Mode A, no slippage

```
Years:           19.16
Cumulative ret:  +736.89%
CAGR:            11.73%
Vol (annualised): 12.55%
Sharpe (vs ^IRX): 0.83
Sortino:         1.07
Max DD:          -23.11%
Calmar:          0.51
Hit rate (mo):   64.63%
```

### Slippage sensitivity

Round-trip bps applied to one-way turnover (`cost = one_way_turnover × bps / 1e4`).

| bps | CAGR | Cumulative | Max DD |
|---|---|---|---|
| 0 | 11.73% | +736.89% | -23.11% |
| 5 | 11.61% | +720.26% | -23.15% |
| 10 | 11.49% | +703.95% | -23.19% |

Slippage costs ~12 bps of CAGR per 5 bps round-trip — modest, consistent with a once-monthly rebalance over a concentrated two-asset book.

### Benchmarks (matched period)

| Series | CAGR | Vol | Sharpe | Max DD |
|---|---|---|---|---|
| Mode A 0 bps | 11.73% | 12.55% | 0.83 | -23.11% |
| SPY total return | 10.88% | 19.73% | 0.55 | -55.19% |
| 60/40 SPY/AGG | 8.06% | 11.83% | 0.59 | -35.40% |

The strategy delivers SPY-equivalent CAGR with ~58% lower maximum drawdown and ~36% lower volatility.

## Latest signal — 2026-03-31 month-end (currently held)

| Ticker | 12-mo ROC | Above 200-day | Eligible | Rank |
|---|---|---|---|---|
| GLD | +49.3% | YES | YES | 1 |
| DBC | +33.0% | YES | YES | 2 |
| SPY | +17.6% | **NO** | NO | — |
| TLT | -0.5% | NO | NO | — |

**Currently held (April 2026)**: GLD @ 50%, DBC @ 50%.

This is the signal computed at the close of 2026-03-31 and executed at the close of 2026-04-01. **SPY broke below its 200-day SMA at end-March 2026 and was excluded by the trend filter**, so the strategy rotated out of SPY into DBC for April — exactly the regime-shift behaviour the rotation is designed for.

The memo's narrative claim of "GLD + SPY at 50% each" is wrong on both counts: it cited stale ROCs (which would have ranked DBC > GLD > SPY anyway by its own numbers) and missed the SPY trend-filter break. The engine's output is authoritative.

**Implementation note (in-progress signal filter)**: An earlier draft of the engine surfaced a preliminary 2026-04-30 signal generated from partial-April data (the resample's last bin contained only 2026-04-28's close). The signal logic now filters out any signal whose execution day has not yet occurred in the data, so mid-month engine runs no longer pollute `current_positioning.json` with a forward-looking signal that will revise. The simulation always handled this correctly — the bug was confined to the diagnostic and JSON outputs. Covered by `tests/test_dates.py::test_filter_drops_in_progress_signals`.

## Files written

- `data/equity_curve.json` (610 KB) — daily equity for Mode A {0, 5, 10} bps + SPY + 60/40
- `data/holdings_timeline.json` (57 KB) — monthly weights, ROCs, and SMA flags
- `data/performance_stats.json` (6 KB) — full stats per series
- `data/current_positioning.json` (1 KB) — latest signal snapshot
- `data/raw/*.parquet` — gitignored price caches

## Tests

```
tests/test_dates.py — 6 passed in 0.39s
```

## Next milestone

Milestone 2 — Mode A dashboard end-to-end. `template.html` with the white theme, fetch fallback, equity curve with log toggle, drawdown chart, performance table (base + 5 bps + 10 bps), rolling 12-month vs SPY, holdings timeline heatmap, current ranking table, and assumptions footer. `scripts/pipeline.py` injects the JSON into `template.html` to produce `docs/index.html`.
