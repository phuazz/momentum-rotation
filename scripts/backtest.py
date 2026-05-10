"""Momentum Rotation backtest engine — Modes A and B.

Universe: GLD, SPY, TLT, DBC. Cash is synthesised from the ^IRX 13-week T-bill yield.

Modes
- A (Vanilla): top-2 eligible ETFs at 50% each. One survivor → 50% + 50% cash.
  Zero survivors → 100% cash.
- B (Graduated): top-2 picks identical to A, but the allocation level is sized
  by a composite score. Composite = mean of three sub-scores (each 0–100):
    breadth                = count_above_200dma / N × 100
    momentum_strength      = avg ROC of qualifying ETFs, clipped [0, 40%], rescaled
    leader_trend_strength  = avg distance of top-2 above 200d SMA, clipped [0, 25%], rescaled
  Allocation map: [0,25)→25%, [25,50)→50%, [50,75)→75%, [75,100]→100%.
  "Qualifying" = above 200d SMA. Zero eligible → 100% cash regardless of composite.

Conventions (shared)
- Signal date: month-end calendar timestamp.
- Execution: at the close of the first trading day strictly after the signal date.
- ROC window: trailing 12 calendar months on the monthly close series.
- 200-day SMA: 200-trading-day rolling mean on the daily series, sampled at
  month-end.
- Slippage: round-trip bps applied to one-way turnover.
- Cash: synthesised from ^IRX, daily compound rate (1 + IRX/100)^(1/252) - 1.

Run:
    python scripts/backtest.py
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

# === Configuration ===
RISK_TICKERS: list[str] = ["GLD", "SPY", "TLT", "DBC"]
RF_TICKER: str = "^IRX"
BENCH_TICKERS: list[str] = ["SPY", "AGG"]
ROC_MONTHS: int = 12
SMA_DAYS: int = 200
TOP_N: int = 2
SLIPPAGE_BPS: list[int] = [0, 5, 10]

# Mode B winsorisation bands (locked in CLAUDE.md)
MOMO_CLIP_HI: float = 0.40
LEADER_CLIP_HI: float = 0.25

# Mode C — robustness sweep across alternative four-ETF universes.
# Each universe runs the Mode A strategy (top-2 by ROC, 200d SMA filter,
# 50/50 sizing). Bond, equity, and commodity slots are rotated separately so
# that any sensitivity is attributable to one substitution at a time.
UNIVERSES_C: list[dict] = [
    {"id": "baseline", "label": "Baseline (TLT / DBC)",
     "tickers": ["GLD", "SPY", "TLT", "DBC"]},
    {"id": "short_bond", "label": "Short-duration bond (IEF)",
     "tickers": ["GLD", "SPY", "IEF", "DBC"]},
    {"id": "agg_bond", "label": "Aggregate bond (AGG)",
     "tickers": ["GLD", "SPY", "AGG", "DBC"]},
    {"id": "broad_commodity", "label": "K-1-free commodity (PDBC)",
     "tickers": ["GLD", "SPY", "TLT", "PDBC"]},
    {"id": "equity_qqq", "label": "Tech-tilt equity (QQQ)",
     "tickers": ["GLD", "QQQ", "TLT", "DBC"]},
]

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
DATA_DIR = ROOT / "data"


# === Data loading ===
def fetch_prices(ticker: str, force_refresh: bool = False) -> pd.DataFrame:
    """Fetch full daily OHLCV+Adj Close from Yahoo. Cache to data/raw/<ticker>.parquet.
    Refresh tail if cache is more than five days stale.
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = ticker.replace("^", "_")
    cache = RAW_DIR / f"{safe_name}.parquet"

    if cache.exists() and not force_refresh:
        df = pd.read_parquet(cache)
        last_cached = df.index[-1]
        today = pd.Timestamp(datetime.now(timezone.utc).date())
        if (today - last_cached).days > 5:
            try:
                fresh = yf.Ticker(ticker).history(period="3mo", auto_adjust=False)
                if not fresh.empty:
                    fresh.index = fresh.index.tz_localize(None)
                    df = pd.concat([df[df.index < fresh.index[0]], fresh])
                    df = df[~df.index.duplicated(keep="last")].sort_index()
                    df.to_parquet(cache)
            except Exception as exc:
                print(f"[warn] tail refresh failed for {ticker}: {exc}")
        return df

    df = yf.Ticker(ticker).history(period="max", auto_adjust=False)
    if df.empty:
        raise RuntimeError(f"No data returned for {ticker}")
    df.index = df.index.tz_localize(None)
    df.to_parquet(cache)
    return df


def synth_cash_daily_returns(rf_pct: pd.Series) -> pd.Series:
    """^IRX is annualised percent. Convert to daily compound rate."""
    return (1.0 + rf_pct.ffill() / 100.0) ** (1.0 / 252.0) - 1.0


