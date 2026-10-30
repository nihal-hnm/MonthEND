"""Historical analysis & predictive feedback engine.

Provides:
  - Month-over-month delta by category
  - Category surge detection
  - Spending persona classification
  - Run-rate projection
  - Contextual habit tips linked to savings goals
"""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

import database
from cycle_engine import CycleInfo, get_current_cycle
from formatters import money


# ─── Month-over-Month ────────────────────────────────────────────────────────

def mom_category_delta(cycle: CycleInfo, payday_day: int) -> list[dict]:
    """Compare current cycle category spend against previous cycle's same day-index.

    Returns list of {category, current, previous, delta_pct, direction}.
    """
    # Previous cycle
    prev_start = _prev_cycle_start(cycle.cycle_start, payday_day)
    prev_same_day = prev_start + timedelta(days=cycle.day_index - 1)

    current_expenses = database.list_expenses_range(
        cycle.cycle_start.isoformat(),
        cycle.today.isoformat(),
    )
    prev_expenses = database.list_expenses_range(
        prev_start.isoformat(),
        prev_same_day.isoformat(),
    )

    current_by_cat = _sum_by_category(current_expenses)
    prev_by_cat = _sum_by_category(prev_expenses)

    all_cats = set(current_by_cat.keys()) | set(prev_by_cat.keys())
    result = []
    for cat in sorted(all_cats):
        curr = current_by_cat.get(cat, Decimal("0.00"))
        prev = prev_by_cat.get(cat, Decimal("0.00"))
        if prev > 0:
            delta = float((curr - prev) / prev * 100)
        elif curr > 0:
            delta = 100.0
        else:
            delta = 0.0

        direction = "↑" if delta > 5 else ("↓" if delta < -5 else "→")
        result.append({
            "category": cat,
            "current": curr,
            "previous": prev,
            "delta_pct": round(delta, 1),
            "direction": direction,
        })

    # Sort by absolute delta descending
    result.sort(key=lambda x: abs(x["delta_pct"]), reverse=True)
    return result


def category_surges(deltas: list[dict], threshold: float = 20.0) -> list[str]:
    """Generate alert strings for categories surging above threshold %."""
    alerts = []
    for d in deltas:
        if d["delta_pct"] > threshold and d["current"] > Decimal("0"):
            alerts.append(
                f"{d['category']} is {d['delta_pct']}% higher than Day {_day_idx()} of last cycle"
            )
    return alerts


def _day_idx():
    """Current day index for messages (cached simple)."""
    return date.today().day


# ─── Spending Persona ────────────────────────────────────────────────────────

def classify_spending_persona(payday_day: int) -> dict:
    """Classify user into a spending persona based on last full cycle.

    Personas:
      - Weekend Spiker: >40% of spend on Sat/Sun
      - Mid-Month Burner: >50% of spend in days 10-20 of cycle
      - Consistent Saver: standard deviation of daily spend < 30% of mean
      - Default: "Balanced Spender"
    """
    cycle = get_current_cycle(payday_day)
    prev_start = _prev_cycle_start(cycle.cycle_start, payday_day)
    prev_end = cycle.cycle_start - timedelta(days=1)

    expenses = database.list_expenses_range(
        prev_start.isoformat(),
        prev_end.isoformat(),
    )

    if not expenses:
        return {"persona": "New User", "reason": "No previous cycle data available", "icon": "🆕"}

    # Aggregate by day
    daily_totals = defaultdict(Decimal)
    weekend_total = Decimal("0")
    mid_month_total = Decimal("0")
    grand_total = Decimal("0")

    for e in expenses:
        amt = money(e["amount"])
        d = date.fromisoformat(e["spent_on"])
        daily_totals[d] += amt
        grand_total += amt

        if d.weekday() >= 5:  # Sat=5, Sun=6
            weekend_total += amt

        day_in_cycle = (d - prev_start).days + 1
        cycle_len = (prev_end - prev_start).days + 1
        if cycle_len > 0 and 0.3 < day_in_cycle / cycle_len < 0.7:
            mid_month_total += amt

    if grand_total == 0:
        return {"persona": "Minimal Spender", "reason": "Almost no spending last cycle", "icon": "🧘"}

    weekend_pct = float(weekend_total / grand_total * 100)
    mid_pct = float(mid_month_total / grand_total * 100)

    # Check consistency (std dev)
    values = list(daily_totals.values())
    if values:
        mean_val = sum(values, Decimal("0")) / len(values)
        variance = sum((v - mean_val) ** 2 for v in values) / len(values)
        std_dev = float(variance.sqrt()) if hasattr(variance, 'sqrt') else float(variance ** Decimal("0.5"))
        cv = std_dev / float(mean_val) if mean_val > 0 else 0
    else:
        cv = 0

    if weekend_pct > 40:
        return {"persona": "Weekend Spiker", "reason": f"{weekend_pct:.0f}% of spending on weekends", "icon": "🎉"}
    elif mid_pct > 50:
        return {"persona": "Mid-Month Burner", "reason": f"{mid_pct:.0f}% of spending concentrated mid-cycle", "icon": "🔥"}
    elif cv < 0.3:
        return {"persona": "Consistent Saver", "reason": "Low variation in daily spending", "icon": "📊"}
    else:
        return {"persona": "Balanced Spender", "reason": "Steady spending pattern", "icon": "⚖️"}


