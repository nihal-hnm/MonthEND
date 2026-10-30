"""Impulse spending detection and daily micro-budget enforcement.

Heuristics:
  1. Single discretionary spend > 1.5× daily ceiling → impulse flag
  2. Late-night (23:00–04:00) food/shopping → auto-flag
  3. 5-second cooldown review for flagged high-velocity spends
"""
from datetime import datetime
from decimal import Decimal

from categories import DISCRETIONARY_CATEGORIES, LATE_NIGHT_RISK_CATEGORIES
from formatters import money


def safe_daily_limit(disposable: Decimal, spent_so_far: Decimal, remaining_days: int) -> Decimal:
    """(disposable - spent_so_far) / remaining_days = safe daily budget."""
    remaining = money(disposable) - money(spent_so_far)
    if remaining_days <= 0:
        return Decimal("0.00")
    limit = remaining / remaining_days
    return max(Decimal("0.00"), limit.quantize(Decimal("0.01")))


def check_impulse_flags(
    amount,
    category: str,
    daily_ceiling: Decimal,
    hour_of_day: int | None = None,
) -> dict:
    """Return impulse analysis for a single expense.

    Returns:
        {
            "is_impulse": bool,
            "flags": list[str],
            "needs_cooldown": bool,
            "severity": "NONE" | "MILD" | "HIGH",
        }
    """
    amt = money(amount)
    if hour_of_day is None:
        hour_of_day = datetime.now().hour

    flags = []

    # Flag 1: exceeds 1.5× daily ceiling (discretionary only)
    ceiling_threshold = daily_ceiling * Decimal("1.5")
    if category in DISCRETIONARY_CATEGORIES and amt > ceiling_threshold and daily_ceiling > 0:
        flags.append(f"Single spend (₹{amt}) exceeds 1.5× your daily limit (₹{ceiling_threshold})")

    # Flag 2: late-night risk category (23:00 – 03:59)
    if (hour_of_day >= 23 or hour_of_day < 4) and category in LATE_NIGHT_RISK_CATEGORIES:
        flags.append(f"Late-night ({hour_of_day}:00) spending on {category}")

    # Flag 3: exceeds daily ceiling at all (any category)
    if amt > daily_ceiling and daily_ceiling > 0:
        flags.append(f"This spend alone exceeds today's safe limit (₹{daily_ceiling})")

    is_impulse = len(flags) > 0
    severity = "NONE"
    if len(flags) >= 2:
        severity = "HIGH"
    elif len(flags) == 1:
        severity = "MILD"

    # Cooldown for high-severity impulse
    needs_cooldown = severity == "HIGH"

    return {
        "is_impulse": is_impulse,
        "flags": flags,
        "needs_cooldown": needs_cooldown,
        "severity": severity,
    }


def daily_impulse_summary(expenses: list[dict], daily_ceiling: Decimal) -> dict:
    """Summarize impulse patterns for a day's worth of expenses."""
    total_impulse = Decimal("0.00")
    total_planned = Decimal("0.00")
    impulse_count = 0

    for e in expenses:
        amt = money(e.get("amount", 0))
        is_planned = e.get("is_planned", 1)
        if not is_planned:
            total_impulse += amt
            impulse_count += 1
        else:
            total_planned += amt

    total = total_impulse + total_planned
    impulse_pct = (total_impulse / total * 100) if total > 0 else Decimal("0")

    return {
        "total_spent": total,
        "impulse_total": total_impulse,
        "planned_total": total_planned,
        "impulse_count": impulse_count,
        "impulse_percentage": float(impulse_pct.quantize(Decimal("0.1"))),
        "over_budget": total > daily_ceiling,
    }