def daily_panel(data: dict[str, pd.DataFrame], tickers: list[str]) -> pd.DataFrame:
    cols = {t: data[t]["Adj Close"] for t in tickers if t in data}
    return pd.DataFrame(cols).sort_index()


# === Signal compute (shared) ===
def signal_panel(daily_risk: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    monthly = daily_risk.resample("ME").last()
    roc = monthly.pct_change(ROC_MONTHS)
    sma_daily = daily_risk.rolling(SMA_DAYS, min_periods=SMA_DAYS).mean()
    sma_me = sma_daily.reindex(monthly.index, method="pad")
    above_sma = monthly > sma_me
    return monthly, roc, sma_me, above_sma


# === Mode B sub-scores ===
def compute_mode_b_subscores(
    eligible: list[tuple[str, float]],
    top: list[tuple[str, float]],
    monthly_row: pd.Series,
    sma_row: pd.Series,
    n_universe: int,
) -> tuple[float, float, float, float]:
    """Return (breadth, momentum_strength, leader_trend_strength, composite).
    Each sub-score is on a 0–100 scale.
    """
    breadth = len(eligible) / n_universe * 100.0

    if eligible:
        avg_roc = sum(r for _, r in eligible) / len(eligible)
        momentum_strength = max(0.0, min(MOMO_CLIP_HI, avg_roc)) / MOMO_CLIP_HI * 100.0
    else:
        momentum_strength = 0.0

    distances: list[float] = []
    for t, _ in top:
        sma_v = sma_row[t]
        px = monthly_row[t]
        if pd.notna(sma_v) and pd.notna(px) and sma_v > 0:
            distances.append(float(px / sma_v - 1.0))
    if distances:
        avg_dist = sum(distances) / len(distances)
        leader_trend_strength = max(0.0, min(LEADER_CLIP_HI, avg_dist)) / LEADER_CLIP_HI * 100.0
    else:
        leader_trend_strength = 0.0

    composite = (breadth + momentum_strength + leader_trend_strength) / 3.0
    return breadth, momentum_strength, leader_trend_strength, composite


def map_composite_to_allocation(composite: float) -> float:
    """[0,25)→0.25, [25,50)→0.50, [50,75)→0.75, [75,100]→1.00.
    Inclusive lower, exclusive upper. 100 snaps into the top band.
    """
    if composite < 25.0:
        return 0.25
    if composite < 50.0:
        return 0.50
    if composite < 75.0:
        return 0.75
    return 1.00


# === Signal records ===
@dataclass
class SignalRecord:
    """Mode A record. Mode B uses ModeBRecord."""
    date: pd.Timestamp
    roc: dict[str, float]
    above_sma: dict[str, bool]
    eligible: list[tuple[str, float]]
    top: list[tuple[str, float]]


@dataclass
class ModeBRecord:
    date: pd.Timestamp
    roc: dict[str, float]
    above_sma: dict[str, bool]
    eligible: list[tuple[str, float]]
    top: list[tuple[str, float]]
    breadth: float
    momentum_strength: float
    leader_trend_strength: float
    composite_score: float
    allocation_target: float
    allocation_realised: float


def construct_mode_a_weights(
    monthly: pd.DataFrame, roc: pd.DataFrame, above_sma: pd.DataFrame
) -> tuple[pd.DataFrame, list[SignalRecord]]:
    cols = list(monthly.columns) + ["CASH"]
    weights = pd.DataFrame(0.0, index=monthly.index, columns=cols)
    log: list[SignalRecord] = []

    for sd in monthly.index:
        roc_row = roc.loc[sd]
        sma_row = above_sma.loc[sd]
        # Require all four ETFs to have 12 months of history before signalling.
        if roc_row.isna().any():
            continue

        eligible: list[tuple[str, float]] = []
        for t in monthly.columns:
            r = roc_row[t]
            if pd.notna(r) and bool(sma_row[t]):
                eligible.append((t, float(r)))
        eligible.sort(key=lambda x: x[1], reverse=True)
        top = eligible[:TOP_N]

        if len(top) == 2:
            weights.loc[sd, top[0][0]] = 0.5
            weights.loc[sd, top[1][0]] = 0.5
        elif len(top) == 1:
            weights.loc[sd, top[0][0]] = 0.5
            weights.loc[sd, "CASH"] = 0.5
        else:
            weights.loc[sd, "CASH"] = 1.0

        log.append(
            SignalRecord(
                date=sd,
                roc={t: (float(roc_row[t]) if pd.notna(roc_row[t]) else float("nan")) for t in monthly.columns},
                above_sma={t: bool(sma_row[t]) for t in monthly.columns},
                eligible=eligible,
                top=top,
            )
        )

    weights = weights[weights.sum(axis=1) > 0]
    return weights, log


def construct_mode_b_weights(
    monthly: pd.DataFrame,
    roc: pd.DataFrame,
    sma_me: pd.DataFrame,
    above_sma: pd.DataFrame,
) -> tuple[pd.DataFrame, list[ModeBRecord]]:
    cols = list(monthly.columns) + ["CASH"]
    weights = pd.DataFrame(0.0, index=monthly.index, columns=cols)
    log: list[ModeBRecord] = []
    n_universe = len(monthly.columns)

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
        top = eligible[:TOP_N]

        breadth, momo, leader, composite = compute_mode_b_subscores(
            eligible, top, price_row, sma_lvl, n_universe
        )
        allocation_target = map_composite_to_allocation(composite)

        # Edge case: zero eligible overrides allocation map → 100% cash.
        if not eligible:
            weights.loc[sd, "CASH"] = 1.0
            allocation_realised = 0.0
        elif len(top) == 2:
            slot = allocation_target / 2.0
            weights.loc[sd, top[0][0]] = slot
            weights.loc[sd, top[1][0]] = slot
            weights.loc[sd, "CASH"] = 1.0 - allocation_target
            allocation_realised = allocation_target
        else:  # len(top) == 1
            slot = allocation_target / 2.0
            weights.loc[sd, top[0][0]] = slot
            weights.loc[sd, "CASH"] = 1.0 - slot
            allocation_realised = slot

        log.append(
            ModeBRecord(
                date=sd,
                roc={t: (float(roc_row[t]) if pd.notna(roc_row[t]) else float("nan")) for t in monthly.columns},
                above_sma={t: bool(sma_row_bool[t]) for t in monthly.columns},
                eligible=eligible,
                top=top,
                breadth=breadth,
                momentum_strength=momo,
                leader_trend_strength=leader,
                composite_score=composite,
                allocation_target=allocation_target,
                allocation_realised=allocation_realised,
            )
        )

    weights = weights[weights.sum(axis=1) > 0]
    return weights, log


def compute_live_snapshot(daily_risk: pd.DataFrame) -> dict:
    """Live ranking using the latest daily close — what the signal would say if
    computed right now, mid-month. Uses calendar-12-month ROC for consistency
    with the monthly signal logic.
    """
    if daily_risk.empty or len(daily_risk) < SMA_DAYS:
        return {}
    latest_date = daily_risk.index[-1]
    latest = daily_risk.iloc[-1]

    target = latest_date - pd.DateOffset(years=1)
    pos = daily_risk.index.searchsorted(target, side="right") - 1
    if pos < 0:
        return {}
    yr_ago = daily_risk.iloc[pos]
    roc = (latest / yr_ago - 1).astype(float)

    sma_200 = daily_risk.rolling(SMA_DAYS, min_periods=SMA_DAYS).mean().iloc[-1]
    distance = (latest / sma_200 - 1).astype(float)
    above = latest > sma_200

    eligible = [
        (t, float(roc[t]))
        for t in daily_risk.columns
        if pd.notna(roc[t]) and pd.notna(above[t]) and bool(above[t])
    ]
    eligible.sort(key=lambda x: x[1], reverse=True)
    rank_map = {t: i + 1 for i, (t, _) in enumerate(eligible)}

    rankings = []
    for t in daily_risk.columns:
        rankings.append(
            {
                "ticker": t,
                "price": (None if pd.isna(latest[t]) else round(float(latest[t]), 4)),
                "roc_12m": (None if pd.isna(roc[t]) else round(float(roc[t]), 6)),
                "sma_200": (None if pd.isna(sma_200[t]) else round(float(sma_200[t]), 4)),
                "distance_pct": (None if pd.isna(distance[t]) else round(float(distance[t]), 6)),
                "above_200dma": (None if pd.isna(above[t]) else bool(above[t])),
                "eligible": t in rank_map,
                "rank": rank_map.get(t, 0),
            }
        )
    return {
        "as_of_date": latest_date.strftime("%Y-%m-%d"),
        "rankings": rankings,
    }


def attach_mode_b_to_snapshot(snapshot: dict, n_universe: int) -> dict:
    """Augment a base live snapshot with Mode B sub-scores and target allocation.
    Returns a new dict; does not mutate the input.
    """
    if not snapshot or not snapshot.get("rankings"):
        return snapshot
    rankings = snapshot["rankings"]
    eligible = [
        (r["ticker"], float(r["roc_12m"]))
        for r in rankings
        if r.get("eligible") and r.get("roc_12m") is not None
    ]
    eligible.sort(key=lambda x: x[1], reverse=True)
    top = eligible[:TOP_N]

    breadth = len(eligible) / n_universe * 100.0

    if eligible:
        avg_roc = sum(r for _, r in eligible) / len(eligible)
        momentum_strength = max(0.0, min(MOMO_CLIP_HI, avg_roc)) / MOMO_CLIP_HI * 100.0
    else:
        momentum_strength = 0.0

    dist_map = {r["ticker"]: r.get("distance_pct") for r in rankings}
    dists = [dist_map[t] for t, _ in top if dist_map.get(t) is not None]
    if dists:
        avg_dist = sum(dists) / len(dists)
        leader_trend_strength = max(0.0, min(LEADER_CLIP_HI, avg_dist)) / LEADER_CLIP_HI * 100.0
    else:
        leader_trend_strength = 0.0

    composite = (breadth + momentum_strength + leader_trend_strength) / 3.0
    allocation_target = map_composite_to_allocation(composite)

    out = dict(snapshot)
    out["breadth"] = round(float(breadth), 4)
    out["momentum_strength"] = round(float(momentum_strength), 4)
    out["leader_trend_strength"] = round(float(leader_trend_strength), 4)
    out["composite_score"] = round(float(composite), 4)
    out["allocation_target"] = round(float(allocation_target), 4)
    return out


def filter_executable_signals(
    weights: pd.DataFrame, daily_index: pd.DatetimeIndex
) -> pd.DataFrame:
    """Drop signal dates whose execution day (first trading day strictly after the
    signal date) is not present in the daily data.
    """
    keep_mask = pd.Series(
        [(daily_index > sd).any() for sd in weights.index], index=weights.index
    )
    return weights[keep_mask]


# === Simulation ===
def simulate(
    weights_df: pd.DataFrame,
    panel: pd.DataFrame,
    daily_returns: pd.DataFrame,
    slippage_bps: float = 0.0,
) -> tuple[pd.Series, float]:
    cols = list(panel.columns)
    w_aligned = weights_df.reindex(columns=cols).fillna(0.0)
    daily_index = panel.index

    schedule: list[tuple[pd.Timestamp, np.ndarray]] = []
    for sd, row in w_aligned.iterrows():
        future = daily_index[daily_index > sd]
        if len(future) == 0:
            break
        schedule.append((future[0], row.values.astype(float)))

    if not schedule:
        return pd.Series(dtype=float), 0.0

    rets_arr = daily_returns.reindex(columns=cols).fillna(0.0).values
    start_idx = daily_index.get_loc(schedule[0][0])
    n_days = len(daily_index) - start_idx

    holdings = schedule[0][1].copy()
    pv = 1.0
    sched_idx = 1
    next_exec_idx = daily_index.get_loc(schedule[1][0]) if len(schedule) > 1 else None
    next_target = schedule[1][1] if len(schedule) > 1 else None

    pv_arr = np.empty(n_days, dtype=float)
    pv_arr[0] = pv
    cum_cost = 0.0

    for i in range(1, n_days):
        di = start_idx + i
        rets = rets_arr[di]
        port_ret = float(holdings @ rets)
        pv *= 1.0 + port_ret
        if 1.0 + port_ret > 0:
            holdings = holdings * (1.0 + rets) / (1.0 + port_ret)

        if next_exec_idx is not None and di == next_exec_idx:
            turnover_oneway = float(np.abs(next_target - holdings).sum() / 2.0)
            cost = turnover_oneway * slippage_bps / 1e4
            pv *= 1.0 - cost
            cum_cost += cost
            holdings = next_target.copy()
            sched_idx += 1
            if sched_idx < len(schedule):
                next_exec_idx = daily_index.get_loc(schedule[sched_idx][0])
                next_target = schedule[sched_idx][1]
            else:
                next_exec_idx = None
                next_target = None
        pv_arr[i] = pv

    return pd.Series(pv_arr, index=daily_index[start_idx:]), cum_cost


# === Performance ===
def perf_stats(equity: pd.Series, rf_daily: pd.Series | None = None) -> dict:
    rets = equity.pct_change().dropna()
    n_years = (equity.index[-1] - equity.index[0]).days / 365.25
    cumret = float(equity.iloc[-1] / equity.iloc[0] - 1)
    cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / n_years) - 1) if n_years > 0 else 0.0
    vol = float(rets.std(ddof=1) * np.sqrt(252))

    if rf_daily is not None:
        rf_aligned = rf_daily.reindex(rets.index).ffill().fillna(0.0)
        excess = rets - rf_aligned
    else:
        excess = rets

    sharpe = float(excess.mean() / excess.std(ddof=1) * np.sqrt(252)) if excess.std(ddof=1) > 0 else 0.0
    downside = excess[excess < 0]
    sortino = (
        float(excess.mean() / downside.std(ddof=1) * np.sqrt(252))
        if len(downside) > 1 and downside.std(ddof=1) > 0
        else 0.0
    )

    drawdown = equity / equity.cummax() - 1
    max_dd = float(drawdown.min())
    calmar = float(cagr / abs(max_dd)) if max_dd != 0 else 0.0

    monthly = equity.resample("ME").last().pct_change().dropna()
    hit_rate = float((monthly > 0).mean()) if len(monthly) else 0.0
    best_month = float(monthly.max()) if len(monthly) else 0.0
    worst_month = float(monthly.min()) if len(monthly) else 0.0

    yearly = equity.resample("YE").last().pct_change().dropna()
    yearly_returns = {str(d.year): float(r) for d, r in yearly.items()}

    return {
        "cagr": cagr,
        "vol": vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_dd,
        "calmar": calmar,
        "hit_rate": hit_rate,
        "best_month": best_month,
        "worst_month": worst_month,
        "cumulative_return": cumret,
        "years": float(n_years),
        "yearly_returns": yearly_returns,
    }


