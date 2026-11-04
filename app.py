"""Survival Budget / MonthEnd — Ultra-modern Indian personal finance dashboard.
Designed with high-end FinTech aesthetics (Tailwind-inspired tokens, glassmorphism,
tactile controls, and dark/light adaptive theming).
"""
import json
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

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

# ─── Page Configuration ────────────────────────────────────────────────────────

st.set_page_config(
    page_title="MonthEnd • Smart Financial Pacing",
    page_icon="📅",
    layout="wide",
    initial_sidebar_state="collapsed",
)

database.init_db()

# ─── Load Settings ────────────────────────────────────────────────────────────

settings = database.get_settings()
CURRENCY = settings.get("currency", "₹")
PAYDAY = settings.get("payday_day_of_month", 1)
INFLOW_TYPE = settings.get("inflow_type", "SALARY")
EXPECTED_AMOUNT = fmoney(settings.get("expected_amount", 0))

# ─── Cycle Computation ───────────────────────────────────────────────────────

cycle = get_current_cycle(PAYDAY)

# ─── Theme State & Global CSS Injection ───────────────────────────────────────

if "theme" not in st.session_state:
    st.session_state["theme"] = "dark"

is_light = (st.session_state["theme"] == "light")

# Ultra-polished CSS styling for both Light and Dark themes
COMMON_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important;
}

#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}

/* Sleek Button Styles */
div.stButton > button:first-child {
    border-radius: 10px;
    font-weight: 600;
    font-size: 0.95rem;
    padding: 0.55rem 1.25rem;
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    border: 1px solid transparent;
}
div.stButton > button:first-child:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}
div.stButton > button:first-child:active {
    transform: translateY(0);
}

/* Tab Bar Modernization */
.stTabs [data-baseweb="tab-list"] {
    gap: 8px;
    padding: 6px;
    border-radius: 12px;
}
.stTabs [data-baseweb="tab"] {
    border-radius: 8px;
    padding: 8px 18px;
    font-weight: 600;
    font-size: 0.9rem;
    transition: all 0.15s ease-in-out;
}
.stTabs [data-baseweb="tab-border"] {
    display: none;
}

/* Card Elevations */
[data-testid="stExpander"] {
    border-radius: 12px !important;
    overflow: hidden;
    margin-bottom: 12px;
}
.streamlit-expanderHeader {
    font-weight: 600 !important;
    font-size: 0.95rem !important;
}

/* Metric Container Cards */
[data-testid="metric-container"] {
    border-radius: 14px;
    padding: 16px 20px;
    transition: transform 0.2s ease, box-shadow 0.2s ease;
}
[data-testid="metric-container"]:hover {
    transform: translateY(-2px);
}
[data-testid="stMetricValue"] {
    font-family: 'JetBrains Mono', monospace !important;
    font-weight: 700 !important;
    font-size: 1.65rem !important;
}
[data-testid="stMetricLabel"] {
    font-size: 0.85rem !important;
    font-weight: 600 !important;
    text-transform: uppercase;
    letter-spacing: 0.04em;
}

/* Inputs & Form Elements */
input, textarea, [data-baseweb="select"] {
    border-radius: 8px !important;
    font-size: 0.95rem !important;
}