# ─── Run-Rate Projection ────────────────────────────────────────────────────

def run_rate_projection(spent_so_far: Decimal, elapsed_days: int, cycle_length: int) -> dict:
    """(spent / elapsed_days) × cycle_length = projected total spend.

    Returns {daily_rate, projected_total, on_track: bool, pace_label}.
    """
    spent = money(spent_so_far)
    if elapsed_days <= 0:
        return {
            "daily_rate": Decimal("0.00"),
            "projected_total": Decimal("0.00"),
            "on_track": True,
            "pace_label": "No data yet",
        }

    daily_rate = (spent / elapsed_days).quantize(Decimal("0.01"))
    projected = (daily_rate * cycle_length).quantize(Decimal("0.01"))

    return {
        "daily_rate": daily_rate,
        "projected_total": projected,
        "on_track": True,  # Will be compared against budget by caller
        "pace_label": f"₹{daily_rate}/day → ₹{projected} projected by end of cycle",
    }


# ─── Contextual Habit Tips ───────────────────────────────────────────────────

def generate_habit_tips(
    surges: list[str],
    goals: list[dict],
    daily_rate: Decimal,
) -> list[str]:
    """Generate actionable micro-saving tips linked to savings goals."""
    tips = []

    # Link surges to savings
    goal_names = [g.get("name", "your goal") for g in goals[:3]] if goals else ["your savings"]

    # Category-specific tips
    if any("Zomato" in s or "Swiggy" in s or "food" in s.lower() for s in surges):
        saving = daily_rate * Decimal("0.3")  # ~30% of daily rate
        for gn in goal_names[:1]:
            tips.append(
                f"Skipping 2 food deliveries this week saves ~₹{saving * 2:.0f} toward {gn}"
            )

    if any("Blinkit" in s or "Zepto" in s or "quick" in s.lower() for s in surges):
        tips.append("Batch your quick-commerce orders weekly instead of daily to cut impulse buys")

    if any("Cab" in s or "Ola" in s or "Uber" in s or "Auto" in s.lower() for s in surges):
        tips.append("Switch 2 cab rides to metro/bus this week to save on transit")

    if not tips:
        if daily_rate > 0:
            tips.append(f"Cutting ₹{daily_rate * Decimal('0.1'):.0f}/day (10%) frees ₹{daily_rate * 3:.0f}/month for goals")
        tips.append("Review your top 3 spending categories for easy reduction opportunities")

    return tips[:5]


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _sum_by_category(expenses: list[dict]) -> dict[str, Decimal]:
    result = defaultdict(Decimal)
    for e in expenses:
        result[e["category"]] += money(e["amount"])
    return dict(result)


def _prev_cycle_start(current_start: date, payday_day: int) -> date:
    """Go back ~1 month to find the previous cycle start."""
    first_of_month = current_start.replace(day=1)
    prev_month = first_of_month - timedelta(days=1)
    try:
        return prev_month.replace(day=min(payday_day, _days_in_month(prev_month)))
    except ValueError:
        return prev_month.replace(day=1)


def _days_in_month(dt: date) -> int:
    if dt.month == 12:
        return 31
    return (dt.replace(month=dt.month + 1, day=1) - timedelta(days=1)).day


def get_median_monthly_surplus(payday_day: int, months_back: int = 3) -> Decimal:
    """Compute median monthly surplus (inflow - expenses) over recent cycles."""
    today = date.today()
    surpluses = []

    current_start = today
    for _ in range(months_back):
        cycle = get_current_cycle(payday_day, current_start)
        inflows = database.get_inflows_in_range(
            cycle.cycle_start.isoformat(), cycle.cycle_end.isoformat()
        )
        inflow_total = sum((money(i["amount"]) for i in inflows), Decimal("0.00"))
        spent = database.get_total_spent_in_range(
            cycle.cycle_start.isoformat(), cycle.cycle_end.isoformat()
        )
        surplus = inflow_total - spent
        if inflow_total > 0:  # Only count cycles with recorded inflows
            surpluses.append(surplus)

        # Move back to previous cycle
        current_start = cycle.cycle_start - timedelta(days=1)

    if not surpluses:
        return Decimal("0.00")

    surpluses.sort()
    mid = len(surpluses) // 2
    if len(surpluses) % 2 == 0:
        return ((surpluses[mid - 1] + surpluses[mid]) / 2).quantize(Decimal("0.01"))
    return surpluses[mid]