# === Orchestrator ===
def prepare_data(force_refresh: bool = False, risk_tickers: list[str] | None = None) -> dict:
    risk_tickers = risk_tickers if risk_tickers is not None else RISK_TICKERS
    needed = list(dict.fromkeys(risk_tickers + BENCH_TICKERS + [RF_TICKER]))
    data = {t: fetch_prices(t, force_refresh=force_refresh) for t in needed}

    daily_risk = daily_panel(data, risk_tickers)

    rf_pct = data[RF_TICKER]["Adj Close"]
    cash_daily_ret_full = synth_cash_daily_returns(rf_pct)
    cash_daily_ret = cash_daily_ret_full.reindex(daily_risk.index).ffill().fillna(0.0)
    cash_price = (1.0 + cash_daily_ret).cumprod()

    sim_panel = daily_risk.copy()
    sim_panel["CASH"] = cash_price
    sim_panel = sim_panel.dropna(how="all")
    sim_returns = sim_panel.pct_change().fillna(0.0)

    monthly, roc, sma_me, above_sma = signal_panel(daily_risk)

    return {
        "data": data,
        "daily_risk": daily_risk,
        "cash_daily_ret": cash_daily_ret,
        "cash_price": cash_price,
        "sim_panel": sim_panel,
        "sim_returns": sim_returns,
        "monthly": monthly,
        "roc": roc,
        "sma_me": sma_me,
        "above_sma": above_sma,
        "risk_tickers": list(risk_tickers),
    }


