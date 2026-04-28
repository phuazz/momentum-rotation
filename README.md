# Momentum Rotation Dashboard

Backtest dashboard for a four-asset monthly momentum rotation system, modelled on Rayner Teo's framework.

Public URL (planned): https://phuazz.github.io/momentum-rotation/

## Status

**Milestone 1 — complete.** Mode A replicates Teo's headline figures within tolerance: CAGR 11.73% vs target 10% (+173 bps), maximum drawdown -23.11% vs target -22% (-111 bps), 2008 +14.97% / 2020 +21.30% / 2022 -2.93%. See [REPLICATION_REPORT.md](REPLICATION_REPORT.md) for the detail. No UI yet — Milestone 2 builds the dashboard.

## Strategy specification

Universe: GLD, SPY, TLT, DBC. Cash is synthesised from the ^IRX 13-week T-bill yield.

Signal (monthly):

1. On the prior month-end close, compute trailing 12-month ROC for each ETF.
2. Filter to ETFs trading above their 200-day SMA at the prior month-end.
3. Rank survivors by ROC descending.
4. Hold the top two at 50% / 50%.
5. If only one survives, hold it at 50% with 50% in cash. If none survive, 100% cash. *(My interpretation of the unspecified edge cases — flagged in the assumptions footer.)*
6. Execute at the close of the first trading day of the next month.

Returns are total return, computed from Yahoo Finance adjusted close.

## Modes

| Mode | Description |
|---|---|
| A | Vanilla replication of Teo's spec. Base case. |
| B | Graduated allocation overlay (planned). Equal-weight 1/3 composite of breadth, momentum strength, leader trend strength → 25/50/75/100% risk-asset exposure. |
| C | Universe sensitivity (planned). Alt bond, commodity, equity, gold proxies. |
| D | Combined view (planned). |

## Run locally

```bash
pip install -r requirements.txt
python scripts/backtest.py
```

After Milestone 2:

```bash
python scripts/pipeline.py
npx serve docs
```

For source-only dashboard development:

```bash
npx serve .
# then open template.html
```

## Data sources

Primary: Yahoo Finance via `yfinance`. Cached to `data/raw/*.parquet` (gitignored). Two-source verification of inception dates uses Wikipedia and the strategy memo.

## Replication target (from the memo)

Teo cites ~535% cumulative return, ~10% CAGR, ~22% maximum drawdown over 2006 onwards. Tolerance for replication: ±200 bps CAGR, ±500 bps maximum drawdown. Outside that band triggers a diagnosis pass before claiming replication.

## Last updated

2026-04-28
