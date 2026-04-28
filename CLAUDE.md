# CLAUDE.md — Momentum Rotation Project

Inherits the vault `C:\dev\CLAUDE.md`. Project-specific rules and locked decisions below.

## Locked design decisions

- **Build script**: `scripts/pipeline.py` (vault standard). Engine: `scripts/backtest.py`.
- **Cron**: monthly at 06:00 UTC on day 2 of each calendar month, plus daily idempotent safety-net runs at 06:00 UTC on days 3–5 that only write if the current month's data is missing or stale. Handles month-start US market holidays without exact calendar logic.
- **Cash proxy**: synthesised from ^IRX yield (13-week T-bill, daily-compounded). BIL is not used.
- **"One survivor" rule**: hold the survivor at 50% with 50% in cash. Flagged as my interpretation, not Teo's, in the assumptions footer.
- **"Zero survivors" rule**: 100% cash.

## Mode B composite (locked)

Equal weight 1/3 each:

- `breadth = count_above_200dma / 4 × 100`
- `momentum_strength = avg ROC of qualifying ETFs, winsorised at [0%, 40%], rescaled linearly to 0–100`
- `leader_trend_strength = avg distance of top-2 qualifying ETFs above their 200-day SMA, winsorised at [0%, 25%], rescaled linearly to 0–100`

Allocation map: `[0,25)→25%, [25,50)→50%, [50,75)→75%, [75,100]→100%`. Inclusive lower, exclusive upper, 100 snaps into the top band.

Reasoning for replacing dispersion with leader trend strength: dispersion misfires both ways — high in 2022-Q1-style transitions (low conviction, not high), low in 2017-style uniform uptrends (clear regime, not unclear). Leader trend strength scores the assets the strategy is about to hold, is independent of the other two signals, and naturally de-risks whipsaw.

## Replication tolerance vs Teo

CAGR ±200 bps. Max drawdown ±500 bps. Outside that band triggers a diagnosis pass (dividend treatment, rebalance day convention, ROC window, warmup convention) before claiming replication.

## Inception dates (verified 2026-04-28)

| Ticker | Yahoo first trade | Source 2 | Note |
|---|---|---|---|
| GLD | 2004-11-18 | Wikipedia: November 2004 | Aligned |
| SPY | 1993-01-29 | Wikipedia: 1993-01-22 | Yahoo daily bars start 5 trading days after listing — known data quirk, not material |
| TLT | 2002-07-30 | Memo (well-known launch 2002-07-22) | Same Yahoo lag |
| DBC | 2006-02-06 | Memo: 2006-02-02 | Same Yahoo lag |

**Stooq cross-check**: free CSV endpoint now requires an API key (verified 2026-04-28). Falling back to Yahoo + memo/Wikipedia for inception dates and to Yahoo alone for historical price values. This single-source dependency for prices is documented in the assumptions footer of the dashboard.

## First eligible signal date

DBC's first month-end close is 2006-02-28. With a 12-month ROC requirement, the first eligible signal date is **2007-02-28**, with first execution at the close of **2007-03-01**. The backtest equity curve starts at $1.00 on 2007-03-01.

Teo's "since 2006" claim implies either (a) holding cash through 2006 with the strategy switching on in early 2007, or (b) a shorter ROC window during warmup. We model (a). This may explain a portion of any residual gap to Teo's 535% cumulative figure.

## Data integrity rules (project-specific overlays)

- Flag any month where the data fetch returns < 21 trading days as a potential issue, do not silently proceed.
- All dates shown in UI must be verified against the actual calendar weekday using a date library before render.
- No transcription of numbers from this README into UI footnotes — both should read from the same source-of-truth JSON.