def run_strategy(prep: dict, mode: str) -> tuple[pd.DataFrame, list, dict]:
    monthly = prep["monthly"]
    roc = prep["roc"]
    sma_me = prep["sma_me"]
    above_sma = prep["above_sma"]
    daily_risk = prep["daily_risk"]
    sim_panel = prep["sim_panel"]
    sim_returns = prep["sim_returns"]
    cash_daily_ret = prep["cash_daily_ret"]

    if mode == "A":
        weights, sig_log = construct_mode_a_weights(monthly, roc, above_sma)
    elif mode == "B":
        weights, sig_log = construct_mode_b_weights(monthly, roc, sma_me, above_sma)
    else:
        raise ValueError(f"Unknown mode: {mode}")

    pre_filter_count = len(weights)
    weights = filter_executable_signals(weights, daily_risk.index)
    keep_dates = set(weights.index)
    sig_log = [r for r in sig_log if r.date in keep_dates]
    if len(weights) < pre_filter_count:
        dropped = pre_filter_count - len(weights)
        print(f"[mode_{mode.lower()}] dropped {dropped} in-progress signal(s)")

    print(
        f"[mode_{mode.lower()}] {len(weights)} executable signals from "
        f"{weights.index[0].date()} to {weights.index[-1].date()}"
    )

    results: dict = {}
    for sl in SLIPPAGE_BPS:
        eq, cost = simulate(weights, sim_panel, sim_returns, slippage_bps=sl)
        results[f"mode_{mode.lower()}_{sl}bps"] = {
            "equity": eq,
            "stats": perf_stats(eq, rf_daily=cash_daily_ret),
            "cumulative_cost": cost,
        }

    return weights, sig_log, results


