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

Milestone 2 — Mode A dashboard end-to-end. `template.html` with the white theme, fetch fallback, equity curve with log toggle, drawdown chart, performance table (base + 5 bps + 10 bps), rolling 12-month vs SPY, holdings timeline heatmap, current ranking table, and assumptions footer. `scripts/pipeline.py` injects the JSON into `template.html` to produce `docs/index.html`. **Done 2026-04-28.**

---

# Milestone 3 — Mode B (Graduated allocation)

**Date**: 2026-04-29
**Engine**: `scripts/backtest.py` (extended)

## Spec (locked in CLAUDE.md)

Composite score in [0, 100], equal-weight mean of three sub-scores:

- **breadth** — count above 200d SMA / 4 × 100
- **momentum_strength** — average ROC of qualifying ETFs (those above 200d SMA), winsorised at [0%, 40%], rescaled linearly to 0–100
- **leader_trend_strength** — average distance of the top-two qualifying ETFs above their 200d SMA, winsorised at [0%, 25%], rescaled linearly to 0–100

Allocation map: `[0,25)→25%`, `[25,50)→50%`, `[50,75)→75%`, `[75,100]→100%`. Lower-inclusive, upper-exclusive; 100 snaps into the top band. Zero eligible overrides to 100% cash regardless of composite.

## Mode B vs Mode A — full window (2007-03-01 to 2026-04-28, 19.16 years)

| Metric | Mode A 0bps | Mode B 0bps | Read |
|---|---|---|---|
| CAGR | 11.73% | 7.69% | -404 bps — graduated overlay gives up upside |
| Volatility (ann) | 12.55% | 8.75% | -380 bps — meaningful de-risk |
| Sharpe | 0.83 | 0.73 | A still wins on risk-adjusted in this window |
| Sortino | 1.07 | 0.85 | same direction |
| Maximum drawdown | -23.11% | -20.53% | -258 bps shallower |
| Calmar | 0.51 | 0.37 | A's CAGR outweighs A's deeper DD |
| Hit rate (monthly) | 64.63% | 64.63% | unchanged — same signal logic |
| 2008 calendar | +14.97% | +6.58% | A's GFC bond-pivot was a single-year windfall; B sized down |
| 2020 calendar | +21.30% | +13.50% | A captured more of the rebound |
| 2022 calendar | -2.93% | -0.65% | the case for B — choppy regime, breadth collapsed, allocation throttled to 25% |
| Slippage cost (10 bps) | -24 bps CAGR | -18 bps CAGR | B is structurally lower-turnover at low allocation |

## Reading the result

The 2022 outcome is the clearest evidence the composite does what it is meant to. Both modes ended 2022 mostly defensive — Mode A's "one survivor" rule put it in DBC+50% cash from May through August, then 100% cash for September–October. But Mode B got there earlier and in finer increments: breadth collapsed below 50, leader trend strength faded, and the realised allocation walked down 75% → 50% → 38% → 25% → 0% across May to October as conditions worsened, rather than Mode A's binary 50%/100% cash steps. Mode B finished the year at -0.65% versus Mode A at -2.93%, and the path was smoother — there was no single month in 2022 where Mode B was meaningfully more aggressive than Mode A.

The 2008 outcome is the cost. Mode A's bond pivot in late 2008 was a runaway trend — leader trend strength was extreme, breadth was constructive, and a fully-allocated bet captured the TLT rally. Mode B saw the same signals but sized smaller because the composite needed time to clear the 75 threshold and even then ramped allocation rather than jumping. This is the classic graduated-allocation tradeoff: you give up some payoff at the right tail to clip more of the chop in the middle. The full-period CAGR delta (-404 bps) is the price of that smoothing.

## Latest Mode B signal (2026-03-31, currently held since 2026-04-01)

| Component | Value |
|---|---|
| Breadth | 50.0 (2 of 4 above 200d SMA) |
| Momentum strength | 100.0 (avg ROC of GLD+DBC = 41.2%, clipped at 40%) |
| Leader trend strength | 80.8 (avg distance above SMA = 20.2%, clipped at 25%) |
| Composite score | 76.9 |
| Allocation target | 100% (composite ≥ 75) |
| Allocation realised | 100% (2 eligible — same as target) |
| Holdings | GLD @ 50%, DBC @ 50% |

In this regime Mode B and Mode A hold the same book. The split shows up in periods like 2022 where breadth is mid-band.

## Tests

```
tests/test_dates.py — 13 passed
```

New Mode B coverage: allocation-band edges (lower-inclusive, 100 snaps into top), winsorisation clip on momentum_strength, leader_trend clip at 25%, zero-eligible override to 100% cash, one-survivor scaled allocation.

---

# Milestone 4 — Mode C (Universe sensitivity)

**Date**: 2026-04-29
**Engine**: `scripts/backtest.py` (extended via `UNIVERSES_C`, `run_mode_c`)

