"""Debt, EMI & liability subsystem.

Computes true disposable income after pending EMIs and manages
liability lifecycle (add, pay, track).
"""
from datetime import date
from decimal import Decimal

import database
from formatters import money


def true_disposable_income(balance, cycle_start: str, cycle_end: str) -> Decimal:
    """balance - Σ(pending EMIs this cycle) = true disposable income."""
    bal = money(balance)
    pending = database.get_pending_emis_total(cycle_start, cycle_end)
    return max(Decimal("0.00"), bal - pending)


def get_pending_emis_this_cycle(cycle_start: str, cycle_end: str) -> list[dict]:
    """Get active liabilities with payment status for this cycle."""
    liabilities = database.list_liabilities(status="ACTIVE")
    result = []
    for lia in liabilities:
        payments = database.list_liability_payments(lia["id"])
        paid_this_cycle = any(
            cycle_start <= p["paid_date"] <= cycle_end for p in payments
        )
        result.append({
            **lia,
            "paid_this_cycle": paid_this_cycle,
            "emi_status": _emi_status(lia, paid_this_cycle),
        })
    return result


def _emi_status(liability: dict, paid_this_cycle: bool) -> str:
    """Determine EMI status: PAID, DUE, OVERDUE, or UPCOMING."""
    if paid_this_cycle:
        return "PAID"
    today_day = date.today().day
    due_day = liability["due_day"]
    if today_day > due_day:
        return "OVERDUE"
    elif today_day == due_day:
        return "DUE"
    return "UPCOMING"


def pay_emi(liability_id: int, amount=None):
    """Mark an EMI as paid: log payment, decrement tenure, auto-log expense."""
    lia = None
    for l in database.list_liabilities(status=None):
        if l["id"] == liability_id:
            lia = l
            break
    if lia is None:
        return None

    pay_amount = money(amount) if amount else money(lia["emi_amount"])
    today_str = date.today().isoformat()

    # Log the payment
    payment_id = database.add_liability_payment(liability_id, pay_amount, today_str)

    # Auto-log as expense
    database.add_expense(
        plan_id=None,
        amount=pay_amount,
        category=f"EMI: {lia['name']}",
        note=f"Auto-logged EMI payment for {lia['name']}",
        spent_on=today_str,
        payment_mode="UPI",
        is_planned=True,
    )

    # Decrement tenure
    database.decrement_tenure(liability_id)

    return payment_id


def total_monthly_emi_burden() -> Decimal:
    """Sum of all active EMI amounts."""
    liabilities = database.list_liabilities(status="ACTIVE")
    return sum((money(l["emi_amount"]) for l in liabilities), Decimal("0.00"))