def run_benchmarks(prep: dict, ref_start: pd.Timestamp) -> dict:
    data = prep["data"]
    cash_price = prep["cash_price"]
    cash_daily_ret = prep["cash_daily_ret"]

    spy = data["SPY"]["Adj Close"].dropna()
    spy = spy[spy.index >= ref_start]
    spy_eq = spy / spy.iloc[0]

    bench_panel = daily_panel(data, ["SPY", "AGG"]).dropna(how="all")
    bench_panel["CASH"] = cash_price.reindex(bench_panel.index).ffill()
    bench_returns = bench_panel.pct_change().fillna(0.0)
    bench_monthly_idx = bench_panel[["SPY", "AGG"]].resample("ME").last().index
    bench_w = pd.DataFrame(0.0, index=bench_monthly_idx, columns=["SPY", "AGG", "CASH"])
    bench_w["SPY"] = 0.6
    bench_w["AGG"] = 0.4
    bench_w = bench_w[bench_w.index >= ref_start]
    bench_eq, _ = simulate(bench_w, bench_panel, bench_returns, slippage_bps=0)

    return {
        "spy_total_return": {
            "equity": spy_eq,
            "stats": perf_stats(spy_eq, rf_daily=cash_daily_ret),
        },
        "spy_60_agg_40": {
            "equity": bench_eq,
            "stats": perf_stats(bench_eq, rf_daily=cash_daily_ret),
        },
    }


