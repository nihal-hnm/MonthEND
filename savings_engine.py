"""Time-bound savings & wishlist target module.

Handles monthly allocation, feasibility checks, milestone tracking,
and internal fund transfers.
"""
from datetime import date
from decimal import Decimal
from typing import NamedTuple

import database
from formatters import money


class GoalProgress(NamedTuple):
    goal_id: int
    name: str
    target: Decimal
    current: Decimal
    remaining: Decimal
    pct: float            # 0.0 – 100.0
    monthly_needed: Decimal
    months_left: int
    is_feasible: bool
    milestone: str        # "0%", "25%", "50%", "75%", "100%"
    status: str


def monthly_allocation(target, current, months_remaining: int) -> Decimal:
    """(target - current) / months_remaining"""
    remaining = money(target) - money(current)
    if months_remaining <= 0:
        return remaining if remaining > 0 else Decimal("0.00")
    return (remaining / months_remaining).quantize(Decimal("0.01"))


def months_until(deadline_str: str | None) -> int:
    """Months between today and the deadline date."""
    if not deadline_str:
        return 12  # Default to 1 year if no deadline
    try:
        deadline = date.fromisoformat(deadline_str)
    except (ValueError, TypeError):
        return 12
    today = date.today()
    months = (deadline.year - today.year) * 12 + (deadline.month - today.month)
    return max(0, months)


def get_milestone(pct: float) -> str:
    """Map percentage to nearest milestone label."""
    if pct >= 100:
        return "100%"
    elif pct >= 75:
        return "75%"
    elif pct >= 50:
        return "50%"
    elif pct >= 25:
        return "25%"
    return "0%"


def compute_goal_progress(goal: dict) -> GoalProgress:
    """Compute full progress info for a single savings goal."""
    target = money(goal["target_amount"])
    current = money(goal["current_amount"])
    remaining = max(Decimal("0.00"), target - current)
    pct = float(current / target * 100) if target > 0 else 0.0
    m_left = months_until(goal.get("deadline"))
    m_needed = monthly_allocation(target, current, m_left)

    return GoalProgress(
        goal_id=goal["id"],
        name=goal["name"],
        target=target,
        current=current,
        remaining=remaining,
        pct=round(pct, 1),
        monthly_needed=m_needed,
        months_left=m_left,
        is_feasible=True,  # Updated by feasibility_check
        milestone=get_milestone(pct),
        status=goal.get("status", "ACTIVE"),
    )


def feasibility_check(goals: list[GoalProgress], median_monthly_surplus: Decimal) -> list[GoalProgress]:
    """Compare total required monthly allocations against historical surplus.

    If total allocation > surplus → mark infeasible goals.
    """
    total_allocation = sum(g.monthly_needed for g in goals if g.status == "ACTIVE")
    surplus = money(median_monthly_surplus)

    if total_allocation <= surplus:
        return goals  # All feasible

    # Mark goals as infeasible if combined they exceed surplus
    result = []
    cumulative = Decimal("0.00")
    for g in goals:
        if g.status != "ACTIVE":
            result.append(g)
            continue
        cumulative += g.monthly_needed
        is_feasible = cumulative <= surplus
        result.append(g._replace(is_feasible=is_feasible))

    return result


def transfer_to_goal(goal_id: int, amount) -> bool:
    """Transfer money from disposable balance to a savings goal."""
    amt = money(amount)
    if amt <= 0:
        return False
    database.add_savings_transfer(goal_id, amt)
    return True


def total_monthly_savings_needed() -> Decimal:
    """Sum of monthly allocations for all active goals."""
    goals = database.list_savings_goals(status="ACTIVE")
    total = Decimal("0.00")
    for g in goals:
        progress = compute_goal_progress(g)
        total += progress.monthly_needed
    return total

