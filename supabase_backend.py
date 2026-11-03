"""Supabase Cloud backend integration for MonthEnd Sovereign FinTech.

Provides full parity with database.py's SQLite layer, supporting:
- Automatic credential resolution (.env, supabase_config.json, st.secrets, os.environ)
- Direct PostgREST client operations via supabase-py
- Live health checks and friendly schema diagnostics
- Bi-directional sync between local SQLite and Supabase cloud
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

CONFIG_FILE = Path(__file__).with_name("supabase_config.json")
ENV_FILE = Path(__file__).with_name(".env")

# Cached Supabase client
_supabase_client = None
_last_config_signature: Optional[str] = None


def _load_env_file() -> dict[str, str]:
    """Parse key=value pairs from .env without requiring external dependencies."""
    env_vars: dict[str, str] = {}
    if not ENV_FILE.exists():
        return env_vars
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k:
                    env_vars[k] = v
    except Exception:
        pass
    return env_vars


def get_config() -> dict[str, str]:
    """Resolve Supabase configuration from all available sources in priority order:
    1. supabase_config.json (in-app UI overrides)
    2. Streamlit secrets (st.secrets)
    3. Environment variables (os.environ)
    4. .env file
    """
    config = {
        "url": "",
        "key": "",
        "mode": "auto",  # auto | supabase | sqlite
    }

    # 4. .env file
    file_env = _load_env_file()
    if "SUPABASE_URL" in file_env:
        config["url"] = file_env["SUPABASE_URL"]
    if "SUPABASE_KEY" in file_env:
        config["key"] = file_env["SUPABASE_KEY"]
    elif "SUPABASE_ANON_KEY" in file_env:
        config["key"] = file_env["SUPABASE_ANON_KEY"]
    if "BACKEND_MODE" in file_env:
        config["mode"] = file_env["BACKEND_MODE"]

    # 3. os.environ
    if os.environ.get("SUPABASE_URL"):
        config["url"] = os.environ["SUPABASE_URL"]
    if os.environ.get("SUPABASE_KEY"):
        config["key"] = os.environ["SUPABASE_KEY"]
    elif os.environ.get("SUPABASE_ANON_KEY"):
        config["key"] = os.environ["SUPABASE_ANON_KEY"]
    if os.environ.get("BACKEND_MODE"):
        config["mode"] = os.environ["BACKEND_MODE"]

    # 2. Streamlit secrets
    try:
        import streamlit as st  # type: ignore
        if hasattr(st, "secrets"):
            if "SUPABASE_URL" in st.secrets:
                config["url"] = str(st.secrets["SUPABASE_URL"])
            if "SUPABASE_KEY" in st.secrets:
                config["key"] = str(st.secrets["SUPABASE_KEY"])
            elif "SUPABASE_ANON_KEY" in st.secrets:
                config["key"] = str(st.secrets["SUPABASE_ANON_KEY"])
            if "BACKEND_MODE" in st.secrets:
                config["mode"] = str(st.secrets["BACKEND_MODE"])
    except Exception:
        pass

    # 1. supabase_config.json
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    if saved.get("url"):
                        config["url"] = str(saved["url"]).strip()
                    if saved.get("key"):
                        config["key"] = str(saved["key"]).strip()
                    if saved.get("mode"):
                        config["mode"] = str(saved["mode"]).strip()
        except Exception:
            pass

    return config


def save_config(url: str, key: str, mode: str = "auto") -> None:
    """Save user-configured Supabase credentials to supabase_config.json."""
    global _supabase_client, _last_config_signature
    payload = {
        "url": url.strip(),
        "key": key.strip(),
        "mode": mode.strip().lower(),
        "updated_at": datetime.now().isoformat(),
    }
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    _supabase_client = None
    _last_config_signature = None


def is_configured() -> bool:
    """Return True if both Supabase URL and Key are available."""
    cfg = get_config()
    return bool(cfg["url"] and cfg["key"])


def get_client():
    """Retrieve or initialize the active Supabase client."""
    global _supabase_client, _last_config_signature
    cfg = get_config()
    sig = f"{cfg['url']}::{cfg['key']}"

    if _supabase_client is not None and _last_config_signature == sig:
        return _supabase_client

    if not cfg["url"] or not cfg["key"]:
        return None

    from supabase import create_client, Client  # type: ignore

    _supabase_client = create_client(cfg["url"], cfg["key"])
    _last_config_signature = sig
    return _supabase_client


def test_connection() -> tuple[bool, str, dict[str, Any]]:
    """Test connection to the Supabase backend.
    
    Returns:
        (success: bool, message: str, details: dict)
    """
    cfg = get_config()
    if not cfg["url"] or not cfg["key"]:
        return False, "Supabase URL and API Key are not configured.", {"configured": False}

    try:
        client = get_client()
        if client is None:
            return False, "Failed to instantiate Supabase client.", {"configured": False}

        # Query settings table to verify schema availability
        res = client.table("settings").select("id").limit(1).execute()
        return True, "Successfully connected to Supabase backend.", {
            "configured": True,
            "url": cfg["url"],
            "has_settings": bool(res.data),
        }
    except Exception as exc:
        err_msg = str(exc)
        if "relation \"public.settings\" does not exist" in err_msg or "PGRST204" in err_msg or "404" in err_msg:
            return False, "Connected to Supabase, but tables are missing. Please execute 'supabase_schema.sql' in the Supabase SQL Editor.", {
                "configured": True,
                "schema_missing": True,
                "error": err_msg,
            }
        return False, f"Supabase connection failed: {err_msg}", {
            "configured": True,
            "error": err_msg,
        }


# ─── Settings ─────────────────────────────────────────────────────────────────

def get_settings() -> dict:
    client = get_client()
    default_settings = {
        "id": 1,
        "payday_day_of_month": 1,
        "inflow_type": "SALARY",
        "expected_amount": "0",
        "currency": "₹",
        "use_custom_days": 0,
        "custom_days_left": 30,
    }
    if client is None:
        return default_settings

    try:
        res = client.table("settings").select("*").eq("id", 1).execute()
        if res.data:
            d = dict(res.data[0])
            if "use_custom_days" not in d:
                d["use_custom_days"] = 0
            if "custom_days_left" not in d:
                d["custom_days_left"] = 30
            return d
        # Seed row if missing
        client.table("settings").insert(default_settings).execute()
        return default_settings
    except Exception:
        return default_settings


def save_settings(payday_day: int, inflow_type: str, expected_amount, currency="₹",
                  use_custom_days: int = 0, custom_days_left: int = 30):
    client = get_client()
    if client is None:
        return

    payload = {
        "id": 1,
        "payday_day_of_month": int(payday_day),
        "inflow_type": str(inflow_type),
        "expected_amount": str(expected_amount),
        "currency": str(currency),
        "use_custom_days": int(use_custom_days),
        "custom_days_left": int(custom_days_left),
        "updated_at": datetime.now().isoformat(),
    }
    client.table("settings").upsert(payload).execute()


# ─── Plans ────────────────────────────────────────────────────────────────────

def save_plan(name, metrics, advice_json: str) -> int:
    client = get_client()
    if client is None:
        return 0

    payload = {
        "name": (name or "").strip() or "Survival plan",
        "total_money": str(metrics.total_money),
        "fixed_expenses": str(metrics.fixed_expenses),
        "reserve": str(metrics.emergency_reserve),
        "days": int(metrics.days),
        "daily_allowance": str(metrics.daily_allowance),
        "advice_json": advice_json,
    }
    res = client.table("plans").insert(payload).execute()
    if res.data and len(res.data) > 0:
        return int(res.data[0]["id"])
    return 0


def list_plans() -> list[dict]:
    client = get_client()
    if client is None:
        return []
    res = client.table("plans").select("*").order("created_at", desc=True).order("id", desc=True).execute()
    return res.data or []


def delete_plan(plan_id: int):
    client = get_client()
    if client is None:
        return
    client.table("plans").delete().eq("id", int(plan_id)).execute()


# ─── Expenses ─────────────────────────────────────────────────────────────────

def add_expense(plan_id, amount, category, note="", spent_on=None,
                payment_mode="UPI", is_planned=True, hour_logged=None) -> int:
    client = get_client()
    if client is None:
        return 0

    if hour_logged is None:
        hour_logged = datetime.now().hour

    payload = {
        "plan_id": int(plan_id) if plan_id is not None else None,
        "spent_on": spent_on or date.today().isoformat(),
        "amount": str(amount),
        "category": str(category),
        "note": (note or "").strip(),
        "payment_mode": str(payment_mode),
        "is_planned": 1 if is_planned else 0,
        "hour_logged": int(hour_logged),
    }
    res = client.table("expenses").insert(payload).execute()
    if res.data and len(res.data) > 0:
        return int(res.data[0]["id"])
    return 0


def list_expenses(plan_id=None, spent_on=None) -> list[dict]:
    client = get_client()
    if client is None:
        return []

    query = client.table("expenses").select("*")
    if plan_id is not None:
        query = query.eq("plan_id", int(plan_id))
    if spent_on is not None:
        query = query.eq("spent_on", str(spent_on))

    res = query.order("spent_on", desc=True).order("id", desc=True).execute()
    return res.data or []


def list_expenses_range(start_date: str, end_date: str) -> list[dict]:
    client = get_client()
    if client is None:
        return []
    res = client.table("expenses").select("*")\
        .gte("spent_on", start_date)\
        .lte("spent_on", end_date)\
        .order("spent_on", desc=True)\
        .order("id", desc=True)\
        .execute()
    return res.data or []


def delete_expense(expense_id: int):
    client = get_client()
    if client is None:
        return
    client.table("expenses").delete().eq("id", int(expense_id)).execute()


def get_total_spent_in_range(start_date: str, end_date: str) -> Decimal:
    client = get_client()
    if client is None:
        return Decimal("0.00")

    res = client.table("expenses").select("amount")\
        .gte("spent_on", start_date)\
        .lte("spent_on", end_date)\
        .execute()
    rows = res.data or []
    return sum((Decimal(str(r["amount"])) for r in rows), Decimal("0.00"))


# ─── Inflows ──────────────────────────────────────────────────────────────────

def add_inflow(amount, inflow_type, inflow_date, cycle_start=None, cycle_end=None, note="") -> int:
    client = get_client()
    if client is None:
        return 0

    payload = {
        "amount": str(amount),
        "type": str(inflow_type),
        "date": str(inflow_date),
        "cycle_start": cycle_start,
        "cycle_end": cycle_end,
        "note": (note or "").strip(),
    }
    res = client.table("inflows").insert(payload).execute()
    if res.data and len(res.data) > 0:
        return int(res.data[0]["id"])
    return 0


def list_inflows(limit=50) -> list[dict]:
    client = get_client()
    if client is None:
        return []
    res = client.table("inflows").select("*")\
        .order("date", desc=True)\
        .order("id", desc=True)\
        .limit(int(limit))\
        .execute()
    return res.data or []


def get_inflows_in_range(start_date: str, end_date: str) -> list[dict]:
    client = get_client()
    if client is None:
        return []
    res = client.table("inflows").select("*")\
        .gte("date", start_date)\
        .lte("date", end_date)\
        .order("date", desc=True)\
        .execute()
    return res.data or []


# ─── Liabilities ──────────────────────────────────────────────────────────────

def add_liability(name, liability_type, emi_amount, principal=0, interest_rate=0.0,
                  tenure_remaining=1, due_day=1, note="") -> int:
    client = get_client()
    if client is None:
        return 0

    payload = {
        "name": str(name),
        "type": str(liability_type),
        "principal": str(principal),
        "emi_amount": str(emi_amount),
        "interest_rate": float(interest_rate),
        "tenure_remaining": int(tenure_remaining),
        "due_day": int(due_day),
        "status": "ACTIVE",
        "note": (note or "").strip(),
    }
    res = client.table("liabilities").insert(payload).execute()
    if res.data and len(res.data) > 0:
        return int(res.data[0]["id"])
    return 0


def list_liabilities(status="ACTIVE") -> list[dict]:
    client = get_client()
    if client is None:
        return []

    query = client.table("liabilities").select("*")
    if status:
        query = query.eq("status", str(status))
    res = query.order("due_day").order("id").execute()
    return res.data or []


def update_liability_status(liability_id: int, status: str):
    client = get_client()
    if client is None:
        return
    client.table("liabilities").update({"status": str(status)}).eq("id", int(liability_id)).execute()


def delete_liability(liability_id: int):
    client = get_client()
    if client is None:
        return
    client.table("liabilities").delete().eq("id", int(liability_id)).execute()


def decrement_tenure(liability_id: int):
    client = get_client()
    if client is None:
        return

    res = client.table("liabilities").select("tenure_remaining").eq("id", int(liability_id)).execute()
    if res.data:
        cur_tenure = int(res.data[0].get("tenure_remaining", 1))
        new_tenure = max(0, cur_tenure - 1)
        up_data: dict[str, Any] = {"tenure_remaining": new_tenure}
        if new_tenure <= 0:
            up_data["status"] = "COMPLETED"
        client.table("liabilities").update(up_data).eq("id", int(liability_id)).execute()


# ─── Liability Payments ──────────────────────────────────────────────────────

def add_liability_payment(liability_id: int, amount, paid_date=None) -> int:
    client = get_client()
    if client is None:
        return 0

    if paid_date is None:
        paid_date = date.today().isoformat()

    payload = {
        "liability_id": int(liability_id),
        "paid_date": str(paid_date),
        "amount": str(amount),
    }
    res = client.table("liability_payments").insert(payload).execute()
    if res.data and len(res.data) > 0:
        return int(res.data[0]["id"])
    return 0


def list_liability_payments(liability_id=None) -> list[dict]:
    client = get_client()
    if client is None:
        return []

    query = client.table("liability_payments").select("*")
    if liability_id is not None:
        query = query.eq("liability_id", int(liability_id))
    res = query.order("paid_date", desc=True).execute()
    return res.data or []


def get_pending_emis_total(cycle_start: str, cycle_end: str) -> Decimal:
    client = get_client()
    if client is None:
        return Decimal("0.00")

    res = client.table("liabilities").select("id, emi_amount, due_day").eq("status", "ACTIVE").execute()
    liabilities = res.data or []
    total = Decimal("0.00")
    for lia in liabilities:
        paid_res = client.table("liability_payments").select("id")\
            .eq("liability_id", lia["id"])\
            .gte("paid_date", cycle_start)\
            .lte("paid_date", cycle_end)\
            .execute()
        if not (paid_res.data or []):
            total += Decimal(str(lia["emi_amount"]))
    return total


# ─── Savings Goals ────────────────────────────────────────────────────────────

def add_savings_goal(name: str, target_amount, deadline=None, priority=1) -> int:
    client = get_client()
    if client is None:
        return 0

    payload = {
        "name": str(name),
        "target_amount": str(target_amount),
        "deadline": deadline,
        "priority": int(priority),
        "current_amount": "0",
        "status": "ACTIVE",
    }
    res = client.table("savings_goals").insert(payload).execute()
    if res.data and len(res.data) > 0:
        return int(res.data[0]["id"])
    return 0


def list_savings_goals(status="ACTIVE") -> list[dict]:
    client = get_client()
    if client is None:
        return []

    query = client.table("savings_goals").select("*")
    if status:
        query = query.eq("status", str(status))
    res = query.order("priority").order("id").execute()
    return res.data or []


def update_savings_goal_amount(goal_id: int, new_amount):
    client = get_client()
    if client is None:
        return
    client.table("savings_goals").update({"current_amount": str(new_amount)}).eq("id", int(goal_id)).execute()


def update_savings_goal_status(goal_id: int, status: str):
    client = get_client()
    if client is None:
        return
    client.table("savings_goals").update({"status": str(status)}).eq("id", int(goal_id)).execute()


def delete_savings_goal(goal_id: int):
    client = get_client()
    if client is None:
        return
    client.table("savings_goals").delete().eq("id", int(goal_id)).execute()


# ─── Savings Transfers ───────────────────────────────────────────────────────

def add_savings_transfer(goal_id: int, amount, transfer_date=None) -> int:
    client = get_client()
    if client is None:
        return 0

    if transfer_date is None:
        transfer_date = date.today().isoformat()

    payload = {
        "goal_id": int(goal_id),
        "amount": str(amount),
        "date": str(transfer_date),
    }
    res = client.table("savings_transfers").insert(payload).execute()
    new_id = int(res.data[0]["id"]) if (res.data and len(res.data) > 0) else 0

    # Increment goal current_amount
    goal_res = client.table("savings_goals").select("current_amount, target_amount").eq("id", int(goal_id)).execute()
    if goal_res.data:
        g = goal_res.data[0]
        cur_amt = Decimal(str(g.get("current_amount", "0"))) + Decimal(str(amount))
        target_amt = Decimal(str(g.get("target_amount", "0")))
        up_data = {"current_amount": str(cur_amt)}
        if cur_amt >= target_amt:
            up_data["status"] = "REACHED"
        client.table("savings_goals").update(up_data).eq("id", int(goal_id)).execute()

    return new_id


def list_savings_transfers(goal_id=None) -> list[dict]:
    client = get_client()
    if client is None:
        return []

    query = client.table("savings_transfers").select("*")
    if goal_id is not None:
        query = query.eq("goal_id", int(goal_id))
    res = query.order("date", desc=True).execute()
    return res.data or []


# ─── Data Reset ──────────────────────────────────────────────────────────────

def clear_all_data():
    """Wipe all user records in Supabase and reset settings to clean defaults."""
    client = get_client()
    if client is None:
        return

    # Delete all rows from child and parent tables using .neq('id', -1)
    client.table("savings_transfers").delete().neq("id", -1).execute()
    client.table("savings_goals").delete().neq("id", -1).execute()
    client.table("liability_payments").delete().neq("id", -1).execute()
    client.table("liabilities").delete().neq("id", -1).execute()
    client.table("expenses").delete().neq("id", -1).execute()
    client.table("plans").delete().neq("id", -1).execute()
    client.table("inflows").delete().neq("id", -1).execute()
    client.table("settings").upsert({
        "id": 1,
        "payday_day_of_month": 1,
        "inflow_type": "SALARY",
        "expected_amount": "0",
        "currency": "₹",
        "use_custom_days": 0,
        "custom_days_left": 30,
    }).execute()


# ─── Sync & Migration Tools ──────────────────────────────────────────────────

def sync_sqlite_to_supabase(sqlite_db_path: Path) -> dict[str, int]:
    """Migrate all records from local SQLite into the connected Supabase instance."""
    import sqlite3
    client = get_client()
    if client is None:
        raise ValueError("Supabase is not configured.")

    if not sqlite_db_path.exists():
        return {"plans": 0, "expenses": 0, "inflows": 0, "liabilities": 0, "liability_payments": 0, "savings_goals": 0, "savings_transfers": 0}

    counts: dict[str, int] = {}
    conn = sqlite3.connect(str(sqlite_db_path))
    conn.row_factory = sqlite3.Row

    try:
        # Settings
        row = conn.execute("SELECT * FROM settings WHERE id=1").fetchone()
        if row:
            s_data = dict(row)
            client.table("settings").upsert({
                "id": 1,
                "payday_day_of_month": s_data.get("payday_day_of_month", 1),
                "inflow_type": s_data.get("inflow_type", "SALARY"),
                "expected_amount": str(s_data.get("expected_amount", "0")),
                "currency": s_data.get("currency", "₹"),
                "use_custom_days": int(s_data.get("use_custom_days", 0)),
                "custom_days_left": int(s_data.get("custom_days_left", 30)),
            }).execute()

        # Plans
        plans = [dict(r) for r in conn.execute("SELECT * FROM plans").fetchall()]
        if plans:
            client.table("plans").upsert(plans).execute()
        counts["plans"] = len(plans)

        # Expenses
        expenses = [dict(r) for r in conn.execute("SELECT * FROM expenses").fetchall()]
        if expenses:
            # Batch in 100s
            for i in range(0, len(expenses), 100):
                client.table("expenses").upsert(expenses[i:i+100]).execute()
        counts["expenses"] = len(expenses)

        # Inflows
        inflows = [dict(r) for r in conn.execute("SELECT * FROM inflows").fetchall()]
        if inflows:
            client.table("inflows").upsert(inflows).execute()
        counts["inflows"] = len(inflows)

        # Liabilities
        liabs = [dict(r) for r in conn.execute("SELECT * FROM liabilities").fetchall()]
        if liabs:
            client.table("liabilities").upsert(liabs).execute()
        counts["liabilities"] = len(liabs)

        # Liability payments
        lp = [dict(r) for r in conn.execute("SELECT * FROM liability_payments").fetchall()]
        if lp:
            client.table("liability_payments").upsert(lp).execute()
        counts["liability_payments"] = len(lp)

        # Savings goals
        sg = [dict(r) for r in conn.execute("SELECT * FROM savings_goals").fetchall()]
        if sg:
            client.table("savings_goals").upsert(sg).execute()
        counts["savings_goals"] = len(sg)

        # Savings transfers
        st = [dict(r) for r in conn.execute("SELECT * FROM savings_transfers").fetchall()]
        if st:
            client.table("savings_transfers").upsert(st).execute()
        counts["savings_transfers"] = len(st)

    finally:
        conn.close()

    return counts