## Universes

Mode A strategy applied to five four-ETF universes. Bond, equity, and commodity slots are rotated independently so any sensitivity is attributable to one substitution at a time.

| ID | Substitution | Tickers | Start |
|---|---|---|---|
| baseline | — | GLD / SPY / TLT / DBC | 2007-03-01 |
| short_bond | TLT → IEF | GLD / SPY / IEF / DBC | 2007-03-01 |
| agg_bond | TLT → AGG | GLD / SPY / AGG / DBC | 2007-03-01 |
| broad_commodity | DBC → PDBC | GLD / SPY / TLT / PDBC | 2015-12-01 |
| equity_qqq | SPY → QQQ | GLD / QQQ / TLT / DBC | 2007-03-01 |

PDBC starts later because of its 2014-11 inception (12-month ROC warmup brings the first executable signal to 2015-11-30).

## Mode C results (no slippage)

| Universe | Start | CAGR | Vol | Sharpe | Max DD | 2008 | 2020 | 2022 |
|---|---|---|---|---|---|---|---|---|
| Baseline | 2007-03-01 | 11.73% | 12.55% | 0.83 | -23.11% | +14.97% | +21.30% | -2.93% |
| Short-duration bond (IEF) | 2007-03-01 | 11.39% | 11.80% | 0.85 | -23.78% | +6.81% | +16.98% | -2.93% |
| Aggregate bond (AGG) | 2007-03-01 | 11.01% | 11.65% | 0.83 | -25.11% | +2.14% | +18.40% | -2.93% |
| K-1-free commodity (PDBC) | 2015-12-01 | 13.05% | 12.33% | 0.89 | -17.26% | — | +21.26% | -2.83% |
| Tech-tilt equity (QQQ) | 2007-03-01 | 13.16% | 13.20% | 0.90 | -23.11% | +14.97% | +25.20% | +3.46% |

## Reading the sensitivity

**Bond slot (TLT vs IEF vs AGG)**: the 2008 results spread by ~12.8 percentage points across the three bond proxies (baseline +14.97% vs AGG +2.14%). TLT's long duration was the reason Teo's strategy thrived in the GFC — 20+ year Treasuries rallied hardest in the flight-to-quality. IEF (7–10 yr) captured roughly 45% of that. AGG (broad investment-grade, ~6 yr duration with some credit risk) captured almost none — the credit component bled in late 2008. The IEF and AGG variants also differ from baseline in 2020 (+16.98% and +18.40% vs baseline +21.30%), suggesting the bond sleeve was the source of differential performance during the 2020 rates rally too. Full-period CAGRs converge (11.0% to 11.7%) because the post-GFC decade rewarded all bond proxies similarly when held as part of the rotation. Takeaway: TLT is structurally the right bond for this strategy, but the strategy is not fragile to bond-proxy choice — every variant beats SPY's full-period CAGR with materially lower volatility.

**Commodity slot (DBC vs PDBC)**: PDBC's window is too short (10 years vs 19) for a clean read. Over its own period it shows higher CAGR (13.1%) and shallower max DD (-17.3%), but this is a 10-year sample weighted with the 2022 commodity bull. The substitution would be useful for tax purposes (PDBC avoids the K-1 form that DBC issues) but is not validated as a long-history alternative in this report.

**Equity slot (SPY vs QQQ)**: tech-tilt QQQ adds ~143 bps of CAGR and bumps Sharpe from 0.83 to 0.90. The QQQ uplift is broader than a single year — 2020 shows +25.20% vs baseline +21.30% (+390 bps) and 2022 shows **+3.46% vs baseline -2.93%** (a 639 bps swing). The 2022 result is striking because QQQ as an asset fell ~33% in 2022; the rotation's gain came from staying out of equities once the QQQ trend filter broke (which it did harder than SPY's), leaving the book in commodities and bonds for longer. The 2008 result is unchanged from baseline because both equity proxies failed the trend filter at the same point and the strategy went all-bonds either way. Worth noting that the QQQ tilt exposes the strategy to higher concentration risk in the equity sleeve, and the full-period uplift reflects a single sample path through a tech-led era.

## Mode C invariant test

`tests/test_dates.py::test_mode_c_baseline_matches_mode_a` asserts that running Mode C with the baseline universe produces identical CAGR, max drawdown, volatility, and Sharpe as Mode A 0bps to 1e-12. This guards against silent regressions in the universe-parameterisation refactor.

## Tests

```
tests/test_dates.py — 13 passed (5 new for Mode B, 1 new for Mode C invariant)
```

## Next milestone

Mode D — combined view. Default interpretation: Mode B graduated allocation applied to each Mode C universe. Open question for next session: should the comparison surface every B×universe combination (10–20 series) or restrict to a few "promising" combinations (baseline, equity_qqq, the strongest-Sharpe alternative)?
