"""Exact, deterministic calculations for a time-based survival budget.

Extended with cycle-aware calculations and true disposable income.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")


def money(value) -> Decimal:
    """Convert user input to a paisa-precision Decimal without float drift."""
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class BudgetMetrics:
    total_money: Decimal
    fixed_expenses: Decimal
    emergency_reserve: Decimal
    usable_pool: Decimal
    daily_allowance: Decimal
    days: int
    critical: bool
    feasible: bool
    # Extended fields (optional, default to base values)
    pending_emis: Decimal = Decimal("0.00")
    true_disposable: Decimal = Decimal("0.00")
    safe_daily_limit: Decimal = Decimal("0.00")
    spent_this_cycle: Decimal = Decimal("0.00")
    days_remaining: int = 0


def calculate_budget(total_money, fixed_expenses, days: int, reserve=0,
                     pending_emis=0, spent_this_cycle=0, days_remaining=None) -> BudgetMetrics:
    """Calculate the spendable pool after bills, reserve, and pending EMIs."""
    total = money(total_money)
    fixed = money(fixed_expenses)
    reserve_value = money(reserve)
    emis = money(pending_emis)
    spent = money(spent_this_cycle)

    if total < 0 or fixed < 0 or reserve_value < 0:
        raise ValueError("Money amounts cannot be negative.")
    if not isinstance(days, int) or days < 1 or days > 3650:
        raise ValueError("Days must be a whole number between 1 and 3,650.")

    usable = total - fixed - reserve_value
    true_disp = max(Decimal("0.00"), usable - emis)

    feasible = total - fixed > 0 and usable > 0
    allowance = max(Decimal("0.00"), usable) / days
    allowance = allowance.quantize(CENT, rounding=ROUND_HALF_UP)

    # Safe daily limit accounts for what's already been spent
    rem_days = days_remaining if days_remaining is not None else days
    if rem_days > 0:
        safe_limit = max(Decimal("0.00"), true_disp - spent) / rem_days
        safe_limit = safe_limit.quantize(CENT, rounding=ROUND_HALF_UP)
    else:
        safe_limit = Decimal("0.00")

    return BudgetMetrics(
        total_money=total,
        fixed_expenses=fixed,
        emergency_reserve=reserve_value,
        usable_pool=usable,
        daily_allowance=allowance,
        days=days,
        critical=allowance < Decimal("2.00"),
        feasible=feasible,
        pending_emis=emis,
        true_disposable=true_disp,
        safe_daily_limit=safe_limit,
        spent_this_cycle=spent,
        days_remaining=rem_days,
    )


def derive_daily_status(metrics: BudgetMetrics, spent_today) -> dict:
    spent = money(spent_today)
    # Use safe_daily_limit if available, otherwise fall back to daily_allowance
    limit = metrics.safe_daily_limit if metrics.safe_daily_limit > 0 else metrics.daily_allowance
    remaining = limit - spent
    return {
        "allowance": limit,
        "spent": spent,
        "remaining": remaining,
        "over_budget": remaining < 0,
    }
