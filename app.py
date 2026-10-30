"""Survival Budget — Offline-first Indian personal finance dashboard.

Tabs: Dashboard, Quick Log, Liabilities, Savings Goals, Analytics, Plan & Guidance, History
"""
import json
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import streamlit as st

import database
from analytics_engine import (
    category_surges,
    classify_spending_persona,
    generate_habit_tips,
    get_median_monthly_surplus,
    mom_category_delta,
    run_rate_projection,
)
from budget_engine import BudgetMetrics, calculate_budget, derive_daily_status, money
from categories import (
    BNPL_PRESETS,
    CATEGORIES,
    FLAT_CATEGORIES,
    INFLOW_LABELS,
    INFLOW_TYPES,
    LIABILITY_TYPES,
    PAYMENT_MODES,
)
from cycle_engine import CycleInfo, check_inflow_overdue, cycle_status_text, get_current_cycle
from formatters import format_inr, money as fmoney
from impulse_engine import check_impulse_flags, daily_impulse_summary, safe_daily_limit
from liability_engine import (
    get_pending_emis_this_cycle,
    pay_emi,
    total_monthly_emi_burden,
    true_disposable_income,
)
from llm_client import STATIC_GUIDE, LLMError, generate_advice
from savings_engine import (
    compute_goal_progress,
    feasibility_check,
    total_monthly_savings_needed,
    transfer_to_goal,
)

# ─── Page Config ──────────────────────────────────────────────────────────────

st.set_page_config(page_title="MonthEnd", page_icon="📅", layout="wide")
database.init_db()

# ─── Load Settings ────────────────────────────────────────────────────────────

settings = database.get_settings()
CURRENCY = settings.get("currency", "₹")
PAYDAY = settings.get("payday_day_of_month", 1)
INFLOW_TYPE = settings.get("inflow_type", "SALARY")
EXPECTED_AMOUNT = fmoney(settings.get("expected_amount", 0))

# ─── Cycle Computation ───────────────────────────────────────────────────────

cycle = get_current_cycle(PAYDAY)

# ─── Header ───────────────────────────────────────────────────────────────────


st.title("📅 MonthEnd")
st.caption("Beat the month-end crunch — track every ₹, pace your cycle, kill impulse spends.")





# Cycle status chip
chip_text = cycle_status_text(cycle, INFLOW_TYPE)
st.markdown(
    f'<div style="background:#1e293b;color:#38bdf8;padding:8px 16px;border-radius:8px;'
    f'font-weight:600;display:inline-block;margin-bottom:8px;border:1px solid #334155;">'
    f'📅 {chip_text}</div>',
    unsafe_allow_html=True,
)

# Overdue inflow alert
if check_inflow_overdue(PAYDAY, EXPECTED_AMOUNT):
    st.error(
        f"⚠️ Your {INFLOW_LABELS.get(INFLOW_TYPE, 'income')} hasn't been logged yet! "
        f"Expected around day {PAYDAY} of the month. Log it in the sidebar."
    )

st.info("This tool provides automated financial survival pacing — not certified financial advice. All data stays on this device.")

