"""Inflow cycle engine — custom billing anchor tied to payday.

Replaces calendar-month logic with a rolling cycle:
  payday_day of Month M  →  payday_day-1 of Month M+1
"""
from datetime import date, timedelta
from decimal import Decimal
from typing import NamedTuple

import database
from formatters import money


class CycleInfo(NamedTuple):
    cycle_start: date
    cycle_end: date
    day_index: int        # 1-based day within cycle
    total_days: int       # length of cycle in days
    days_remaining: int   # days left including today
    today: date


def get_current_cycle(payday_day: int, ref_date: date | None = None) -> CycleInfo:
    """Compute the billing cycle boundaries for a given payday anchor.

    Cycle runs from payday_day of Month M to (payday_day - 1) of Month M+1.
    """
    today = ref_date or date.today()
    payday_day = max(1, min(28, payday_day))  # Clamp to 1-28 for safety

    # Determine cycle start
    if today.day >= payday_day:
        # We're in the cycle that started this month
        cycle_start = today.replace(day=payday_day)
    else:
        # We're in the cycle that started last month
        first_of_month = today.replace(day=1)
        prev_month = first_of_month - timedelta(days=1)
        cycle_start = prev_month.replace(day=min(payday_day, _days_in_month(prev_month)))

    # Determine cycle end: payday_day - 1 of the next month
    if cycle_start.month == 12:
        next_month_date = cycle_start.replace(year=cycle_start.year + 1, month=1, day=1)
    else:
        next_month_date = cycle_start.replace(month=cycle_start.month + 1, day=1)

    end_day = min(payday_day - 1, _days_in_month(next_month_date))
    if end_day < 1:
        # payday_day is 1 → cycle ends on last day of same month
        if cycle_start.month == 12:
            last_day = date(cycle_start.year + 1, 1, 1) - timedelta(days=1)
        else:
            last_day = date(cycle_start.year, cycle_start.month + 1, 1) - timedelta(days=1)
        cycle_end = last_day
    else:
        cycle_end = next_month_date.replace(day=end_day)

    total_days = (cycle_end - cycle_start).days + 1
    day_index = (today - cycle_start).days + 1
    days_remaining = max(0, (cycle_end - today).days + 1)

    return CycleInfo(cycle_start, cycle_end, day_index, total_days, days_remaining, today)


def _days_in_month(dt: date) -> int:
    """Return the number of days in the month of dt."""
    if dt.month == 12:
        return 31
    return (dt.replace(month=dt.month + 1, day=1) - timedelta(days=1)).day


def cycle_status_text(cycle: CycleInfo, inflow_type: str = "SALARY") -> str:
    """Generate a human-readable cycle status chip text."""
    from categories import INFLOW_LABELS
    label = INFLOW_LABELS.get(inflow_type, inflow_type.title())
    return f"Day {cycle.day_index} of {cycle.total_days} · {cycle.days_remaining} days until {label.lower()}"


def check_inflow_overdue(payday_day: int, expected_amount, db_path=database.DB_PATH) -> bool:
    """Returns True if inflow hasn't been logged within 24h of the due date."""
    today = date.today()
    if today.day != payday_day and today.day != ((payday_day % 28) + 1):
        return False

    cycle = get_current_cycle(payday_day, today)
    # Check if any inflow exists for the current cycle start ± 1 day
    inflows = database.get_inflows_in_range(
        (cycle.cycle_start - timedelta(days=1)).isoformat(),
        (cycle.cycle_start + timedelta(days=1)).isoformat(),
        db_path,
    )
    return len(inflows) == 0 and money(expected_amount) > 0

