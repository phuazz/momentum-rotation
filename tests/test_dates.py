"""Date edge-case tests for the momentum-rotation engine.

Two required scenarios per CLAUDE.md:
- month boundary (Jan -> Feb), including a leap year
- year boundary (Dec -> Jan), with a market holiday on Jan 1
"""

from __future__ import annotations

import pandas as pd
import pytest


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
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
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