# ─── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("⚙️ Settings")

    with st.expander("Cycle & Inflow Settings", expanded=False):
        new_payday = st.number_input(
            "Payday / inflow day of month", min_value=1, max_value=28,
            value=PAYDAY, step=1, help="Which day your income arrives"
        )
        new_inflow_type = st.selectbox(
            "Income type", INFLOW_TYPES,
            index=INFLOW_TYPES.index(INFLOW_TYPE) if INFLOW_TYPE in INFLOW_TYPES else 0,
            format_func=lambda x: INFLOW_LABELS.get(x, x),
        )
        new_expected = st.number_input(
            "Expected monthly income (₹)", min_value=0.0,
            value=float(EXPECTED_AMOUNT), step=500.0, format="%.0f",
        )
        if st.button("Save Settings", key="save_settings"):
            database.save_settings(new_payday, new_inflow_type, new_expected)
            st.success("Settings saved!")
            st.rerun()

    st.divider()
    st.header("💰 Your Runway")
    total = st.number_input("Available money (₹)", min_value=0.0, value=float(EXPECTED_AMOUNT or 5000.0), step=500.0, format="%.0f")
    fixed = st.number_input("Fixed bills / obligations (₹)", min_value=0.0, value=0.0, step=100.0, format="%.0f")
    reserve = st.number_input("Emergency reserve (₹)", min_value=0.0, value=0.0, step=100.0, format="%.0f")

    st.divider()
    st.subheader("📝 Plan Context")
    plan_name = st.text_input("Plan name", value="Monthly survival plan")
    pantry = st.text_area("Food at hand", placeholder="Rice, dal, atta, oil…")
    lifestyle = st.text_input("Lifestyle", placeholder="Student, shared PG…")
    location = st.text_input("Region", placeholder="Mumbai / Delhi / Bangalore…")

    st.divider()
    with st.expander("🗑️ Reset & Clear Data", expanded=False):
        st.caption("Erase all logged expenses, plans, inflows, liabilities, and goals from this device.")
        if st.button("Clear All Data & Cache", type="secondary", use_container_width=True):
            database.clear_all_data()
            st.session_state.clear()
            st.cache_data.clear()
            st.success("All data and session cache cleared!")
            st.rerun()
        inflow_amt = st.number_input("Inflow amount (₹)", min_value=0.0, step=500.0, format="%.0f", key="inflow_amt")
        inflow_note = st.text_input("Inflow note", key="inflow_note", placeholder="October salary…")
        if st.button("Log Inflow", key="log_inflow"):
            if inflow_amt > 0:
                database.add_inflow(
                    inflow_amt, INFLOW_TYPE, date.today().isoformat(),
                    cycle.cycle_start.isoformat(), cycle.cycle_end.isoformat(), inflow_note,
                )
                st.success(f"Inflow of {format_inr(inflow_amt)} logged!")
                st.rerun()

# ─── Core Budget Computation ─────────────────────────────────────────────────

# Get cycle-aware spending
spent_this_cycle = database.get_total_spent_in_range(
    cycle.cycle_start.isoformat(), cycle.cycle_end.isoformat()
)
pending_emis = database.get_pending_emis_total(
    cycle.cycle_start.isoformat(), cycle.cycle_end.isoformat()
)

try:
    metrics = calculate_budget(
        total, fixed, cycle.total_days, reserve,
        pending_emis=pending_emis,
        spent_this_cycle=spent_this_cycle,
        days_remaining=cycle.days_remaining,
    )
except (ValueError, InvalidOperation) as exc:
    st.error(str(exc))
    st.stop()

if money(total) - money(fixed) <= 0:
    st.error("Fixed obligations exceed available capital. Address bills first.")

# ─── Top Metrics Row ─────────────────────────────────────────────────────────

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Available", format_inr(metrics.total_money))
m2.metric("After Bills", format_inr(max(Decimal("0"), metrics.total_money - metrics.fixed_expenses)))
m3.metric("True Disposable", format_inr(metrics.true_disposable), help="After bills, reserve & pending EMIs")
m4.metric("Safe Daily Limit", format_inr(metrics.safe_daily_limit), f"{cycle.days_remaining} days left")
m5.metric("Spent This Cycle", format_inr(spent_this_cycle))

if metrics.critical and metrics.usable_pool > 0:
    st.error("🚨 Extreme Austerity: daily limit is very low. Prioritize mess/tiffin, cut all discretionary spend.")

# ─── Tabs ─────────────────────────────────────────────────────────────────────

