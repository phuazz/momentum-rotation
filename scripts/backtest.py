"""Momentum Rotation backtest engine — Mode A.

Universe: GLD, SPY, TLT, DBC. Cash is synthesised from the ^IRX 13-week T-bill yield.

Conventions
- Signal date: month-end calendar timestamp; the value is the last trading day's
  adjusted close in that month.
- Execution: at the close of the first trading day strictly after the signal date.
- ROC window: trailing 12 calendar months on the monthly close series
  (close[t] / close[t-12] - 1).
- 200-day SMA: 200-trading-day rolling mean on the daily series, sampled at the
  last trading day of each month.
- Slippage: round-trip bps applied to one-way turnover. One-way turnover is
  sum(|new - old|) / 2; cost = one_way_turnover * round_trip_bps / 1e4.
- Cash: synthesised from ^IRX, daily compound rate (1 + IRX/100)^(1/252) - 1.

Run:
    python scripts/backtest.py
"""

from __future__ import annotations

import json
from dataclasses import dataclass
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


# === Signal compute ===
def signal_panel(daily_risk: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    monthly = daily_risk.resample("ME").last()
    roc = monthly.pct_change(ROC_MONTHS)
    sma_daily = daily_risk.rolling(SMA_DAYS, min_periods=SMA_DAYS).mean()
    sma_me = sma_daily.reindex(monthly.index, method="pad")
    above_sma = monthly > sma_me
    return monthly, roc, sma_me, above_sma


@dataclass
class SignalRecord:
    date: pd.Timestamp
    roc: dict[str, float]
    above_sma: dict[str, bool]
    eligible: list[tuple[str, float]]
    top: list[tuple[str, float]]


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
        # First eligible signal is end-Feb 2007, given DBC inception 2006-02-06.
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


def filter_executable_signals(
    weights: pd.DataFrame, daily_index: pd.DatetimeIndex
) -> pd.DataFrame:
    """Drop signal dates whose execution day (first trading day strictly after the
    signal date) is not present in the daily data.

    A signal at calendar month-end M is "executable" only when the data extends
    past M — i.e. the rebalance has actually happened. Mid-month runs of the
    engine produce a preliminary signal stamped at the upcoming month-end (using
    only the partial month's data); this filter prevents that signal from
    polluting the holdings timeline or the current-position card. The actual
    currently-held portfolio is the most recent executable signal.
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
def run_mode_a(force_refresh: bool = False) -> dict:
    needed = list(dict.fromkeys(RISK_TICKERS + BENCH_TICKERS + [RF_TICKER]))
    data = {t: fetch_prices(t, force_refresh=force_refresh) for t in needed}

    daily_risk = daily_panel(data, RISK_TICKERS)

    rf_pct = data[RF_TICKER]["Adj Close"]
    cash_daily_ret_full = synth_cash_daily_returns(rf_pct)
    cash_daily_ret = cash_daily_ret_full.reindex(daily_risk.index).ffill().fillna(0.0)
    cash_price = (1.0 + cash_daily_ret).cumprod()

    sim_panel = daily_risk.copy()
    sim_panel["CASH"] = cash_price
    sim_panel = sim_panel.dropna(how="all")
    sim_returns = sim_panel.pct_change().fillna(0.0)

    monthly, roc, sma_me, above_sma = signal_panel(daily_risk)
    weights, sig_log = construct_mode_a_weights(monthly, roc, above_sma)

    # Drop in-progress signals (e.g., a preliminary 2026-04-30 signal generated
    # mid-month from partial-April data when no execution day has happened yet).
    # The current portfolio reflects the most recent executable signal, which is
    # the prior month-end's.
    pre_filter_count = len(weights)
    weights = filter_executable_signals(weights, daily_risk.index)
    sig_log = [r for r in sig_log if r.date in set(weights.index)]
    if len(weights) < pre_filter_count:
        dropped = pre_filter_count - len(weights)
        print(f"[mode_a] dropped {dropped} in-progress signal(s)")

    print(
        f"[mode_a] {len(weights)} executable signals from {weights.index[0].date()} to {weights.index[-1].date()}"
    )

    results: dict = {}
    for sl in SLIPPAGE_BPS:
        eq, cost = simulate(weights, sim_panel, sim_returns, slippage_bps=sl)
        results[f"mode_a_{sl}bps"] = {
            "equity": eq,
            "stats": perf_stats(eq, rf_daily=cash_daily_ret),
            "cumulative_cost": cost,
        }

    first_exec = results["mode_a_0bps"]["equity"].index[0]

    # SPY total-return benchmark, normalised at first_exec
    spy = data["SPY"]["Adj Close"].dropna()
    spy = spy[spy.index >= first_exec]
    spy_eq = spy / spy.iloc[0]
    results["spy_total_return"] = {
        "equity": spy_eq,
        "stats": perf_stats(spy_eq, rf_daily=cash_daily_ret),
    }

    # 60/40 SPY/AGG, monthly rebalance
    bench_panel = daily_panel(data, ["SPY", "AGG"]).dropna(how="all")
    bench_panel["CASH"] = cash_price.reindex(bench_panel.index).ffill()
    bench_returns = bench_panel.pct_change().fillna(0.0)
    bench_monthly_idx = bench_panel[["SPY", "AGG"]].resample("ME").last().index
    bench_w = pd.DataFrame(0.0, index=bench_monthly_idx, columns=["SPY", "AGG", "CASH"])
    bench_w["SPY"] = 0.6
    bench_w["AGG"] = 0.4
    bench_w = bench_w[bench_w.index >= weights.index[0]]
    bench_eq, _ = simulate(bench_w, bench_panel, bench_returns, slippage_bps=0)
    results["spy_60_agg_40"] = {
        "equity": bench_eq,
        "stats": perf_stats(bench_eq, rf_daily=cash_daily_ret),
    }

    return {
        "weights": weights,
        "results": results,
        "sig_log": sig_log,
        "monthly": monthly,
        "roc": roc,
        "above_sma": above_sma,
        "data": data,
    }


# === JSON outputs ===
def emit_outputs(payload: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    weights = payload["weights"]
    results = payload["results"]
    sig_log = payload["sig_log"]
    now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # equity_curve.json
    eq_payload = {
        "schema_version": 1,
        "as_of_utc": now_utc,
        "mode": "A",
        "data_source": "Yahoo Finance via yfinance",
        "cash_proxy": "synthesised from ^IRX yield",
        "series": {},
    }
    for k, v in results.items():
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

    # holdings_timeline.json
    hold_payload = {
        "schema_version": 1,
        "as_of_utc": now_utc,
        "mode": "A",
        "tickers": RISK_TICKERS + ["CASH"],
        "history": [],
    }
    log_by_date = {r.date: r for r in sig_log}
    for sd in weights.index:
        rec = log_by_date.get(sd)
        wrow = weights.loc[sd]
        entry = {
            "signal_date": sd.strftime("%Y-%m-%d"),
            "weights": {t: float(wrow.get(t, 0.0)) for t in RISK_TICKERS + ["CASH"]},
            "rocs": (
                {t: (None if not np.isfinite(rec.roc[t]) else round(rec.roc[t], 6)) for t in RISK_TICKERS}
                if rec
                else {}
            ),
            "above_sma": ({t: bool(rec.above_sma[t]) for t in RISK_TICKERS} if rec else {}),
        }
        hold_payload["history"].append(entry)
    (DATA_DIR / "holdings_timeline.json").write_text(json.dumps(hold_payload))

    # performance_stats.json
    stats_payload = {
        "schema_version": 1,
        "as_of_utc": now_utc,
        "modes": {k: v["stats"] for k, v in results.items() if "stats" in v},
    }
    (DATA_DIR / "performance_stats.json").write_text(json.dumps(stats_payload, indent=2))

    # current_positioning.json
    last_sd = weights.index[-1]
    last_w = weights.loc[last_sd]
    last_rec = log_by_date.get(last_sd)
    eligible_set = {e[0] for e in (last_rec.eligible if last_rec else [])}
    rank_map = {t: i + 1 for i, (t, _) in enumerate(last_rec.eligible if last_rec else [])}
    holdings_list = []
    for t in RISK_TICKERS:
        roc_v = last_rec.roc.get(t) if last_rec else None
        above = last_rec.above_sma.get(t) if last_rec else None
        holdings_list.append(
            {
                "ticker": t,
                "weight": float(last_w.get(t, 0.0)),
                "roc_12m": (None if roc_v is None or not np.isfinite(roc_v) else round(roc_v, 6)),
                "above_200dma": bool(above) if above is not None else None,
                "eligible": t in eligible_set,
                "rank": rank_map.get(t, 0),
            }
        )
    pos_payload = {
        "schema_version": 1,
        "as_of_utc": now_utc,
        "last_signal_date": last_sd.strftime("%Y-%m-%d"),
        "mode": "A",
        "holdings": holdings_list,
    }
    (DATA_DIR / "current_positioning.json").write_text(json.dumps(pos_payload, indent=2))
    print(f"[emit] wrote 4 JSON files to {DATA_DIR}")


def print_summary(payload: dict) -> None:
    results = payload["results"]
    print("\n=== Mode A replication results ===")
    for k in ["mode_a_0bps", "mode_a_5bps", "mode_a_10bps", "spy_total_return", "spy_60_agg_40"]:
        s = results[k]["stats"]
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

    base = results["mode_a_0bps"]["stats"]
    print("\n=== vs Teo targets ===")
    print(f"  CAGR:        target 10.0%   observed {base['cagr']:.2%}   delta {(base['cagr']-0.10)*100:+.1f} pp")
    print(f"  Max DD:      target -22.0%  observed {base['max_drawdown']:.2%}   delta {(base['max_drawdown']-(-0.22))*100:+.1f} pp")
    print(f"  Cumulative:  target +535%   observed {base['cumulative_return']:.0%}")
    cagr_within = abs(base["cagr"] - 0.10) <= 0.02
    dd_within = abs(base["max_drawdown"] - (-0.22)) <= 0.05
    print(f"  CAGR within ±200 bps:    {'YES' if cagr_within else 'NO'}")
    print(f"  Max DD within ±500 bps:  {'YES' if dd_within else 'NO'}")

    last_sd = payload["weights"].index[-1]
    last_rec = next(r for r in payload["sig_log"] if r.date == last_sd)
    print(f"\n=== Latest signal ({last_sd.date()}) ===")
    rocs = ", ".join(
        f"{t}={(v*100):+.1f}%" for t, v in last_rec.roc.items() if np.isfinite(v)
    )
    print(f"  ROCs:        {rocs}")
    print(f"  Above 200dma: " + ", ".join(f"{t}={'YES' if v else 'NO'}" for t, v in last_rec.above_sma.items()))
    if last_rec.eligible:
        print(f"  Eligible:     " + " > ".join(f"{t}({r*100:+.1f}%)" for t, r in last_rec.eligible))
    if last_rec.top:
        print(f"  Hold:         " + ", ".join(f"{t}@50%" for t, _ in last_rec.top))


def main() -> None:
    payload = run_mode_a()
    emit_outputs(payload)
    print_summary(payload)


if __name__ == "__main__":
    main()