/* Progress Bars */
.stProgress > div > div > div > div {
    border-radius: 9999px;
}
</style>
"""

LIGHT_THEME_CSS = """
<style>
/* Light Mode Theme */
[data-testid="stAppViewContainer"] {
    background-color: #f8fafc;
    color: #0f172a;
}
[data-testid="stHeader"] {
    background-color: rgba(248, 250, 252, 0.8);
    backdrop-filter: blur(8px);
}
[data-testid="stSidebar"] {
    background-color: #ffffff;
    border-right: 1px solid #e2e8f0;
}
h1, h2, h3, h4, h5, h6 {
    color: #0f172a !important;
    font-weight: 700 !important;
}
p, span, label, .stMarkdown {
    color: #334155;
}
.stCaption, [data-testid="stCaptionContainer"] {
    color: #64748b !important;
}
/* Metrics in Light Mode */
[data-testid="metric-container"] {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05), 0 4px 6px -1px rgba(0, 0, 0, 0.02);
}
[data-testid="stMetricLabel"] {
    color: #64748b !important;
}
[data-testid="stMetricValue"] {
    color: #0f172a !important;
}
/* Tabs in Light Mode */
.stTabs [data-baseweb="tab-list"] {
    background-color: #f1f5f9;
    border: 1px solid #e2e8f0;
}
.stTabs [data-baseweb="tab"] {
    color: #64748b;
}
.stTabs [aria-selected="true"] {
    background-color: #ffffff !important;
    color: #0284c7 !important;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}
/* Form Controls in Light Mode */
div[data-baseweb="select"] > div {
    background-color: #ffffff !important;
    border-color: #cbd5e1 !important;
    color: #0f172a !important;
}
input, textarea {
    background-color: #ffffff !important;
    color: #0f172a !important;
    border: 1px solid #cbd5e1 !important;
}
[data-testid="stExpander"] {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
}
.streamlit-expanderHeader {
    background-color: #ffffff !important;
    color: #0f172a !important;
}
</style>
"""

DARK_THEME_CSS = """
<style>
/* Dark Mode Theme */
[data-testid="stAppViewContainer"] {
    background-color: #0b0f19;
    color: #f8fafc;
}
[data-testid="stHeader"] {
    background-color: rgba(11, 15, 25, 0.8);
    backdrop-filter: blur(8px);
}
[data-testid="stSidebar"] {
    background-color: #111827;
    border-right: 1px solid #1f293d;
}
h1, h2, h3, h4, h5, h6 {
    color: #f8fafc !important;
    font-weight: 700 !important;
}
p, span, label, .stMarkdown {
    color: #cbd5e1;
}
.stCaption, [data-testid="stCaptionContainer"] {
    color: #94a3b8 !important;
}
/* Metrics in Dark Mode */
[data-testid="metric-container"] {
    background: linear-gradient(145deg, #131b2e, #0f172a);
    border: 1px solid #1f293d;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
}
[data-testid="stMetricLabel"] {
    color: #94a3b8 !important;
}
[data-testid="stMetricValue"] {
    color: #f8fafc !important;
}
/* Tabs in Dark Mode */
.stTabs [data-baseweb="tab-list"] {
    background-color: #111827;
    border: 1px solid #1f293d;
}
.stTabs [data-baseweb="tab"] {
    color: #94a3b8;
}
.stTabs [aria-selected="true"] {
    background-color: #1e293b !important;
    color: #38bdf8 !important;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
}
/* Form Controls in Dark Mode */
div[data-baseweb="select"] > div {
    background-color: #111827 !important;
    border-color: #1f293d !important;
    color: #f8fafc !important;
}
input, textarea {
    background-color: #111827 !important;
    color: #f8fafc !important;
    border: 1px solid #1f293d !important;
}
[data-testid="stExpander"] {
    background-color: #111827;
    border: 1px solid #1f293d;
}
.streamlit-expanderHeader {
    background-color: #111827 !important;
    color: #f8fafc !important;
}
</style>
"""

st.markdown(COMMON_CSS, unsafe_allow_html=True)
if is_light:
    st.markdown(LIGHT_THEME_CSS, unsafe_allow_html=True)
else:
    st.markdown(DARK_THEME_CSS, unsafe_allow_html=True)

# ─── Navigation & Executive Header ───────────────────────────────────────────

header_col1, header_col2 = st.columns([5, 1.4])

with header_col1:
    title_badge_html = f"""
    <div style="display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-bottom: 4px;">
        <span style="font-size: 2.1rem; font-weight: 800; letter-spacing: -0.02em; 
                     background: linear-gradient(135deg, {'#0284c7, #2563eb' if is_light else '#38bdf8, #818cf8'});
                     -webkit-background-clip: text; -webkit-text-fill-color: transparent;">
            MonthEnd
        </span>
        <span style="font-size: 0.72rem; font-weight: 700; padding: 3px 10px; border-radius: 9999px;
                     background: {'#e0f2fe' if is_light else '#1e293b'}; 
                     color: {'#0369a1' if is_light else '#38bdf8'}; 
                     border: 1px solid {'#bae6fd' if is_light else '#334155'};">
            MONTHEND BUDGET
        </span>
    </div>
    <div style="font-size: 0.95rem; color: {'#64748b' if is_light else '#94a3b8'}; margin-bottom: 12px;">
        Intelligent financial pacing & anti-impulse defense for the Indian everyday economy.
    </div>
    """
    st.markdown(title_badge_html, unsafe_allow_html=True)

with header_col2:
    st.write("")  # Spacing
    theme_toggle_val = st.toggle(
        "☀️ Light Mode",
        value=(st.session_state["theme"] == "light"),
        key="global_theme_toggle",
    )
    if theme_toggle_val != is_light:
        st.session_state["theme"] = "light" if theme_toggle_val else "dark"
        st.rerun()

# ─── Top Inflow Status Banner ─────────────────────────────────────────────────

chip_text = cycle_status_text(cycle, INFLOW_TYPE)
cycle_progress_pct = min(100, int((cycle.day_index / max(1, cycle.total_days)) * 100))

status_banner_html = f"""
<div style="display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px;
            background: {'linear-gradient(135deg, #ffffff, #f1f5f9)' if is_light else 'linear-gradient(135deg, #111827, #1e293b)'};
            border: 1px solid {'#e2e8f0' if is_light else '#1f293d'};
            padding: 12px 20px; border-radius: 14px; margin-bottom: 20px;
            box-shadow: 0 2px 6px rgba(0, 0, 0, {'0.03' if is_light else '0.2'});">
    <div style="display: flex; align-items: center; gap: 14px;">
        <div style="width: 42px; height: 42px; border-radius: 10px; display: flex; align-items: center; justify-content: center;
                    background: {'#e0f2fe' if is_light else 'rgba(56, 189, 248, 0.15)'}; font-size: 1.25rem;">
            📅
        </div>
        <div>
            <div style="font-weight: 700; font-size: 0.95rem; color: {'#0f172a' if is_light else '#f8fafc'};">
                {chip_text}
            </div>
            <div style="font-size: 0.8rem; color: {'#64748b' if is_light else '#94a3b8'};">
                Billing Cycle: {cycle.cycle_start.strftime('%b %d')} &rarr; {cycle.cycle_end.strftime('%b %d, %Y')} ({cycle.total_days} days total)
            </div>
        </div>
    </div>
    <div style="display: flex; align-items: center; gap: 12px; min-width: 220px;">
        <div style="flex: 1;">
            <div style="display: flex; justify-content: space-between; font-size: 0.75rem; font-weight: 600; margin-bottom: 4px; color: {'#475569' if is_light else '#94a3b8'};">
                <span>Cycle Elapsed</span>
                <span>{cycle_progress_pct}%</span>
            </div>
            <div style="width: 100%; height: 6px; background: {'#e2e8f0' if is_light else '#334155'}; border-radius: 9999px; overflow: hidden;">
                <div style="width: {cycle_progress_pct}%; height: 100%; background: {'#0284c7' if is_light else '#38bdf8'}; border-radius: 9999px;"></div>
            </div>
        </div>
        <span style="font-size: 0.8rem; font-weight: 700; padding: 4px 10px; border-radius: 8px;
                     background: {'#f0fdf4' if is_light else 'rgba(16, 185, 129, 0.15)'}; 
                     color: {'#16a34a' if is_light else '#34d399'}; 
                     border: 1px solid {'#bbf7d0' if is_light else 'rgba(16, 185, 129, 0.3)'};">
            {cycle.days_remaining}D LEFT
        </span>
    </div>
</div>
"""
st.markdown(status_banner_html, unsafe_allow_html=True)

# Overdue inflow alert
if check_inflow_overdue(PAYDAY, EXPECTED_AMOUNT):
    st.error(
        f"⚠️ **Inflow Overdue**: Expected {INFLOW_LABELS.get(INFLOW_TYPE, 'Income')} on day {PAYDAY} has not been logged yet. "
        "Log it in the sidebar to maintain accurate surplus and pacing calculations."
    )

# ─── Sidebar Settings & Inflow Center ────────────────────────────────────────

with st.sidebar:
    st.markdown("### ⚙️ Command Center")

    with st.expander("📅 Inflow & Payday Cycle", expanded=False):
        new_payday = st.number_input(
            "Payday / Anchor Day", min_value=1, max_value=28,
            value=PAYDAY, step=1, help="Day of the month when your main income arrives"
        )
        new_inflow_type = st.selectbox(
            "Income Stream Type", INFLOW_TYPES,
            index=INFLOW_TYPES.index(INFLOW_TYPE) if INFLOW_TYPE in INFLOW_TYPES else 0,
            format_func=lambda x: INFLOW_LABELS.get(x, x),
        )
        new_expected = st.number_input(
            "Expected Monthly Income (₹)", min_value=0.0,
            value=float(EXPECTED_AMOUNT), step=500.0, format="%.0f",
        )
        if st.button("Save Cycle Settings", use_container_width=True):
            database.save_settings(new_payday, new_inflow_type, new_expected)
            st.success("Cycle settings updated!")
            st.rerun()

    st.markdown("---")
    st.markdown("### 💰 Financial Runway")
    total = st.number_input(
        "Available Cash / Balance (₹)", min_value=0.0,
        value=float(EXPECTED_AMOUNT or 5000.0), step=500.0, format="%.0f",
        help="All liquid funds currently at your disposal"
    )
    fixed = st.number_input(
        "Fixed Living Bills (₹)", min_value=0.0, value=0.0, step=100.0, format="%.0f",
        help="Mandatory living costs (Rent, PG fee, Mess fee, WiFi)"
    )
    reserve = st.number_input(
        "Protected Emergency Reserve (₹)", min_value=0.0, value=0.0, step=100.0, format="%.0f",
        help="Untouchable safety cushion for medical or emergency needs"
    )

    st.markdown("---")
    with st.expander("📝 Lifestyle & Context", expanded=False):
        plan_name = st.text_input("Plan Name", value="Monthly Survival Plan")
        pantry = st.text_area("Pantry Staples on Hand", placeholder="Rice, dal, atta, oil, tea, spices…")
        lifestyle = st.text_input("Living Constraints", placeholder="Student PG, shared fridge, no vehicle…")
        location = st.text_input("City / Region", placeholder="Bangalore, Mumbai, Delhi, Pune…")

    with st.expander("📥 Log Incoming Inflow", expanded=False):
        inflow_amt = st.number_input("Inflow Amount (₹)", min_value=0.0, step=500.0, format="%.0f", key="inflow_amt")
        inflow_note = st.text_input("Source / Reference", key="inflow_note", placeholder="Salary, Dad allowance, Freelance…")
        if st.button("Record Inflow", key="log_inflow", use_container_width=True):
            if inflow_amt > 0:
                database.add_inflow(
                    inflow_amt, INFLOW_TYPE, date.today().isoformat(),
                    cycle.cycle_start.isoformat(), cycle.cycle_end.isoformat(), inflow_note,
                )
                st.success(f"Inflow of {format_inr(inflow_amt)} recorded!")
                st.rerun()

    with st.expander("🗑️ Reset & Data Hygiene", expanded=False):
        st.caption("Erase all logged records (expenses, plans, inflows, liabilities, and goals) from this device.")
        if st.button("Wipe All Local Data", type="secondary", use_container_width=True):
            database.clear_all_data()
            st.session_state.clear()
            st.cache_data.clear()
            st.success("All local data wiped cleanly!")
            st.rerun()

# ─── Core Deterministic Budget Engine ─────────────────────────────────────────

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
    st.error(f"Computation Error: {exc}")
    st.stop()

if money(total) - money(fixed) <= 0:
    st.error("🚨 **Deficit Alert**: Fixed living obligations exceed or equal available capital. Prioritize immediate rent/mess bills before discretionary spends.")

# ─── KPI Dashboard Cards ──────────────────────────────────────────────────────

kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)
kpi1.metric("Total Liquid", format_inr(metrics.total_money))
kpi2.metric("After Bills", format_inr(max(Decimal("0"), metrics.total_money - metrics.fixed_expenses)))
kpi3.metric(
    "True Disposable", format_inr(metrics.true_disposable),
    help="Actual spendable cash after deducting fixed bills, protected emergency reserve, and pending BNPL/EMIs."
)
kpi4.metric(
    "Safe Daily Limit", format_inr(metrics.safe_daily_limit),
    f"{cycle.days_remaining} days left in cycle",
    help="Maximum spend allowable today to guarantee runway survival."
)
kpi5.metric(
    "Spent This Cycle", format_inr(spent_this_cycle),
    help="Cumulative outflow recorded from start of active cycle."
)

if metrics.critical and metrics.usable_pool > 0:
    st.error("🚨 **Extreme Austerity Protocol**: Safe daily ceiling is under ₹100/day. Rely strictly on hostel mess/tiffin, bulk dal-rice cooking, and suspend all non-essential outflows.")

# ─── Workspace Tabs ───────────────────────────────────────────────────────────

tab_dash, tab_log, tab_lia, tab_save, tab_analytics, tab_plan, tab_history = st.tabs([
    "📊 Dashboard",
    "➕ Log Expense",
    "🏦 Liabilities & EMI",
    "🎯 Savings Goals",
    "📈 Analytics",
    "📋 Budget Guidance",
    "📜 History",
])

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 1: COMMAND DASHBOARD
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_dash:
    dash_col_left, dash_col_right = st.columns([1.5, 1])

    with dash_col_left:
        st.markdown("#### ⚡ Run-Rate & Burn Velocity")
        projection = run_rate_projection(spent_this_cycle, cycle.day_index, cycle.total_days)
        on_track = projection["projected_total"] <= metrics.true_disposable

        rr_col1, rr_col2, rr_col3 = st.columns(3)
        rr_col1.metric("Current Burn Rate", f"{format_inr(projection['daily_rate'])} / day")
        rr_col2.metric("Projected Total Outflow", format_inr(projection["projected_total"]))
        rr_col3.metric(
            "Pace Health",
            "✅ On Track" if on_track else "⚠️ Over-Pace",
            delta=f"{format_inr(metrics.true_disposable - projection['projected_total'])} headroom"
            if on_track else f"{format_inr(projection['projected_total'] - metrics.true_disposable)} over budget",
            delta_color="normal" if on_track else "inverse",
        )

        # Spending Persona Banner
        persona = classify_spending_persona(PAYDAY)
        persona_card_html = f"""
        <div style="display: flex; align-items: center; gap: 16px; padding: 14px 20px; border-radius: 12px; margin-top: 14px;
                    background: {'#ffffff' if is_light else '#111827'};
                    border: 1px solid {'#e2e8f0' if is_light else '#1f293d'};
                    box-shadow: 0 1px 3px rgba(0,0,0,{'0.04' if is_light else '0.2'});">
            <div style="font-size: 2rem;">{persona["icon"]}</div>
            <div>
                <div style="font-weight: 700; font-size: 1rem; color: {'#0f172a' if is_light else '#f8fafc'};">
                    {persona["persona"]}
                </div>
                <div style="font-size: 0.85rem; color: {'#64748b' if is_light else '#94a3b8'};">
                    {persona["reason"]}
                </div>
            </div>
        </div>
        """
        st.markdown(persona_card_html, unsafe_allow_html=True)

    with dash_col_right:
        st.markdown("#### 📅 Today's Survival Pulse")
        today_str = date.today().isoformat()
        today_expenses = database.list_expenses(spent_on=today_str)
        today_spent = sum((money(e["amount"]) for e in today_expenses), Decimal("0.00"))
        status = derive_daily_status(metrics, today_spent)

        today_col1, today_col2 = st.columns(2)
        today_col1.metric("Spent Today", format_inr(status["spent"]))
        today_col2.metric(
            "Safe Ceiling Left",
            format_inr(status["remaining"]),
            delta="Within pace" if not status["over_budget"] else "Over daily ceiling",
            delta_color="normal" if not status["over_budget"] else "inverse",
        )

        if status["over_budget"]:
            st.warning("⚠️ **Today's ceiling exceeded.** Carry no further discretionary spends until tomorrow to safeguard cycle runway.")

        if today_expenses:
            imp_summary = daily_impulse_summary(today_expenses, metrics.safe_daily_limit)
            if imp_summary["impulse_count"] > 0:
                impulse_box_html = f"""
                <div style="background: {'#fef3c7' if is_light else 'rgba(146, 64, 14, 0.25)'};
                            border: 1px solid {'#fde68a' if is_light else '#92400e'};
                            color: {'#92400e' if is_light else '#fbbf24'};
                            padding: 10px 14px; border-radius: 10px; margin-top: 10px; font-size: 0.85rem;">
                    ⚡ <strong>{imp_summary["impulse_count"]} unplanned spend(s) today</strong> ({format_inr(imp_summary["impulse_total"])}) 
                    accounting for {imp_summary["impulse_percentage"]}% of today's total outflow.
                </div>
                """
                st.markdown(impulse_box_html, unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("#### 🏦 Pending EMIs & Liabilities This Cycle")
    pending_list = get_pending_emis_this_cycle(cycle.cycle_start.isoformat(), cycle.cycle_end.isoformat())
    if pending_list:
        emi_cols = st.columns(min(3, len(pending_list)))
        for idx, lia in enumerate(pending_list):
            with emi_cols[idx % 3]:
                status_icon = {"PAID": "🟢", "UPCOMING": "🔵", "DUE": "🟡", "OVERDUE": "🔴"}.get(lia["emi_status"], "⚪")
                st.markdown(
                    f"""
                    <div style="background: {'#ffffff' if is_light else '#111827'}; border: 1px solid {'#e2e8f0' if is_light else '#1f293d'};
                                border-radius: 12px; padding: 14px; margin-bottom: 10px;">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                            <span style="font-weight: 700; font-size: 0.95rem;">{lia['name']}</span>
                            <span style="font-size: 0.75rem; font-weight: 700; padding: 2px 8px; border-radius: 6px;
                                         background: {'#f1f5f9' if is_light else '#1e293b'}; color: {'#475569' if is_light else '#94a3b8'};">
                                {status_icon} {lia['emi_status']}
                            </span>
                        </div>
                        <div style="font-family: 'JetBrains Mono', monospace; font-size: 1.25rem; font-weight: 700; color: {'#0284c7' if is_light else '#38bdf8'}; margin-bottom: 4px;">
                     
                            {format_inr(lia['emi_amount'])}
                        </div>
                        <div style="font-size: 0.8rem; color: {'#64748b' if is_light else '#94a3b8'};">
                            Due Day {lia['due_day']} • {lia['tenure_remaining']} months left
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
    else:
        st.caption("No pending liabilities found for this cycle.")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 2: QUICK ENTRY LOG
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_log:
    # Prominent Safe Limit Banner
    safe_banner_html = f"""
    <div style="background: {'linear-gradient(135deg, #eff6ff, #dbeafe)' if is_light else 'linear-gradient(135deg, #0f172a, #1e293b)'};
                border: 1px solid {'#bfdbfe' if is_light else '#1e40af'};
                padding: 16px 24px; border-radius: 14px; text-align: center; margin-bottom: 20px;
                box-shadow: 0 4px 12px rgba(37, 99, 235, {'0.08' if is_light else '0.25'});">
        <div style="font-size: 0.8rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
                    color: {'#2563eb' if is_light else '#60a5fa'}; margin-bottom: 4px;">
            TODAY'S SAFE DISCRETIONARY CEILING
        </div>
        <div style="font-family: 'JetBrains Mono', monospace; font-size: 2.25rem; font-weight: 800;
                    color: {'#1e3a8a' if is_light else '#f8fafc'};">
            {format_inr(metrics.safe_daily_limit)}
        </div>
        <div style="font-size: 0.85rem; color: {'#475569' if is_light else '#94a3b8'}; margin-top: 4px;">
            Paced across <strong>{cycle.days_remaining} remaining days</strong> after protecting bills, reserve, and EMIs.
        </div>
    </div>
    """
    st.markdown(safe_banner_html, unsafe_allow_html=True)

    with st.form("quick_expense_form", clear_on_submit=True):
        st.markdown("##### 📝 Record Instant Transaction")

        col_amt, col_cat = st.columns([1, 1.5])
        with col_amt:
            amount = st.number_input("Amount (₹)", min_value=1.0, value=50.0, step=10.0, format="%.0f")

        with col_cat:
            all_cats_flat = []
            for group_name, items in CATEGORIES.items():
                for item in items:
                    all_cats_flat.append(item)
            category = st.selectbox("Category Preset", all_cats_flat) or "Other"

        col_mode, col_date = st.columns(2)
        with col_mode:
            payment_mode = st.selectbox("Payment Mode", PAYMENT_MODES)
        with col_date:
            spend_date = st.date_input("Transaction Date", value=date.today())

        col_plan, col_note = st.columns([1, 2])
        with col_plan:
            is_planned = st.toggle("Was this planned?", value=True, help="Uncheck if this was a spontaneous impulse buy")
        with col_note:
            note = st.text_input("Merchant / Memo", placeholder="e.g. Zepto midnight milk, Chai with friends…")

        submitted = st.form_submit_button("⚡ Save Expense", type="primary", use_container_width=True)

    if submitted:
        hour_now = datetime.now().hour
        impulse_result = check_impulse_flags(amount, category, metrics.safe_daily_limit, hour_now)

        if impulse_result["needs_cooldown"]:
            st.warning("🛑 **HIGH IMPULSE VELOCITY TRIGGERED** — Cooldown activated:")
            for flag in impulse_result["flags"]:
                st.markdown(f"  - ⚠️ {flag}")
            st.markdown("*5-second intentional cooling period in progress...*")

            if "cooldown_pending" not in st.session_state:
                st.session_state.cooldown_pending = {
                    "amount": amount, "category": category, "note": note,
                    "payment_mode": payment_mode, "is_planned": is_planned,
                    "spend_date": spend_date.isoformat(), "hour": hour_now,
                    "time": time.time(),
                }
        else:
            if impulse_result["is_impulse"]:
                for flag in impulse_result["flags"]:
                    st.info(f"💡 {flag}")

            database.add_expense(
                plan_id=None, amount=money(amount), category=category,
                note=note, spent_on=spend_date.isoformat(),
                payment_mode=payment_mode, is_planned=is_planned, hour_logged=hour_now,
            )
            st.success(f"Expense of {format_inr(amount)} logged under {category}!")
            st.rerun()

    # Handle cooldown review
    if "cooldown_pending" in st.session_state:
        pending = st.session_state.cooldown_pending
        elapsed = time.time() - pending["time"]
        if elapsed >= 5:
            c_conf, c_canc = st.columns(2)
            with c_conf:
                if st.button("✅ Confirm Anyway", type="primary", use_container_width=True):
                    database.add_expense(
                        plan_id=None, amount=money(pending["amount"]),
                        category=pending["category"], note=pending["note"],
                        spent_on=pending["spend_date"], payment_mode=pending["payment_mode"],
                        is_planned=pending["is_planned"], hour_logged=pending["hour"],
                    )
                    del st.session_state.cooldown_pending
                    st.success(f"Logged {format_inr(pending['amount'])} with impulse tag.")
                    st.rerun()
            with c_canc:
                if st.button("❌ Abort Purchase", use_container_width=True):
                    del st.session_state.cooldown_pending
                    st.info("Impulse purchase aborted. Wallet saved! 💪")
                    st.rerun()
        else:
            remaining_secs = int(5 - elapsed)
            st.info(f"⏳ Mindful pause: {remaining_secs}s remaining…")
            time.sleep(1)
            st.rerun()

    # Today's Transaction Ledger
    st.markdown("---")
    st.markdown("##### 📜 Today's Transactions")
    today_entries = database.list_expenses(spent_on=today_str)
    if today_entries:
        formatted_list = [
            {
                "Time": f"{e.get('hour_logged', 12)}:00",
                "Category": e["category"],
                "Payment": e.get("payment_mode", "UPI"),
                "Type": "✅ Planned" if e.get("is_planned", 1) else "⚡ Impulse",
                "Memo": e["note"] or "-",
                "Amount": format_inr(e["amount"]),
            }
            for e in today_entries
        ]
        st.dataframe(formatted_list, use_container_width=True, hide_index=True)

        del_id = st.selectbox(
            "Remove Incorrect Entry", [None] + [e["id"] for e in today_entries],
            format_func=lambda x: "Select entry to remove…" if x is None else f"Transaction #{x}",
        )
        if del_id is not None and st.button("Delete Transaction", type="secondary"):
            database.delete_expense(del_id)
            st.rerun()
    else:
        st.caption("No transactions recorded today yet.")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 3: LIABILITIES & BNPL MANAGER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_lia:
    st.markdown("#### 🏦 Liability & Credit Pacing Manager")
    emi_total = total_monthly_emi_burden()

    lia_stat1, lia_stat2 = st.columns([1, 2])
    lia_stat1.metric("Monthly EMI Burden", format_inr(emi_total))
    lia_stat2.caption(
        "Pending EMIs are automatically filtered out from your Available Balance to yield your **True Disposable Income**, "
        "preventing the illusion of phantom cash."
    )

    liabilities = database.list_liabilities(status="ACTIVE")
    if liabilities:
        st.markdown("##### Active Debt & BNPL Obligations")
        for lia in liabilities:
            with st.expander(f"{'⚡ BNPL: ' if lia['type'] == 'BNPL' else '🏦 EMI: '} {lia['name']} — {format_inr(lia['emi_amount'])}/mo"):
                l_c1, l_c2, l_c3 = st.columns(3)
                l_c1.markdown(f"**Facility Type:** `{lia['type']}`")
                l_c2.markdown(f"**Monthly Due Day:** `Day {lia['due_day']}`")
                l_c3.markdown(f"**Tenure Remaining:** `{lia['tenure_remaining']} months`")

                if lia.get("principal") and fmoney(lia["principal"]) > 0:
                    st.markdown(f"**Outstanding Principal:** {format_inr(lia['principal'])} • **Interest Rate:** {lia['interest_rate']}%")

                payments = database.list_liability_payments(lia["id"])
                paid_this_cycle = any(
                    cycle.cycle_start.isoformat() <= p["paid_date"] <= cycle.cycle_end.isoformat()
                    for p in payments
                )

                btn_col1, btn_col2 = st.columns([1.5, 1])
                with btn_col1:
                    if paid_this_cycle:
                        st.success("✅ Fully paid for current cycle")
                    else:
                        if st.button(f"💸 Mark as Paid ({format_inr(lia['emi_amount'])})", key=f"pay_lia_{lia['id']}", type="primary"):
                            pay_emi(lia["id"])
                            st.success(f"Payment logged and deducted for {lia['name']}!")
                            st.rerun()
                with btn_col2:
                    if st.button("🗑️ Delete Facility", key=f"del_lia_{lia['id']}"):
                        database.delete_liability(lia["id"])
                        st.rerun()
    else:
        st.caption("No active liabilities recorded. Great job keeping debt-free!")

    # Add Liability
    st.markdown("---")
    st.markdown("##### ➕ Register New Debt or Pay-Later Facility")

    preset_choice = st.selectbox("Instant Indian Credit Presets", ["Custom"] + list(BNPL_PRESETS.keys()))
    with st.form("new_liability_form", clear_on_submit=True):
        preset_data = BNPL_PRESETS.get(preset_choice, {})
        preset_type = preset_data.get("type", "EMI")
        preset_rate = preset_data.get("interest_rate", 0.0)
        preset_note = preset_data.get("note", "")

        nl_c1, nl_c2 = st.columns(2)
        nl_name = nl_c1.text_input("Obligation Name", value=preset_choice if preset_choice != "Custom" else "")
        nl_type = nl_c2.selectbox(
            "Obligation Type", LIABILITY_TYPES,
            index=LIABILITY_TYPES.index(preset_type) if preset_type in LIABILITY_TYPES else 0
        )

        nl_c3, nl_c4, nl_c5 = st.columns(3)
        nl_emi = nl_c3.number_input("Monthly Payment (₹)", min_value=0.0, step=100.0, format="%.0f")
        nl_principal = nl_c4.number_input("Total Principal (₹)", min_value=0.0, step=1000.0, format="%.0f")
        nl_tenure = nl_c5.number_input("Tenure (Months)", min_value=1, value=1 if preset_type == "BNPL" else 12, step=1)

        nl_c6, nl_c7 = st.columns(2)
        nl_due = nl_c6.number_input("Due Day of Month", min_value=1, max_value=28, value=1, step=1)
        nl_rate = nl_c7.number_input("Interest Rate (%)", min_value=0.0, value=preset_rate, step=0.5, format="%.1f")

        nl_note = st.text_input("Memo / Account Ref", value=preset_note)

        if st.form_submit_button("➕ Register Facility", type="primary", use_container_width=True):
            if nl_name and nl_emi > 0:
                database.add_liability(nl_name, nl_type or "EMI", nl_emi, nl_principal, nl_rate, nl_tenure, nl_due, nl_note)
                st.success(f"Facility '{nl_name}' registered successfully!")
                st.rerun()
            else:
                st.error("Facility name and monthly installment amount are mandatory.")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 4: SAVINGS GOALS & WISHLIST
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_save:
    st.markdown("#### 🎯 Wishlist & Time-Bound Savings Targets")

    goals = database.list_savings_goals(status="ACTIVE")
    median_surplus = get_median_monthly_surplus(PAYDAY)
    total_savings_needed = total_monthly_savings_needed()

    if total_savings_needed > 0:
        sg_m1, sg_m2 = st.columns(2)
        sg_m1.metric("Required Monthly Allocation", format_inr(total_savings_needed))
        sg_m2.metric(
            "Historical Median Monthly Surplus",
            format_inr(median_surplus) if median_surplus > 0 else "Insufficient Data",
            help="Computed as Inflows minus Outflows over recent cycles."
        )

        if total_savings_needed > median_surplus and median_surplus > 0:
            st.warning(
                f"⚠️ **High Feasibility Risk**: Your combined savings target ({format_inr(total_savings_needed)}/mo) "
                f"exceeds your median cycle surplus ({format_inr(median_surplus)}/mo). Consider extending deadlines."
            )

    if goals:
        progress_list = [compute_goal_progress(g) for g in goals]
        checked_list = feasibility_check(progress_list, median_surplus)

        for gp in checked_list:
            feasibility_badge = "✅ Feasible" if gp.is_feasible else "⚠️ High Risk"
            with st.expander(f"🎯 {gp.name} — {format_inr(gp.current)} of {format_inr(gp.target)} ({gp.milestone}) • {feasibility_badge}"):
                st.progress(min(1.0, gp.pct / 100), text=f"{gp.pct}% achieved")

                g_c1, g_c2, g_c3 = st.columns(3)
                g_c1.metric("Remaining Needed", format_inr(gp.remaining))
                g_c2.metric("Monthly Quota", format_inr(gp.monthly_needed))
                g_c3.metric("Months Remaining", gp.months_left)

                t_c1, t_c2 = st.columns([2, 1])
                with t_c1:
                    transfer_amt = st.number_input(
                        f"Allocate Funds to {gp.name} (₹)", min_value=0.0, step=100.0,
                        format="%.0f", key=f"alloc_{gp.goal_id}"
                    )
                with t_c2:
                    st.write("")  # Alignment
                    if st.button("💸 Transfer", key=f"btn_transfer_{gp.goal_id}", type="primary"):
                        if transfer_amt > 0:
                            transfer_to_goal(gp.goal_id, transfer_amt)
                            st.success(f"Allocated {format_inr(transfer_amt)} to {gp.name}!")
                            st.rerun()

                if st.button("🗑️ Delete Goal", key=f"del_goal_{gp.goal_id}"):
                    database.delete_savings_goal(gp.goal_id)
                    st.rerun()
    else:
        st.caption("No savings targets registered yet. Add a target below!")

    # Add Goal
    st.markdown("---")
    st.markdown("##### ➕ Create New Target / Wishlist Goal")
    with st.form("add_goal_form", clear_on_submit=True):
        ag_c1, ag_c2 = st.columns(2)
        g_name = ag_c1.text_input("Goal Name", placeholder="e.g. Sony XM5, Emergency 10k, Flight Tickets…")
        g_target = ag_c2.number_input("Target Amount (₹)", min_value=100.0, step=500.0, format="%.0f")

        ag_c3, ag_c4 = st.columns(2)
        g_deadline = ag_c3.date_input("Target Date", value=date(date.today().year, 12, 31))
        g_priority = ag_c4.number_input("Priority Level (1=Highest)", min_value=1, max_value=5, value=1)

        if st.form_submit_button("🎯 Save Savings Goal", type="primary", use_container_width=True):
            if g_name and g_target > 0:
                database.add_savings_goal(g_name, g_target, g_deadline.isoformat(), g_priority)
                st.success(f"Goal '{g_name}' saved!")
                st.rerun()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 5: PREDICTIVE ANALYTICS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_analytics:
    st.markdown("#### 📈 Historical Delta & Predictive Intelligence")

    deltas = mom_category_delta(cycle, PAYDAY)
    surges: list[str] = []

    if deltas:
        st.markdown("##### Category Outflow Delta (vs Previous Cycle Same Day-Index)")
        delta_table = [
            {
                "Direction": d["direction"],
                "Category": d["category"],
                "Current Cycle": format_inr(d["current"]),
                "Previous Cycle": format_inr(d["previous"]),
                "Variance": f"{d['delta_pct']:+.1f}%",
            }
            for d in deltas[:15]
        ]
        st.dataframe(delta_table, use_container_width=True, hide_index=True)

        surges = category_surges(deltas)
        if surges:
            st.markdown("##### 🚨 Active Category Surge Warnings")
            for s in surges:
                st.warning(f"⚠️ {s}")
    else:
        st.caption("Insufficient historical data for cycle comparison. Keep logging transactions.")

    st.markdown("---")
    st.markdown("##### 💡 Contextual Habit Tips Linked to Goals")
    goal_list = database.list_savings_goals(status="ACTIVE")
    tips = generate_habit_tips(surges, goal_list, projection["daily_rate"])
    for tip in tips:
        st.info(f"💡 {tip}")

    st.markdown("---")
    st.markdown("##### 📊 Active Cycle Category Breakdown")
    cycle_expenses = database.list_expenses_range(cycle.cycle_start.isoformat(), cycle.cycle_end.isoformat())
    if cycle_expenses:
        from collections import defaultdict as dd
        cat_totals: dict[str, Decimal] = dd(Decimal)
        for e in cycle_expenses:
            cat_totals[e["category"]] += money(e["amount"])
        sorted_cats = sorted(cat_totals.items(), key=lambda x: x[1], reverse=True)
        cat_display = [
            {
                "Category": cat,
                "Total Spent": format_inr(amt),
                "Share of Cycle": f"{float((amt / spent_this_cycle) * 100):.1f}%" if spent_this_cycle > 0 else "0%",
            }
            for cat, amt in sorted_cats
        ]
        st.dataframe(cat_display, use_container_width=True, hide_index=True)
    else:
        st.caption("No expenses recorded in the current active cycle.")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 6: LOCAL AI GUIDANCE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_plan:
    st.markdown("#### 📋 Budget & Meal Planning Guidance")
    st.caption("Powered by external Groq Cloud AI Agent (GPT-OSS 120B / Qwen 3.8).")

    can_generate = metrics.feasible and metrics.usable_pool > 0

    if "advice" not in st.session_state:
        st.session_state.advice = STATIC_GUIDE
        st.session_state.advice_source = "static"

    if st.button(
        "Generate Budget Guidance", type="primary",
        disabled=not can_generate,
        help=None if can_generate else "A positive usable balance is required.",
    ):
        with st.spinner("Generating budget recommendations..."):
            try:
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
    st.markdown(f"**Summary:** {advice.get('summary', STATIC_GUIDE['summary'])}")

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

    if st.button("💾 Save Survival Plan to Local DB", disabled=not can_generate):
        plan_id = database.save_plan(plan_name, metrics, json.dumps(advice, ensure_ascii=False))
        st.success(f"Survival plan archived locally (#{plan_id}).")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 7: PASSBOOK HISTORY
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_history:
    hist_plans_tab, hist_exp_tab, hist_inflows_tab = st.tabs(["Archived Plans", "All Cycle Expenses", "Inflow Log"])

    with hist_plans_tab:
        plans = database.list_plans()
        if not plans:
            st.caption("No archived survival plans.")
        else:
            for plan in plans:
                with st.expander(f"{plan['name']} • {plan['days']} Days • {format_inr(plan['daily_allowance'])}/day"):
                    st.caption(f"Archived {plan['created_at']} • Available: {format_inr(plan['total_money'])} • Bills: {format_inr(plan['fixed_expenses'])} • Reserve: {format_inr(plan['reserve'])}")
                    try:
                        st.json(json.loads(plan["advice_json"]))
                    except json.JSONDecodeError:
                        st.text(plan["advice_json"])
                    if st.button("Delete Plan", key=f"del_plan_{plan['id']}"):
                        database.delete_plan(plan["id"])
                        st.rerun()

    with hist_exp_tab:
        all_expenses = database.list_expenses_range(cycle.cycle_start.isoformat(), cycle.cycle_end.isoformat())
        if all_expenses:
            exp_display = [
                {
                    "Date": e["spent_on"],
                    "Category": e["category"],
                    "Payment Mode": e.get("payment_mode", "UPI"),
                    "Type": "✅ Planned" if e.get("is_planned", 1) else "⚡ Impulse",
                    "Memo": e["note"] or "-",
                    "Amount": format_inr(e["amount"]),
                }
                for e in all_expenses
            ]
            st.dataframe(exp_display, use_container_width=True, hide_index=True)
            st.caption(f"Displaying {len(all_expenses)} transactions in active cycle.")
        else:
            st.caption("No transactions found in this cycle.")

    with hist_inflows_tab:
        inflows = database.list_inflows(limit=25)
        if inflows:
            inflow_display = [
                {
                    "Date": i["date"],
                    "Stream Type": INFLOW_LABELS.get(i["type"], i["type"]),
                    "Amount": format_inr(i["amount"]),
                    "Memo": i.get("note", "-"),
                }
                for i in inflows
            ]
            st.dataframe(inflow_display, use_container_width=True, hide_index=True)
        else:
            st.caption("No inflows recorded yet.")

# ─── Footer ───────────────────────────────────────────────────────────────────

st.markdown("---")
footer_html = f"""
<div style="text-align: center; font-size: 0.8rem; color: {'#64748b' if is_light else '#94a3b8'}; padding: 12px 0;">
    🔒 <strong>Private & Secure</strong> • All financial computations and records remain strictly in your database.
</div>
"""
st.markdown(footer_html, unsafe_allow_html=True)