tab_dash, tab_log, tab_lia, tab_save, tab_analytics, tab_plan, tab_history = st.tabs([
    "📊 Dashboard", "⚡ Quick Log", "🏦 Liabilities", "🎯 Savings Goals",
    "📈 Analytics", "🧠 Plan & Guidance", "📋 History",
])

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 1: DASHBOARD
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_dash:
    st.subheader("Cycle Overview")

    # Run-rate projection
    projection = run_rate_projection(spent_this_cycle, cycle.day_index, cycle.total_days)
    proj_col1, proj_col2, proj_col3 = st.columns(3)
    proj_col1.metric("Daily Burn Rate", format_inr(projection["daily_rate"]))
    proj_col2.metric("Projected Total Spend", format_inr(projection["projected_total"]))
    on_track = projection["projected_total"] <= metrics.true_disposable
    proj_col3.metric(
        "Pace Status",
        "✅ On Track" if on_track else "⚠️ Over-pace",
        delta=f"{format_inr(metrics.true_disposable - projection['projected_total'])} headroom"
        if on_track else f"{format_inr(projection['projected_total'] - metrics.true_disposable)} over",
        delta_color="normal" if on_track else "inverse",
    )

    st.divider()

    # Spending persona
    persona = classify_spending_persona(PAYDAY)
    st.markdown(
        f'<div style="background:#1e293b;padding:12px 20px;border-radius:10px;border:1px solid #334155;margin:8px 0;">'
        f'<span style="font-size:1.5em">{persona["icon"]}</span> '
        f'<strong style="color:#e2e8f0;font-size:1.1em">{persona["persona"]}</strong> '
        f'<span style="color:#94a3b8"> — {persona["reason"]}</span></div>',
        unsafe_allow_html=True,
    )

    # Today's spending summary
    st.divider()
    st.subheader("Today's Spending")
    today_str = date.today().isoformat()
    today_expenses = database.list_expenses(spent_on=today_str)
    today_spent = sum((money(e["amount"]) for e in today_expenses), Decimal("0.00"))
    status = derive_daily_status(metrics, today_spent)

    tc1, tc2, tc3 = st.columns(3)
    tc1.metric("Spent Today", format_inr(status["spent"]))
    tc2.metric("Safe Limit Today", format_inr(status["allowance"]))
    tc3.metric("Remaining Today", format_inr(status["remaining"]))

    if status["over_budget"]:
        st.warning("⚠️ You've exceeded today's safe limit. Avoid further discretionary spending.")

    # Today's impulse summary
    if today_expenses:
        imp_summary = daily_impulse_summary(today_expenses, metrics.safe_daily_limit)
        if imp_summary["impulse_count"] > 0:
            st.markdown(
                f'<div style="background:#431407;padding:10px 16px;border-radius:8px;border:1px solid #92400e;color:#fbbf24;">'
                f'⚡ {imp_summary["impulse_count"]} impulse spend(s) today '
                f'({format_inr(imp_summary["impulse_total"])}) — '
                f'{imp_summary["impulse_percentage"]}% of total</div>',
                unsafe_allow_html=True,
            )

    # EMI status
    st.divider()
    st.subheader("EMI Status")
    pending_list = get_pending_emis_this_cycle(cycle.cycle_start.isoformat(), cycle.cycle_end.isoformat())
    if pending_list:
        for lia in pending_list:
            status_color = {
                "PAID": "🟢", "UPCOMING": "🔵", "DUE": "🟡", "OVERDUE": "🔴"
            }.get(lia["emi_status"], "⚪")
            st.markdown(
                f"{status_color} **{lia['name']}** — {format_inr(lia['emi_amount'])} "
                f"(Due day {lia['due_day']}) — {lia['emi_status']}"
            )
    else:
        st.caption("No active EMIs/liabilities.")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 2: QUICK LOG
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_log:
    # Safe daily limit banner
    st.markdown(
        f'<div style="background:#0f172a;padding:12px 20px;border-radius:10px;border:1px solid #1e40af;'
        f'text-align:center;margin-bottom:16px;">'
        f'<span style="color:#60a5fa;font-size:0.85em;">SAFE DAILY LIMIT</span><br>'
        f'<span style="color:#f1f5f9;font-size:1.8em;font-weight:700;">{format_inr(metrics.safe_daily_limit)}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    with st.form("quick_expense_form", clear_on_submit=True):
        qc1, qc2 = st.columns(2)
        amount = qc1.number_input("Amount (₹)", min_value=1.0, value=50.0, step=10.0, format="%.0f")

        # Category with groups
        all_cats_flat = []
        for group_name, items in CATEGORIES.items():
            for item in items:
                all_cats_flat.append(f"{item}")
        category = qc2.selectbox("Category", all_cats_flat)

        qc3, qc4 = st.columns(2)
        payment_mode = qc3.selectbox("Payment Mode", PAYMENT_MODES)
        spend_date = qc4.date_input("Date", value=date.today())

        qc5, qc6 = st.columns(2)
        is_planned = qc5.toggle("Was this planned?", value=True)
        note = qc6.text_input("Note (optional)")

        submitted = st.form_submit_button("💾 Log Expense", type="primary", use_container_width=True)

    if submitted:
        hour_now = datetime.now().hour

        # Impulse check before saving
        impulse_result = check_impulse_flags(
            amount, category, metrics.safe_daily_limit, hour_now
        )

        if impulse_result["needs_cooldown"]:
            # Show cooldown warning
            st.warning("🛑 **HIGH IMPULSE ALERT** — Review before confirming:")
            for flag in impulse_result["flags"]:
                st.markdown(f"  - ⚠️ {flag}")
            st.markdown("*5-second review period...*")

            # Store in session for delayed confirmation
            if "cooldown_pending" not in st.session_state:
                st.session_state.cooldown_pending = {
                    "amount": amount, "category": category, "note": note,
                    "payment_mode": payment_mode, "is_planned": is_planned,
                    "spend_date": spend_date.isoformat(), "hour": hour_now,
                    "time": time.time(),
                }
        else:
            # Save immediately
            if impulse_result["is_impulse"]:
                for flag in impulse_result["flags"]:
                    st.info(f"💡 {flag}")

            database.add_expense(
                plan_id=None, amount=money(amount), category=category,
                note=note, spent_on=spend_date.isoformat(),
                payment_mode=payment_mode, is_planned=is_planned, hour_logged=hour_now,
            )
            st.success(f"Expense of {format_inr(amount)} logged to {category}!")
            st.rerun()

    # Handle cooldown confirmation
    if "cooldown_pending" in st.session_state:
        pending = st.session_state.cooldown_pending
        elapsed = time.time() - pending["time"]
        if elapsed >= 5:
            col_confirm, col_cancel = st.columns(2)
            with col_confirm:
                if st.button("✅ Confirm Anyway", type="primary", use_container_width=True):
                    database.add_expense(
                        plan_id=None, amount=money(pending["amount"]),
                        category=pending["category"], note=pending["note"],
                        spent_on=pending["spend_date"], payment_mode=pending["payment_mode"],
                        is_planned=pending["is_planned"], hour_logged=pending["hour"],
                    )
                    del st.session_state.cooldown_pending
                    st.success(f"Expense of {format_inr(pending['amount'])} logged (impulse flagged)!")
                    st.rerun()
            with col_cancel:
                if st.button("❌ Cancel", use_container_width=True):
                    del st.session_state.cooldown_pending
                    st.info("Impulse spend cancelled. Good save! 💪")
                    st.rerun()
        else:
            remaining_secs = int(5 - elapsed)
            st.info(f"⏳ Review period: {remaining_secs}s remaining…")
            time.sleep(1)
            st.rerun()

    # Today's log
    st.divider()
    st.subheader("Today's Expenses")
    today_entries = database.list_expenses(spent_on=today_str)
    if today_entries:
        formatted = [
            {
                "Time": f"{e.get('hour_logged', 12)}:00",
                "Category": e["category"],
                "Mode": e.get("payment_mode", "UPI"),
                "Planned": "✅" if e.get("is_planned", 1) else "⚡",
                "Note": e["note"] or "",
                "Amount": format_inr(e["amount"]),
            }
            for e in today_entries
        ]
        st.dataframe(formatted, use_container_width=True, hide_index=True)

        delete_id = st.selectbox(
            "Remove an expense", [None] + [e["id"] for e in today_entries],
            format_func=lambda x: "Choose…" if x is None else f"Expense #{x}",
        )
        if delete_id is not None and st.button("Delete selected expense"):
            database.delete_expense(delete_id)
            st.rerun()
    else:
        st.caption("No expenses logged today.")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 3: LIABILITIES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_lia:
    st.subheader("EMI & Liability Manager")

    # Total EMI burden
    emi_total = total_monthly_emi_burden()
    if emi_total > 0:
        st.metric("Total Monthly EMI Burden", format_inr(emi_total))

    # Active liabilities
    liabilities = database.list_liabilities(status="ACTIVE")
    if liabilities:
        for lia in liabilities:
            with st.expander(f"{'🔴' if lia['type'] == 'BNPL' else '🏦'} {lia['name']} — {format_inr(lia['emi_amount'])}/month"):
                lc1, lc2, lc3 = st.columns(3)
                lc1.markdown(f"**Type:** {lia['type']}")
                lc2.markdown(f"**Due Day:** {lia['due_day']}")
                lc3.markdown(f"**Tenure Left:** {lia['tenure_remaining']} months")

                if lia.get("principal") and fmoney(lia["principal"]) > 0:
                    st.markdown(f"**Principal:** {format_inr(lia['principal'])} | **Rate:** {lia['interest_rate']}%")

                if lia.get("note"):
                    st.caption(lia["note"])

                # Payment history
                payments = database.list_liability_payments(lia["id"])
                paid_this_cycle = any(
                    cycle.cycle_start.isoformat() <= p["paid_date"] <= cycle.cycle_end.isoformat()
                    for p in payments
                )

                if paid_this_cycle:
                    st.success("✅ Paid this cycle")
                else:
                    if st.button(f"💸 Pay EMI ({format_inr(lia['emi_amount'])})", key=f"pay_emi_{lia['id']}"):
                        pay_emi(lia["id"])
                        st.success(f"EMI for {lia['name']} marked as paid!")
                        st.rerun()

                if st.button("🗑️ Delete", key=f"del_lia_{lia['id']}"):
                    database.delete_liability(lia["id"])
                    st.rerun()
    else:
        st.caption("No active liabilities. Add one below.")

    # Add new liability
    st.divider()
    st.subheader("Add New Liability")

    # Quick presets
    preset_names = ["Custom"] + list(BNPL_PRESETS.keys())
    selected_preset = st.selectbox("Quick Preset", preset_names)

    with st.form("add_liability_form", clear_on_submit=True):
        preset = BNPL_PRESETS.get(selected_preset, {})
        fc1, fc2 = st.columns(2)
        lia_name = fc1.text_input("Name", value=selected_preset if selected_preset != "Custom" else "")
        lia_type = fc2.selectbox("Type", LIABILITY_TYPES,
                                  index=LIABILITY_TYPES.index(preset.get("type", "EMI")) if preset.get("type") in LIABILITY_TYPES else 0)

        fc3, fc4, fc5 = st.columns(3)
        lia_emi = fc3.number_input("EMI / Monthly Amount (₹)", min_value=0.0, step=100.0, format="%.0f")
        lia_principal = fc4.number_input("Principal (₹)", min_value=0.0, step=1000.0, format="%.0f")
        lia_tenure = fc5.number_input("Tenure (months)", min_value=1, value=1 if preset.get("type") == "BNPL" else 12, step=1)

        fc6, fc7 = st.columns(2)
        lia_due_day = fc6.number_input("Due Day of Month", min_value=1, max_value=28, value=1, step=1)
        lia_rate = fc7.number_input("Interest Rate (%)", min_value=0.0, value=preset.get("interest_rate", 0.0), step=0.5, format="%.1f")

        lia_note = st.text_input("Note", value=preset.get("note", ""))

        if st.form_submit_button("➕ Add Liability", use_container_width=True):
            if lia_name and lia_emi > 0:
                database.add_liability(
                    lia_name, lia_type, lia_emi, lia_principal,
                    lia_rate, lia_tenure, lia_due_day, lia_note,
                )
                st.success(f"Added {lia_name}!")
                st.rerun()
            else:
                st.error("Name and EMI amount are required.")

    # Completed liabilities
    completed = database.list_liabilities(status="COMPLETED")
    if completed:
        with st.expander(f"Completed Liabilities ({len(completed)})"):
            for c in completed:
                st.markdown(f"✅ ~~{c['name']}~~ — {format_inr(c['emi_amount'])}/mo")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 4: SAVINGS GOALS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_save:
    st.subheader("Wishlist & Savings Targets")

    goals = database.list_savings_goals(status="ACTIVE")
    median_surplus = get_median_monthly_surplus(PAYDAY)
    total_savings_needed = total_monthly_savings_needed()

    if total_savings_needed > 0:
        sc1, sc2 = st.columns(2)
        sc1.metric("Monthly Savings Needed", format_inr(total_savings_needed))
        sc2.metric("Median Monthly Surplus", format_inr(median_surplus) if median_surplus > 0 else "No data yet")

        if total_savings_needed > median_surplus and median_surplus > 0:
            st.warning(
                f"⚠️ **High Feasibility Risk:** Savings quota ({format_inr(total_savings_needed)}/mo) "
                f"exceeds your median surplus ({format_inr(median_surplus)}/mo). Consider extending deadlines."
            )

    if goals:
        progress_list = [compute_goal_progress(g) for g in goals]
        checked_list = feasibility_check(progress_list, median_surplus)

        for gp in checked_list:
            with st.expander(
                f"{'🎯' if gp.is_feasible else '⚠️'} {gp.name} — "
                f"{gp.milestone} ({format_inr(gp.current)} / {format_inr(gp.target)})"
            ):
                # Progress bar
                st.progress(min(1.0, gp.pct / 100), text=f"{gp.pct}% complete")

                gc1, gc2, gc3 = st.columns(3)
                gc1.metric("Remaining", format_inr(gp.remaining))
                gc2.metric("Monthly Needed", format_inr(gp.monthly_needed))
                gc3.metric("Months Left", str(gp.months_left))

                if not gp.is_feasible:
                    st.warning("This goal may not be feasible with current surplus. Consider extending the deadline.")

                # Transfer funds
                transfer_amt = st.number_input(
                    f"Transfer to {gp.name} (₹)", min_value=0.0, step=100.0,
                    format="%.0f", key=f"transfer_{gp.goal_id}",
                )
                if st.button(f"💸 Transfer", key=f"btn_transfer_{gp.goal_id}"):
                    if transfer_amt > 0:
                        transfer_to_goal(gp.goal_id, transfer_amt)
                        st.success(f"{format_inr(transfer_amt)} transferred to {gp.name}!")
                        st.rerun()

                if st.button("🗑️ Delete Goal", key=f"del_goal_{gp.goal_id}"):
                    database.delete_savings_goal(gp.goal_id)
                    st.rerun()
    else:
        st.caption("No savings goals yet. Add one below to start tracking!")

    # Add new goal
    st.divider()
    st.subheader("Add New Goal")
    with st.form("add_goal_form", clear_on_submit=True):
        gc1, gc2 = st.columns(2)
        goal_name = gc1.text_input("Goal Name", placeholder="e.g., Headphones, Emergency Fund…")
        goal_target = gc2.number_input("Target Amount (₹)", min_value=100.0, step=500.0, format="%.0f")

        gc3, gc4 = st.columns(2)
        goal_deadline = gc3.date_input("Target Date", value=date(date.today().year, 12, 31))
        goal_priority = gc4.number_input("Priority", min_value=1, max_value=5, value=1, help="1=highest")

        if st.form_submit_button("🎯 Add Goal", use_container_width=True):
            if goal_name and goal_target > 0:
                database.add_savings_goal(goal_name, goal_target, goal_deadline.isoformat(), goal_priority)
                st.success(f"Goal '{goal_name}' added!")
                st.rerun()
            else:
                st.error("Name and target amount are required.")

    # Reached goals
    reached = database.list_savings_goals(status="REACHED")
    if reached:
        with st.expander(f"🏆 Reached Goals ({len(reached)})"):
            for r in reached:
                st.markdown(f"🏆 **{r['name']}** — {format_inr(r['target_amount'])} achieved!")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 5: ANALYTICS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_analytics:
    st.subheader("Historical Analysis & Insights")

    # Month-over-month delta
    st.markdown("#### Category Comparison (vs. Previous Cycle)")
    deltas = mom_category_delta(cycle, PAYDAY)
    if deltas:
        delta_display = [
            {
                "": d["direction"],
                "Category": d["category"],
                "This Cycle": format_inr(d["current"]),
                "Last Cycle": format_inr(d["previous"]),
                "Change": f"{d['delta_pct']:+.1f}%",
            }
            for d in deltas[:15]  # Top 15
        ]
        st.dataframe(delta_display, use_container_width=True, hide_index=True)

        # Surge alerts
        surges = category_surges(deltas)
        if surges:
            st.markdown("#### 🚨 Category Surges")
            for s in surges:
                st.markdown(f"- ⚠️ {s}")
    else:
        st.caption("Not enough data for comparison yet. Keep logging expenses!")

    st.divider()

    # Run-rate projection detail
    st.markdown("#### Run-Rate Projection")
    st.markdown(
        f"At your current pace of **{format_inr(projection['daily_rate'])}/day**, "
        f"you'll spend **{format_inr(projection['projected_total'])}** by end of cycle "
        f"(disposable budget: **{format_inr(metrics.true_disposable)}**)."
    )

    st.divider()

    # Contextual habit tips
    st.markdown("#### 💡 Smart Saving Tips")
    goal_list = database.list_savings_goals(status="ACTIVE")
    tips = generate_habit_tips(
        surges if deltas else [],
        goal_list,
        projection["daily_rate"],
    )
    for tip in tips:
        st.markdown(f"- 💡 {tip}")

    st.divider()

    # Cycle spending breakdown
    st.markdown("#### Spending by Category (This Cycle)")
    cycle_expenses = database.list_expenses_range(
        cycle.cycle_start.isoformat(), cycle.cycle_end.isoformat()
    )
    if cycle_expenses:
        from collections import defaultdict as dd
        cat_totals = dd(Decimal)
        for e in cycle_expenses:
            cat_totals[e["category"]] += money(e["amount"])
        sorted_cats = sorted(cat_totals.items(), key=lambda x: x[1], reverse=True)
        cat_display = [
            {"Category": cat, "Amount": format_inr(amt), "% of Total": f"{float(amt / spent_this_cycle * 100):.1f}%"}
            for cat, amt in sorted_cats
        ] if spent_this_cycle > 0 else []
        if cat_display:
            st.dataframe(cat_display, use_container_width=True, hide_index=True)
    else:
        st.caption("No expenses this cycle yet.")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 6: PLAN & GUIDANCE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_plan:
    can_generate = metrics.feasible and metrics.usable_pool > 0

    if "advice" not in st.session_state:
        st.session_state.advice = STATIC_GUIDE
        st.session_state.advice_source = "static"

    if st.button(
        "Generate Financial Guidance", type="primary",
        disabled=not can_generate,
        help=None if can_generate else "A positive usable budget is required",
    ):
        with st.spinner("Analyzing your budget and preparing personalized guidance..."):
            try:
                # Build extended context
                cycle_ctx = f"Day {cycle.day_index}/{cycle.total_days}, {cycle.days_remaining} days left"
                lia_ctx = f"Pending EMIs: {format_inr(pending_emis)}" if pending_emis > 0 else None
                savings_ctx = f"Monthly savings needed: {format_inr(total_savings_needed)}" if total_savings_needed > 0 else None
                impulse_ctx = None
                if today_expenses:
                    imp = daily_impulse_summary(today_expenses, metrics.safe_daily_limit)
                    if imp["impulse_count"] > 0:
                        impulse_ctx = f"{imp['impulse_count']} impulse spends today ({imp['impulse_percentage']}%)"

                advice, _ = generate_advice(
                    metrics, pantry, lifestyle, location,
                    cycle_context=cycle_ctx,
                    liabilities_context=lia_ctx,
                    savings_context=savings_ctx,
                    impulse_context=impulse_ctx,
                )
                st.session_state.advice = advice
                st.session_state.advice_source = "custom"
            except LLMError:
                st.session_state.advice = STATIC_GUIDE
                st.session_state.advice_source = "fallback"

    if st.session_state.advice_source == "custom":
        st.success("Personalized survival guidance updated.")
    elif st.session_state.advice_source == "fallback":
        st.info("Displaying standard survival framework based on your numbers.")

    advice = st.session_state.advice
    st.subheader("Practical Guidance")
    st.write(advice.get("summary", STATIC_GUIDE["summary"]))

    sections = [
        ("🍳 Meal Framework", "meal_plan"),
        ("🏪 Pantry Optimization", "pantry_tips"),
        ("📏 Spending Limits", "spending_limits"),
        ("🆘 Emergency Trade-offs", "emergency_tradeoffs"),
    ]
    for title, key in sections:
        with st.expander(title, expanded=key == "meal_plan"):
            items = advice.get(key, [])
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        st.markdown(f"**{item.get('meal', 'Idea')}:** {item.get('idea', '')}")
                    else:
                        st.markdown(f"- {item}")
            else:
                st.write(items)

    if st.button("💾 Save Current Plan", disabled=not can_generate):
        plan_id = database.save_plan(plan_name, metrics, json.dumps(advice, ensure_ascii=False))
        st.success(f"Plan saved locally (#{plan_id}).")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 7: HISTORY
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_history:
    hist_tab_plans, hist_tab_expenses, hist_tab_inflows = st.tabs(["Saved Plans", "All Expenses", "Inflow History"])

    with hist_tab_plans:
        plans = database.list_plans()
        if not plans:
            st.caption("No saved plans yet.")
        else:
            for plan in plans:
                with st.expander(
                    f"{plan['name']} · {plan['days']} days · {format_inr(plan['daily_allowance'])}/day"
                ):
                    st.caption(
                        f"Saved {plan['created_at']} · Total {format_inr(plan['total_money'])} "
                        f"· Bills {format_inr(plan['fixed_expenses'])} · Reserve {format_inr(plan['reserve'])}"
                    )
                    try:
                        st.json(json.loads(plan["advice_json"]))
                    except json.JSONDecodeError:
                        st.text(plan["advice_json"])
                    if st.button("Delete plan", key=f"delete_plan_{plan['id']}"):
                        database.delete_plan(plan["id"])
                        st.rerun()

    with hist_tab_expenses:
        all_expenses = database.list_expenses_range(
            cycle.cycle_start.isoformat(), cycle.cycle_end.isoformat()
        )
        if all_expenses:
            exp_display = [
                {
                    "Date": e["spent_on"],
                    "Category": e["category"],
                    "Mode": e.get("payment_mode", "UPI"),
                    "Planned": "✅" if e.get("is_planned", 1) else "⚡",
                    "Note": e["note"] or "",
                    "Amount": format_inr(e["amount"]),
                }
                for e in all_expenses
            ]
            st.dataframe(exp_display, use_container_width=True, hide_index=True)
            st.caption(f"Showing {len(all_expenses)} expenses this cycle ({cycle.cycle_start} to {cycle.cycle_end})")
        else:
            st.caption("No expenses this cycle.")

    with hist_tab_inflows:
        inflows = database.list_inflows(limit=20)
        if inflows:
            inflow_display = [
                {
                    "Date": i["date"],
                    "Type": INFLOW_LABELS.get(i["type"], i["type"]),
                    "Amount": format_inr(i["amount"]),
                    "Note": i.get("note", ""),
                }
                for i in inflows
            ]
            st.dataframe(inflow_display, use_container_width=True, hide_index=True)
        else:
            st.caption("No inflows logged yet.")

# ─── Footer ──────────────────────────────────────────────────────────────────

st.divider()
st.caption(
    "🔒 100% Private & Offline: All financial records stay securely on your local device. "
    "No external APIs or data tracking."
)
