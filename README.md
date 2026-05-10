# Momentum Rotation Dashboard

Backtest dashboard for a four-asset monthly momentum rotation system, modelled on Rayner Teo's framework.

Public URL (planned): https://phuazz.github.io/momentum-rotation/

## Status

**Milestones 1–3 plus Mode C — complete.** Mode A replicates Teo's headline figures within tolerance (CAGR 11.73% vs target 10%, max drawdown -23.11% vs -22%). Mode B graduated overlay live: lower CAGR (7.69%) and lower vol (8.75%) with better behaviour through choppy regimes — 2022 -0.65% vs Mode A -2.93% vs SPY -18.18%. Mode C universe sensitivity live: Mode A strategy applied to five four-ETF universes (baseline plus four single-substitution variants), with a comparison table and overlay chart. See [REPLICATION_REPORT.md](REPLICATION_REPORT.md) for the detail. Dashboard: `python scripts/pipeline.py` then `npx serve docs`.

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
| A | Vanilla replication of Teo's spec. Base case. **Live.** |
| B | Graduated allocation overlay. Equal-weight 1/3 composite of breadth, momentum strength, leader trend strength → 25/50/75/100% risk-asset exposure. **Live.** |
| C | Universe sensitivity. Mode A strategy on five four-ETF universes (baseline plus IEF, AGG, PDBC, QQQ single-substitution variants). **Live.** |
| D | Combined view (planned). Mode B graduated overlay applied to the Mode C universes. |

## Run locally

```bash
pip install -r requirements.txt
python scripts/backtest.py
```

Dashboard build and serve:

```bash
python scripts/pipeline.py
npx serve docs
```

For source-only dashboard development (template.html with fetch fallback):

```bash
npx serve .
# then open template.html
```

## Data sources

Primary: Yahoo Finance via `yfinance`. Cached to `data/raw/*.parquet` (gitignored). Two-source verification of inception dates uses Wikipedia and the strategy memo.

## Replication target (from the memo)

Teo cites ~535% cumulative return, ~10% CAGR, ~22% maximum drawdown over 2006 onwards. Tolerance for replication: ±200 bps CAGR, ±500 bps maximum drawdown. Outside that band triggers a diagnosis pass before claiming replication.

## Last updated

2026-04-29