def run_modes(force_refresh: bool = False) -> dict:
    prep = prepare_data(force_refresh=force_refresh)

    a_weights, a_log, a_results = run_strategy(prep, "A")
    b_weights, b_log, b_results = run_strategy(prep, "B")

    # Anchor benchmarks at Mode A's first executable date so the comparison is
    # apples-to-apples with the original report.
    first_exec = a_results["mode_a_0bps"]["equity"].index[0]
    benchmarks = run_benchmarks(prep, first_exec)

    all_results = {**a_results, **b_results, **benchmarks}

    return {
        "prep": prep,
        "modes": {
            "A": {"weights": a_weights, "sig_log": a_log, "results": a_results},
            "B": {"weights": b_weights, "sig_log": b_log, "results": b_results},
        },
        "benchmarks": benchmarks,
        "all_results": all_results,
    }


# === JSON outputs ===
def _emit_history_entry(rec, weights_row: pd.Series, mode: str, tickers: list[str] | None = None) -> dict:
    tickers = tickers if tickers is not None else RISK_TICKERS
    entry: dict = {
        "weights": {t: float(weights_row.get(t, 0.0)) for t in tickers + ["CASH"]},
    }
    if rec is None:
        return entry
    entry["rocs"] = {
        t: (None if not np.isfinite(rec.roc[t]) else round(rec.roc[t], 6))
        for t in tickers
    }
    entry["above_sma"] = {t: bool(rec.above_sma[t]) for t in tickers}
    if mode == "B":
        entry["composite_score"] = round(float(rec.composite_score), 4)
        entry["allocation_target"] = round(float(rec.allocation_target), 4)
        entry["allocation_realised"] = round(float(rec.allocation_realised), 4)
        entry["breadth"] = round(float(rec.breadth), 4)
        entry["momentum_strength"] = round(float(rec.momentum_strength), 4)
        entry["leader_trend_strength"] = round(float(rec.leader_trend_strength), 4)
    return entry


def _emit_currently_held(
    weights: pd.DataFrame,
    sig_log: list,
    daily_risk: pd.DataFrame,
    mode: str,
    tickers: list[str] | None = None,
) -> dict:
    tickers = tickers if tickers is not None else RISK_TICKERS
    log_by_date = {r.date: r for r in sig_log}
    last_sd = weights.index[-1]
    last_w = weights.loc[last_sd]
    last_rec = log_by_date.get(last_sd)
    eligible_set = {e[0] for e in (last_rec.eligible if last_rec else [])}
    rank_map_held = {t: i + 1 for i, (t, _) in enumerate(last_rec.eligible if last_rec else [])}

    exec_after = daily_risk.index[daily_risk.index > last_sd]
    exec_date = exec_after[0].strftime("%Y-%m-%d") if len(exec_after) > 0 else None

    held_list = []
    for t in tickers:
        roc_v = last_rec.roc.get(t) if last_rec else None
        above = last_rec.above_sma.get(t) if last_rec else None
        held_list.append(
            {
                "ticker": t,
                "weight": float(last_w.get(t, 0.0)),
                "roc_12m": (None if roc_v is None or not np.isfinite(roc_v) else round(roc_v, 6)),
                "above_200dma": bool(above) if above is not None else None,
                "eligible": t in eligible_set,
                "rank": rank_map_held.get(t, 0),
            }
        )
    cash_weight = float(last_w.get("CASH", 0.0))
    if cash_weight > 0:
        held_list.append(
            {
                "ticker": "CASH",
                "weight": cash_weight,
                "roc_12m": None,
                "above_200dma": None,
                "eligible": False,
                "rank": 0,
            }
        )

    out: dict = {
        "signal_date": last_sd.strftime("%Y-%m-%d"),
        "exec_date": exec_date,
        "holdings": held_list,
    }
    if mode == "B" and last_rec is not None:
        out["composite_score"] = round(float(last_rec.composite_score), 4)
        out["allocation_target"] = round(float(last_rec.allocation_target), 4)
        out["allocation_realised"] = round(float(last_rec.allocation_realised), 4)
        out["breadth"] = round(float(last_rec.breadth), 4)
        out["momentum_strength"] = round(float(last_rec.momentum_strength), 4)
        out["leader_trend_strength"] = round(float(last_rec.leader_trend_strength), 4)
    return out


