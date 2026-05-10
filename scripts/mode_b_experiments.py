#!/usr/bin/env python3
"""Mode B experiment harness.

Runs the current Mode B alongside three variants on the same prepared data:
  V1  Continuous allocation map (linear from 25% at composite=0 to 100% at composite>=75)
  V2  2-month confirmation rule for de-risking (asymmetric: slow down, fast up)
  V3  Both V1 + V2

Outputs a comparison table across CAGR / vol / Sharpe / Sortino / max DD / Calmar /
key calendar years. Decision criteria (suggested):
  - Variant is "worth" if it improves CAGR by >=50 bps without increasing max DD by >300 bps
  - Tie-breaker: Sharpe must not drop more than 0.05

Run:
    python3 scripts/mode_b_experiments.py

Cache-only mode (skips yfinance refresh — useful in offline sandboxes):
    MODE_B_NO_REFRESH=1 python3 scripts/mode_b_experiments.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable

# Allow running from project root or scripts/.
ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import pandas as pd  # noqa: E402

import backtest as bt  # noqa: E402

# ---------- Cache-only patch (sandbox-friendly) ----------
if os.environ.get("MODE_B_NO_REFRESH") == "1":
    _original_fetch = bt.fetch_prices

    def _fetch_no_refresh(ticker: str, force_refresh: bool = False) -> pd.DataFrame:
        safe_name = ticker.replace("^", "_")
        cache = bt.RAW_DIR / f"{safe_name}.parquet"
        if cache.exists():
            return pd.read_parquet(cache)
        # No cache → fall back to original behaviour (fetch).
        return _original_fetch(ticker, force_refresh=False)

    bt.fetch_prices = _fetch_no_refresh
    print("[experiments] running in cache-only mode (no yfinance refresh)")


# ---------- Variant allocation maps ----------
def alloc_continuous(composite: float) -> float:
    """Linear from 25% at composite=0 to 100% at composite=75 and beyond.
    Same boundary at the top as the locked stepped map (composite >=75 -> 100%)
    so the upside in clear regimes is preserved; the gain comes from smooth
    interpolation through the 25-75 range instead of three flat bands.
    """
    if composite >= 75.0:
        return 1.0
    return 0.25 + 0.75 * (composite / 75.0)


# Original stepped map for reference (already in backtest.map_composite_to_allocation)
def alloc_stepped(composite: float) -> float:
    return bt.map_composite_to_allocation(composite)


# ---------- Mode B builder, parametrised ----------
def construct_mode_b_weights_v(
    monthly: pd.DataFrame,
    roc: pd.DataFrame,
    sma_me: pd.DataFrame,
    above_sma: pd.DataFrame,
    *,
    alloc_fn: Callable[[float], float] = alloc_stepped,
    confirm_de_risk: bool = False,
):
    """Variant of construct_mode_b_weights.

    confirm_de_risk: if True, an allocation drop only takes effect when the new
    (lower) target persists for 2 consecutive month-ends. Re-risk is immediate.
    """
    cols = list(monthly.columns) + ["CASH"]
    weights = pd.DataFrame(0.0, index=monthly.index, columns=cols)
    n_universe = len(monthly.columns)

    pending_lower: float | None = None  # candidate lower allocation awaiting confirmation
    last_committed_alloc: float = 1.0

    for sd in monthly.index:
        roc_row = roc.loc[sd]
        sma_row_bool = above_sma.loc[sd]
        sma_lvl = sma_me.loc[sd]
        price_row = monthly.loc[sd]

        if roc_row.isna().any():
            continue

        eligible: list[tuple[str, float]] = []
        for t in monthly.columns:
            r = roc_row[t]
            if pd.notna(r) and bool(sma_row_bool[t]):
                eligible.append((t, float(r)))
        eligible.sort(key=lambda x: x[1], reverse=True)
        top = eligible[: bt.TOP_N]

        _, _, _, composite = bt.compute_mode_b_subscores(
            eligible, top, price_row, sma_lvl, n_universe
        )
        raw_target = alloc_fn(composite)

        # Confirmation logic: if raw_target is lower than what we currently
        # hold, queue it; require two consecutive lower targets to commit.
        if confirm_de_risk:
            if raw_target < last_committed_alloc:
                if pending_lower is None:
                    # First observation of a lower target: queue, but hold previous.
                    pending_lower = raw_target
                    effective_target = last_committed_alloc
                else:
                    # Second consecutive lower target: commit (use min of the two).
                    effective_target = min(pending_lower, raw_target)
                    last_committed_alloc = effective_target
                    pending_lower = None
            else:
                # raw_target >= committed → re-risk immediately, clear any queue.
                effective_target = raw_target
                last_committed_alloc = raw_target
                pending_lower = None
        else:
            effective_target = raw_target
            last_committed_alloc = raw_target

        if not eligible:
            weights.loc[sd, "CASH"] = 1.0
            # Reset confirmation state when filter forces 100% cash.
            last_committed_alloc = 0.0
            pending_lower = None
        elif len(top) == 2:
            slot = effective_target / 2.0
            weights.loc[sd, top[0][0]] = slot
            weights.loc[sd, top[1][0]] = slot
            weights.loc[sd, "CASH"] = 1.0 - effective_target
        else:  # len(top) == 1
            slot = effective_target / 2.0
            weights.loc[sd, top[0][0]] = slot
            weights.loc[sd, "CASH"] = 1.0 - slot

    weights = weights[weights.sum(axis=1) > 0]
    return weights


# ---------- Run a variant end-to-end ----------
def run_variant(prep: dict, *, alloc_fn: Callable[[float], float], confirm_de_risk: bool):
    daily_risk = prep["daily_risk"]
    monthly, roc, sma_me, above_sma = bt.signal_panel(daily_risk)
    weights = construct_mode_b_weights_v(
        monthly, roc, sma_me, above_sma,
        alloc_fn=alloc_fn,
        confirm_de_risk=confirm_de_risk,
    )

    # Build the simulation panel exactly as backtest.run_strategy does.
    cash_price = prep["cash_price"]
    sim_panel = daily_risk.copy()
    sim_panel["CASH"] = cash_price.reindex(sim_panel.index).ffill()
    sim_panel = sim_panel.dropna(how="all")
    sim_returns = sim_panel.pct_change().fillna(0.0)

    eq, _ = bt.simulate(weights, sim_panel, sim_returns, slippage_bps=0)
    stats = bt.perf_stats(eq, rf_daily=prep["cash_daily_ret"])
    return eq, stats


# ---------- Main ----------
def main():
    prep = bt.prepare_data(force_refresh=False)

    variants = [
        ("Mode B — current (stepped)", alloc_stepped, False),
        ("V1: continuous alloc",       alloc_continuous, False),
        ("V2: stepped + confirm",      alloc_stepped, True),
        ("V3: continuous + confirm",   alloc_continuous, True),
    ]

    # Mode A baseline — for reference, we use whatever the backtest already produces.
    a_weights, _, a_results = bt.run_strategy(prep, "A")
    a_eq = a_results["mode_a_0bps"]["equity"]
    a_stats = a_results["mode_a_0bps"]["stats"]

    rows = [("Mode A — vanilla (ref)", a_stats, a_eq)]
    for label, alloc_fn, confirm in variants:
        eq, stats = run_variant(prep, alloc_fn=alloc_fn, confirm_de_risk=confirm)
        rows.append((label, stats, eq))

    # Headline table.
    headline_keys = [
        ("CAGR",   lambda s: f"{s['cagr']:+.2%}"),
        ("Vol",    lambda s: f"{s['vol']:.2%}"),
        ("Sharpe", lambda s: f"{s['sharpe']:.2f}"),
        ("Sortino",lambda s: f"{s['sortino']:.2f}"),
        ("MaxDD",  lambda s: f"{s['max_drawdown']:.2%}"),
        ("Calmar", lambda s: f"{s['calmar']:.2f}"),
        ("HitRate",lambda s: f"{s['hit_rate']:.2%}"),
        ("Cumret", lambda s: f"{s['cumulative_return']:+.0%}"),
    ]
    print("\n=== Headline metrics ===")
    print(f"{'Variant':30s}", *(f"{k:>9s}" for k, _ in headline_keys))
    for label, stats, _ in rows:
        print(f"{label:30s}", *(f"{fn(stats):>9s}" for _, fn in headline_keys))

    # Calendar-year table.
    years_of_interest = ["2008", "2011", "2015", "2018", "2020", "2022", "2024", "2025", "2026"]
    print("\n=== Calendar-year returns ===")
    print(f"{'Variant':30s}", *(f"{y:>8s}" for y in years_of_interest))
    for label, stats, _ in rows:
        yr = stats.get("yearly_returns", {})
        cells = []
        for y in years_of_interest:
            v = yr.get(y)
            cells.append("—" if v is None else f"{v:+.1%}")
        print(f"{label:30s}", *(f"{c:>8s}" for c in cells))

    # Decision summary vs current Mode B.
    print("\n=== Delta vs current Mode B (first variant after baseline) ===")
    base = rows[1][1]  # current Mode B
    for label, stats, _ in rows[2:]:
        d_cagr = (stats["cagr"] - base["cagr"]) * 1e4   # bps
        d_dd = (stats["max_drawdown"] - base["max_drawdown"]) * 1e4
        d_sh = stats["sharpe"] - base["sharpe"]
        d_so = stats["sortino"] - base["sortino"]
        verdict = "WORTH"
        if d_cagr < 50:
            verdict = "marginal CAGR lift"
        if d_dd < -300:
            verdict = "WORSE drawdown"
        if d_sh < -0.05:
            verdict = "Sharpe drop"
        print(f"{label:30s}  ΔCAGR={d_cagr:+.0f}bps  ΔMaxDD={d_dd:+.0f}bps  "
              f"ΔSharpe={d_sh:+.2f}  ΔSortino={d_so:+.2f}  → {verdict}")


if __name__ == "__main__":
    main()
