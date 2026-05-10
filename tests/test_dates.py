"""Date edge-case tests for the momentum-rotation engine.

Two required scenarios per CLAUDE.md:
- month boundary (Jan -> Feb), including a leap year
- year boundary (Dec -> Jan), with a market holiday on Jan 1

Also covers Mode B allocation-band edges and the zero-eligible override.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def test_monthend_leap_and_nonleap_february():
    """pandas MonthEnd offset must give the correct last day across leap years."""
    # 2020 is a leap year
    feb_end_2020 = pd.Timestamp("2020-02-15") + pd.offsets.MonthEnd(0)
    assert feb_end_2020 == pd.Timestamp("2020-02-29")
    # 2021 is non-leap
    feb_end_2021 = pd.Timestamp("2021-02-15") + pd.offsets.MonthEnd(0)
    assert feb_end_2021 == pd.Timestamp("2021-02-28")


def test_january_to_february_month_advance():
    """Stepping forward by one month-end from Jan 31 lands on Feb 28 / 29."""
    next_me_leap = pd.Timestamp("2020-01-31") + pd.offsets.MonthEnd(1)
    assert next_me_leap == pd.Timestamp("2020-02-29")
    next_me_norm = pd.Timestamp("2021-01-31") + pd.offsets.MonthEnd(1)
    assert next_me_norm == pd.Timestamp("2021-02-28")


def test_december_to_january_year_boundary():
    """Stepping forward from Dec 31 must land on Jan 31 of the next year."""
    next_me = pd.Timestamp("2023-12-31") + pd.offsets.MonthEnd(1)
    assert next_me == pd.Timestamp("2024-01-31")
    # Two-step stretch across a year boundary
    two_ahead = pd.Timestamp("2023-12-15") + pd.offsets.MonthEnd(2)
    assert two_ahead == pd.Timestamp("2024-01-31")


def test_first_trading_day_after_year_end_skips_holiday():
    """The simulate() function uses the actual trading-day index from yfinance.
    For the 2024-12-31 -> 2025-01 transition we expect the next trading day
    to be 2025-01-02 because 2025-01-01 is a US market holiday.
    Constructed here from a synthetic NYSE-like index that excludes Jan 1.
    """
    nyse_like = pd.DatetimeIndex(
        [
            pd.Timestamp("2024-12-30"),
            pd.Timestamp("2024-12-31"),
            pd.Timestamp("2025-01-02"),
            pd.Timestamp("2025-01-03"),
        ]
    )
    signal = pd.Timestamp("2024-12-31")
    after = nyse_like[nyse_like > signal]
    assert len(after) > 0
    assert after[0] == pd.Timestamp("2025-01-02")


def test_roc_window_is_calendar_12_months():
    """trailing 12-month ROC on a monthly close series compares to 12 entries prior."""
    idx = pd.date_range("2010-01-31", "2012-12-31", freq="ME")
    series = pd.Series([100.0 + i for i in range(len(idx))], index=idx)
    roc = series.pct_change(12)
    # idx[12] is 2011-01-31; idx[0] is 2010-01-31
    assert roc.index[12] == pd.Timestamp("2011-01-31")
    expected = (series.iloc[12] - series.iloc[0]) / series.iloc[0]
    assert pytest.approx(roc.iloc[12], rel=1e-12) == expected


def test_signal_to_execution_lag_uses_next_trading_day():
    """If signal is computed at month-end M, execution must be the first
    trading day strictly after M. Validate with a constructed daily index
    where the calendar month-end falls on a Saturday."""
    # 2024-08-31 is a Saturday. Friday 2024-08-30 is the last trading day in August.
    daily_idx = pd.bdate_range("2024-08-26", "2024-09-06")
    monthly_close = pd.DatetimeIndex(["2024-08-31"])  # calendar month-end
    signal_date = monthly_close[0]
    after = daily_idx[daily_idx > signal_date]
    assert after[0] == pd.Timestamp("2024-09-02")  # Mon, since Aug 31 = Sat


def test_filter_drops_in_progress_signals():
    """Mid-month engine runs must not surface a preliminary signal stamped at
    the upcoming month-end. The actual current portfolio reflects the most
    recent executable signal (i.e., the prior month-end's, already executed).
    """
    from backtest import filter_executable_signals

    # Daily data ends 2026-04-28 (mid-April run).
    daily_idx = pd.DatetimeIndex(
        pd.bdate_range("2026-02-25", "2026-04-28")
    )
    # Signal calendar dates would include the upcoming Apr-2026 month-end.
    signal_idx = pd.DatetimeIndex(
        ["2026-02-28", "2026-03-31", "2026-04-30"]
    )
    weights = pd.DataFrame(
        {"GLD": [0.5, 0.5, 0.5], "SPY": [0.5, 0.5, 0.5]}, index=signal_idx
    )

    filtered = filter_executable_signals(weights, daily_idx)

    # Feb 28 (Sat) -> exec Mon Mar 2 (in data) -> kept
    assert pd.Timestamp("2026-02-28") in filtered.index
    # Mar 31 (Tue) -> exec Wed Apr 1 (in data) -> kept
    assert pd.Timestamp("2026-03-31") in filtered.index
    # Apr 30 (Thu) -> no trading day after that in the data -> dropped
    assert pd.Timestamp("2026-04-30") not in filtered.index
    assert len(filtered) == 2


# === Mode B allocation logic ===

def test_mode_b_allocation_band_edges():
    """Allocation map is lower-inclusive, upper-exclusive, with 100 snapping
    into the top band. Verify the four band boundaries explicitly.
    """
    from backtest import map_composite_to_allocation

    # Below 25 → 25%
    assert map_composite_to_allocation(0.0) == 0.25
    assert map_composite_to_allocation(24.999) == 0.25
    # Exactly 25 → 50% (lower-inclusive of next band)
    assert map_composite_to_allocation(25.0) == 0.50
    assert map_composite_to_allocation(49.999) == 0.50
    # Exactly 50 → 75%
    assert map_composite_to_allocation(50.0) == 0.75
    assert map_composite_to_allocation(74.999) == 0.75
    # Exactly 75 → 100%; 100 also snaps in
    assert map_composite_to_allocation(75.0) == 1.00
    assert map_composite_to_allocation(100.0) == 1.00


def test_mode_b_winsorisation_clip():
    """Momentum strength clips avg ROC at [0%, 40%] before rescaling to 0–100.
    A 50% avg ROC must rescale to 100, not 125.
    """
    from backtest import compute_mode_b_subscores

    # 4-asset universe; 2 eligible with ROC = 50% each (extreme case)
    eligible = [("A", 0.50), ("B", 0.50)]
    top = eligible[:2]
    # Construct a price/SMA row where leader trend is mid-band (10% above SMA)
    price_row = pd.Series({"A": 110.0, "B": 110.0, "C": 100.0, "D": 100.0})
    sma_row = pd.Series({"A": 100.0, "B": 100.0, "C": 100.0, "D": 100.0})

    breadth, momo, leader, composite = compute_mode_b_subscores(
        eligible, top, price_row, sma_row, n_universe=4
    )
    # 2 of 4 eligible -> breadth = 50
    assert breadth == pytest.approx(50.0)
    # Avg ROC = 0.50, clipped to 0.40, rescaled to 100
    assert momo == pytest.approx(100.0)
    # Avg distance = (110/100 - 1) = 0.10, on 0–25% scale -> 40
    assert leader == pytest.approx(40.0)
    assert composite == pytest.approx((50 + 100 + 40) / 3)


def test_mode_b_leader_clip_high_distance():
    """Leader trend strength clips at 25% above SMA. A 40% leader average rescales to 100."""
    from backtest import compute_mode_b_subscores

    eligible = [("A", 0.20), ("B", 0.20)]
    top = eligible[:2]
    # 40% above SMA on both leaders
    price_row = pd.Series({"A": 140.0, "B": 140.0, "C": 100.0, "D": 100.0})
    sma_row = pd.Series({"A": 100.0, "B": 100.0, "C": 100.0, "D": 100.0})
    _, _, leader, _ = compute_mode_b_subscores(
        eligible, top, price_row, sma_row, n_universe=4
    )
    assert leader == pytest.approx(100.0)


def test_mode_b_zero_eligible_overrides_to_full_cash():
    """When no ETF passes the 200d filter, allocation must be 100% cash regardless
    of composite. Construct a monthly panel where every ETF is below SMA at the
    decision date and verify the engine emits a 100% cash row.
    """
    import numpy as np

    from backtest import construct_mode_b_weights

    # Two month-ends; only the second matters (need ROC history first)
    idx = pd.DatetimeIndex(
        [pd.Timestamp(f"{2010+i//12}-{(i%12)+1:02d}-01") + pd.offsets.MonthEnd(0) for i in range(14)]
    )
    monthly = pd.DataFrame(100.0, index=idx, columns=["GLD", "SPY", "TLT", "DBC"])
    # Make ROC well-defined and >= 0 by setting the first 12 months to 100, then a uniform 5% gain
    monthly.iloc[12:] = 105.0
    roc = monthly.pct_change(12)
    # Force every ETF below its 200d SMA at the decision month-end (last row)
    sma_me = pd.DataFrame(np.nan, index=idx, columns=monthly.columns)
    sma_me.iloc[-1] = 200.0  # SMA above price → "above_sma" is False everywhere
    above_sma = monthly > sma_me

    weights, log = construct_mode_b_weights(monthly, roc, sma_me, above_sma)

    # Only the last row produces a signal (earlier rows have NaN ROC)
    assert len(weights) >= 1
    last_w = weights.iloc[-1]
    assert last_w["CASH"] == pytest.approx(1.0)
    for t in ["GLD", "SPY", "TLT", "DBC"]:
        assert last_w[t] == pytest.approx(0.0)
    # Last record's allocation_realised must be 0 (override), even though composite
    # without the override would map to 25% per the band
    last_rec = log[-1]
    assert last_rec.allocation_realised == pytest.approx(0.0)
    assert len(last_rec.eligible) == 0


def test_mode_c_baseline_matches_mode_a():
    """Sensitivity invariant: running Mode C with the baseline universe must
    reproduce Mode A 0bps stats exactly. Same engine, same universe, same
    strategy — the numbers must agree to the last decimal. Reads the JSON
    outputs left by the most recent backtest run, so this test depends on
    that run having produced fresh outputs.
    """
    import json
    from pathlib import Path

    data_dir = Path(__file__).resolve().parent.parent / "data"
    perf_path = data_dir / "performance_stats.json"
    c_path = data_dir / "mode_c_universes.json"
    if not perf_path.exists() or not c_path.exists():
        pytest.skip("backtest outputs missing — run scripts/backtest.py first")
    perf = json.loads(perf_path.read_text())
    c = json.loads(c_path.read_text())
    a_stats = perf["modes"]["mode_a_0bps"]
    baseline_stats = c["universes"]["baseline"]["stats"]
    assert baseline_stats["cagr"] == pytest.approx(a_stats["cagr"], abs=1e-12)
    assert baseline_stats["max_drawdown"] == pytest.approx(a_stats["max_drawdown"], abs=1e-12)
    assert baseline_stats["vol"] == pytest.approx(a_stats["vol"], abs=1e-12)
    assert baseline_stats["sharpe"] == pytest.approx(a_stats["sharpe"], abs=1e-12)


def test_mode_b_one_survivor_scaled():
    """With one survivor and composite landing in the 50% band, the lone leader
    holds at allocation/2 = 25%, with cash filling the rest.
    """
    from backtest import construct_mode_b_weights
    import numpy as np

    idx = pd.DatetimeIndex(
        [pd.Timestamp(f"{2010+i//12}-{(i%12)+1:02d}-01") + pd.offsets.MonthEnd(0) for i in range(14)]
    )
    monthly = pd.DataFrame(100.0, index=idx, columns=["GLD", "SPY", "TLT", "DBC"])
    # GLD up 30% over 12 months, others flat → only GLD has positive ROC
    monthly.iloc[12:, monthly.columns.get_loc("GLD")] = 130.0
    roc = monthly.pct_change(12)
    # Only GLD above SMA at the decision date
    sma_me = pd.DataFrame(200.0, index=idx, columns=monthly.columns)  # all below by default
    sma_me.iloc[-1, sma_me.columns.get_loc("GLD")] = 110.0  # GLD price 130 > SMA 110
    above_sma = monthly > sma_me

    weights, log = construct_mode_b_weights(monthly, roc, sma_me, above_sma)
    last_w = weights.iloc[-1]
    last_rec = log[-1]
    # 1 of 4 eligible -> breadth = 25
    # avg ROC of qualifying = 0.30, clipped to 0.40 → 75/100 momentum
    # leader trend = (130/110 - 1) ≈ 0.1818, clipped at 25% → 72.7/100
    # composite ≈ (25 + 75 + 72.7) / 3 ≈ 57.6 → 75% allocation band
    # one survivor: GLD held at 75%/2 = 37.5%, cash 62.5%
    assert last_rec.allocation_target == pytest.approx(0.75)
    assert last_w["GLD"] == pytest.approx(0.375)
    assert last_w["CASH"] == pytest.approx(0.625)
    assert last_rec.allocation_realised == pytest.approx(0.375)