def emit_outputs(payload: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    all_results = payload["all_results"]
    modes = payload["modes"]
    daily_risk = payload["prep"]["daily_risk"]
    n_universe = len(RISK_TICKERS)

    # === equity_curve.json ===
    eq_payload: dict = {
        "schema_version": 2,
        "as_of_utc": now_utc,
        "modes": ["A", "B"],
        "data_source": "Yahoo Finance via yfinance",
        "cash_proxy": "synthesised from ^IRX yield",
        "series": {},
    }
    for k, v in all_results.items():
        if "equity" in v:
            eq = v["equity"]
            eq_payload["series"][k] = [
                [d.strftime("%Y-%m-%d"), round(float(p), 6)] for d, p in eq.items()
            ]
    if eq_payload["series"]:
        first_key = next(iter(eq_payload["series"]))
        eq_payload["start_date"] = eq_payload["series"][first_key][0][0]
        eq_payload["end_date"] = eq_payload["series"][first_key][-1][0]
    (DATA_DIR / "equity_curve.json").write_text(json.dumps(eq_payload))

    # === holdings_timeline.json ===
    hold_payload: dict = {
        "schema_version": 2,
        "as_of_utc": now_utc,
        "tickers": RISK_TICKERS + ["CASH"],
        "modes": {},
    }
    for mode_key, mode_data in modes.items():
        weights = mode_data["weights"]
        sig_log = mode_data["sig_log"]
        log_by_date = {r.date: r for r in sig_log}
        history = []
        for sd in weights.index:
            rec = log_by_date.get(sd)
            entry = {"signal_date": sd.strftime("%Y-%m-%d")}
            entry.update(_emit_history_entry(rec, weights.loc[sd], mode_key))
            history.append(entry)
        hold_payload["modes"][mode_key] = {"history": history}
    (DATA_DIR / "holdings_timeline.json").write_text(json.dumps(hold_payload))

    # === performance_stats.json ===
    stats_payload = {
        "schema_version": 2,
        "as_of_utc": now_utc,
        "modes": {k: v["stats"] for k, v in all_results.items() if "stats" in v},
    }
    (DATA_DIR / "performance_stats.json").write_text(json.dumps(stats_payload, indent=2))

    # === current_positioning.json ===
    base_snapshot = compute_live_snapshot(daily_risk)
    pos_payload: dict = {
        "schema_version": 3,
        "as_of_utc": now_utc,
        "modes": {},
    }
    for mode_key, mode_data in modes.items():
        currently_held = _emit_currently_held(
            mode_data["weights"], mode_data["sig_log"], daily_risk, mode_key
        )
        if mode_key == "B":
            live_snapshot = attach_mode_b_to_snapshot(base_snapshot, n_universe)
        else:
            live_snapshot = base_snapshot
        pos_payload["modes"][mode_key] = {
            "currently_held": currently_held,
            "live_snapshot": live_snapshot,
        }
    (DATA_DIR / "current_positioning.json").write_text(json.dumps(pos_payload, indent=2))
    print(f"[emit] wrote 4 JSON files to {DATA_DIR}")


def run_mode_c(force_refresh: bool = False) -> dict:
    """Run Mode A strategy on each Mode C universe.
    Returns a dict of universe_id -> per-universe payload.
    """
    out: dict = {}
    for u in UNIVERSES_C:
        uid = u["id"]
        tickers = u["tickers"]
        print(f"\n[mode_c/{uid}] universe: {tickers}")
        prep = prepare_data(force_refresh=force_refresh, risk_tickers=tickers)
        weights, sig_log, results = run_strategy(prep, "A")
        eq = results["mode_a_0bps"]["equity"]
        live = compute_live_snapshot(prep["daily_risk"])
        currently_held = _emit_currently_held(
            weights, sig_log, prep["daily_risk"], "A", tickers=tickers
        )
        log_by_date = {r.date: r for r in sig_log}
        history = []
        for sd in weights.index:
            rec = log_by_date.get(sd)
            entry = {"signal_date": sd.strftime("%Y-%m-%d")}
            entry.update(_emit_history_entry(rec, weights.loc[sd], "A", tickers=tickers))
            history.append(entry)
        out[uid] = {
            "label": u["label"],
            "tickers": list(tickers),
            "start_date": eq.index[0].strftime("%Y-%m-%d"),
            "end_date": eq.index[-1].strftime("%Y-%m-%d"),
            "stats": results["mode_a_0bps"]["stats"],
            "equity": [
                [d.strftime("%Y-%m-%d"), round(float(p), 6)] for d, p in eq.items()
            ],
            "history": history,
            "currently_held": currently_held,
            "live_snapshot": live,
        }
    return out


def emit_mode_c_outputs(universes_results: dict) -> None:
    payload = {
        "schema_version": 1,
        "as_of_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "universes": universes_results,
    }
    (DATA_DIR / "mode_c_universes.json").write_text(json.dumps(payload))
    print(f"[emit] wrote mode_c_universes.json ({len(universes_results)} universes)")


def print_mode_c_summary(universes_results: dict) -> None:
    print("\n=== Mode C — universe sensitivity ===")
    print(f"{'Universe':22s} {'Start':>10s} {'CAGR':>8s} {'Vol':>8s} {'Sharpe':>8s} {'MaxDD':>8s} {'2008':>8s} {'2022':>8s}")
    for uid, u in universes_results.items():
        s = u["stats"]
        y08 = s.get("yearly_returns", {}).get("2008")
        y22 = s.get("yearly_returns", {}).get("2022")
        y08_s = f"{y08:+.1%}" if y08 is not None else "—"
        y22_s = f"{y22:+.1%}" if y22 is not None else "—"
        print(
            f"{u['label'][:22]:22s} {u['start_date']:>10s} {s['cagr']:>7.2%} {s['vol']:>7.2%} "
            f"{s['sharpe']:>8.2f} {s['max_drawdown']:>7.2%} {y08_s:>8s} {y22_s:>8s}"
        )


def print_summary(payload: dict) -> None:
    all_results = payload["all_results"]
    modes = payload["modes"]
    print("\n=== Replication results ===")
    order = [
        "mode_a_0bps", "mode_a_5bps", "mode_a_10bps",
        "mode_b_0bps", "mode_b_5bps", "mode_b_10bps",
        "spy_total_return", "spy_60_agg_40",
    ]
    for k in order:
        if k not in all_results:
            continue
        s = all_results[k]["stats"]
        print(f"\n{k}")
        print(f"  Years:           {s['years']:.2f}")
        print(f"  Cumulative ret:  {s['cumulative_return']:+.2%}")
        print(f"  CAGR:            {s['cagr']:.2%}")
        print(f"  Vol (ann):       {s['vol']:.2%}")
        print(f"  Sharpe:          {s['sharpe']:.2f}")
        print(f"  Sortino:         {s['sortino']:.2f}")
        print(f"  Max DD:          {s['max_drawdown']:.2%}")
        print(f"  Calmar:          {s['calmar']:.2f}")
        print(f"  Hit rate:        {s['hit_rate']:.2%}")
        for y in ["2008", "2020", "2022"]:
            if y in s["yearly_returns"]:
                print(f"  {y}:             {s['yearly_returns'][y]:+.2%}")

    base = all_results["mode_a_0bps"]["stats"]
    print("\n=== Mode A vs Teo targets ===")
    print(f"  CAGR:        target 10.0%   observed {base['cagr']:.2%}   delta {(base['cagr']-0.10)*100:+.1f} pp")
    print(f"  Max DD:      target -22.0%  observed {base['max_drawdown']:.2%}   delta {(base['max_drawdown']-(-0.22))*100:+.1f} pp")
    print(f"  Cumulative:  target +535%   observed {base['cumulative_return']:.0%}")
    cagr_within = abs(base["cagr"] - 0.10) <= 0.02
    dd_within = abs(base["max_drawdown"] - (-0.22)) <= 0.05
    print(f"  CAGR within ±200 bps:    {'YES' if cagr_within else 'NO'}")
    print(f"  Max DD within ±500 bps:  {'YES' if dd_within else 'NO'}")

    # Mode A latest signal
    a_weights = modes["A"]["weights"]
    a_log = modes["A"]["sig_log"]
    last_sd_a = a_weights.index[-1]
    last_rec_a = next((r for r in a_log if r.date == last_sd_a), None)
    if last_rec_a is not None:
        print(f"\n=== Latest Mode A signal ({last_sd_a.date()}) ===")
        rocs = ", ".join(
            f"{t}={(v*100):+.1f}%" for t, v in last_rec_a.roc.items() if np.isfinite(v)
        )
        print(f"  ROCs:        {rocs}")
        print(f"  Above 200dma: " + ", ".join(f"{t}={'YES' if v else 'NO'}" for t, v in last_rec_a.above_sma.items()))
        if last_rec_a.eligible:
            print(f"  Eligible:     " + " > ".join(f"{t}({r*100:+.1f}%)" for t, r in last_rec_a.eligible))
        if last_rec_a.top:
            print(f"  Hold:         " + ", ".join(f"{t}@50%" for t, _ in last_rec_a.top))

    # Mode B latest signal
    b_weights = modes["B"]["weights"]
    b_log = modes["B"]["sig_log"]
    last_sd_b = b_weights.index[-1]
    last_rec_b = next((r for r in b_log if r.date == last_sd_b), None)
    if last_rec_b is not None:
        print(f"\n=== Latest Mode B signal ({last_sd_b.date()}) ===")
        print(f"  Breadth:               {last_rec_b.breadth:.1f}")
        print(f"  Momentum strength:     {last_rec_b.momentum_strength:.1f}")
        print(f"  Leader trend strength: {last_rec_b.leader_trend_strength:.1f}")
        print(f"  Composite score:       {last_rec_b.composite_score:.1f}")
        print(f"  Allocation target:     {last_rec_b.allocation_target:.0%}")
        print(f"  Allocation realised:   {last_rec_b.allocation_realised:.0%}")
        if last_rec_b.top:
            slot = last_rec_b.allocation_realised / max(1, len(last_rec_b.top))
            print(f"  Hold:                  " + ", ".join(f"{t}@{slot:.0%}" for t, _ in last_rec_b.top))


def main() -> None:
    payload = run_modes()
    emit_outputs(payload)
    print_summary(payload)
    universes = run_mode_c()
    emit_mode_c_outputs(universes)
    print_mode_c_summary(universes)


if __name__ == "__main__":
    main()
