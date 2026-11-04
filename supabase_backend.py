"""Supabase Cloud backend integration for MonthEnd."""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

CONFIG_FILE = Path(__file__).with_name("supabase_config.json")
ENV_FILE = Path(__file__).with_name(".env")

# Cached Supabase client
_supabase_client = None
_last_config_signature: Optional[str] = None


# ─── Type-Safe PostgREST Response Helpers ─────────────────────────────────────

def _as_list_dicts(data: Any) -> list[dict[str, Any]]:
    """Safely convert PostgREST response data to list of dicts."""
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _extract_id(res: Any) -> int:
    """Extract integer primary key ID from an APIResponse object."""
    data = getattr(res, "data", None)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        val = data[0].get("id")
        if isinstance(val, int):
            return val
        if val is not None:
            try:
                return int(val)
            except (ValueError, TypeError):
                pass
    return 0


# ─── Configuration ────────────────────────────────────────────────────────────

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


def get_client() -> Any:
    """Retrieve or initialize the active Supabase client."""
    global _supabase_client, _last_config_signature
    cfg = get_config()
    sig = f"{cfg['url']}::{cfg['key']}"

    if _supabase_client is not None and _last_config_signature == sig:
        return _supabase_client

    if not cfg["url"] or not cfg["key"]:
        return None

    from supabase import create_client  # type: ignore

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
        if "relation \"public.settings\" does not exist" in err_msg or "PGRST204" in err_msg or "PGRST205" in err_msg or "Could not find the table" in err_msg or "404" in err_msg:
            return False, "Connected to Supabase, but database tables are missing. Please execute 'supabase_schema.sql' in the Supabase SQL Editor.", {
                "configured": True,
                "schema_missing": True,
                "error": err_msg,
            }
        return False, f"Supabase connection failed: {err_msg}", {
            "configured": True,
            "error": err_msg,
        }


# ─── Authentication ──────────────────────────────────────────────────────────

def sign_in_user(email: str, password: str) -> dict[str, Any]:
    """Authenticate user with email and password via Supabase Auth."""
    client = get_client()
    if client is None:
        return {"success": False, "error": "Supabase client not configured"}
    try:
        res = client.auth.sign_in_with_password({"email": email, "password": password})
        if getattr(res, "user", None):
            session = getattr(res, "session", None)
            return {
                "success": True,
                "user": {"id": res.user.id, "email": res.user.email},
                "token": session.access_token if session else "",
            }
        return {"success": False, "error": "Invalid email or password"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def sign_up_user(email: str, password: str, metadata: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Register new user with email and password via Supabase Auth."""
    client = get_client()
    if client is None:
        return {"success": False, "error": "Supabase client not configured"}
    try:
        credentials: dict[str, Any] = {"email": email, "password": password}
        if metadata:
            credentials["options"] = {"data": metadata}
        res = client.auth.sign_up(credentials)
        if getattr(res, "user", None):
            session = getattr(res, "session", None)
            return {
                "success": True,
                "user": {"id": res.user.id, "email": res.user.email},
                "token": session.access_token if session else "",
            }
        return {"success": False, "error": "Registration failed"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ─── Settings ─────────────────────────────────────────────────────────────────

def get_settings() -> dict[str, Any]:
    client = get_client()
    default_settings: dict[str, Any] = {
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
        rows = _as_list_dicts(res.data)
        if rows:
            d = dict(rows[0])
            if "use_custom_days" not in d:
                d["use_custom_days"] = 0
            if "custom_days_left" not in d:
                d["custom_days_left"] = 30
            return d
        # Seed row if missing
        client.table("settings").insert(default_settings).execute()
        return default_settings
    except Exception as exc:
        raise RuntimeError(f"Supabase settings query failed: {exc}")


def save_settings(payday_day: int, inflow_type: str, expected_amount: Any, currency: str = "₹",
                  use_custom_days: int = 0, custom_days_left: int = 30, **kwargs: Any) -> None:
    client = get_client()
    if client is None:
        return

    payload: dict[str, Any] = {
        "id": 1,
        "payday_day_of_month": payday_day,
        "inflow_type": inflow_type,
        "expected_amount": str(expected_amount),
        "currency": currency,
        "use_custom_days": use_custom_days,
        "custom_days_left": custom_days_left,
        "updated_at": datetime.now().isoformat(),
    }
    for k in ("living_situation", "location", "resources", "emergency_reserve", "fixed_expenses", "total_balance"):
        if k in kwargs and kwargs[k] is not None:
            payload[k] = str(kwargs[k])

    try:
        client.table("settings").upsert(payload).execute()
    except Exception as exc:
        logger.debug("Supabase save_settings optional column upsert notice: %s", exc)


# ─── Plans ────────────────────────────────────────────────────────────────────

def save_plan(name: str, metrics: Any, advice_json: str) -> int:
    client = get_client()
    if client is None:
        return 0

    payload: dict[str, Any] = {
        "name": (name or "").strip() or "Survival plan",
        "total_money": str(metrics.total_money),
        "fixed_expenses": str(metrics.fixed_expenses),
        "reserve": str(metrics.emergency_reserve),
        "days": metrics.days,
        "daily_allowance": str(metrics.daily_allowance),
        "advice_json": advice_json,
    }
    res = client.table("plans").insert(payload).execute()
    return _extract_id(res)


def list_plans() -> list[dict[str, Any]]:
    client = get_client()
    if client is None:
        return []
    res = client.table("plans").select("*").order("created_at", desc=True).order("id", desc=True).execute()
    return _as_list_dicts(res.data)


def delete_plan(plan_id: int) -> None:
    client = get_client()
    if client is None:
        return
    client.table("plans").delete().eq("id", plan_id).execute()


# ─── Expenses ─────────────────────────────────────────────────────────────────

def add_expense(plan_id: Any, amount: Any, category: str, note: str = "", spent_on: Optional[str] = None,
                payment_mode: str = "UPI", is_planned: bool = True, hour_logged: Optional[int] = None) -> int:
    client = get_client()
    if client is None:
        return 0

    if hour_logged is None:
        hour_logged = datetime.now().hour

    payload: dict[str, Any] = {
        "plan_id": int(plan_id) if plan_id is not None else None,
        "spent_on": spent_on or date.today().isoformat(),
        "amount": str(amount),
        "category": category,
        "note": (note or "").strip(),
        "payment_mode": payment_mode,
        "is_planned": 1 if is_planned else 0,
        "hour_logged": hour_logged,
    }
    res = client.table("expenses").insert(payload).execute()
    return _extract_id(res)


def list_expenses(plan_id: Optional[int] = None, spent_on: Optional[str] = None) -> list[dict[str, Any]]:
    client = get_client()
    if client is None:
        return []

    query = client.table("expenses").select("*")
    if plan_id is not None:
        query = query.eq("plan_id", plan_id)
    if spent_on is not None:
        query = query.eq("spent_on", spent_on)

    res = query.order("spent_on", desc=True).order("id", desc=True).execute()
    return _as_list_dicts(res.data)


def list_expenses_range(start_date: str, end_date: str) -> list[dict[str, Any]]:
    client = get_client()
    if client is None:
        return []
    res = client.table("expenses").select("*")\
        .gte("spent_on", start_date)\
        .lte("spent_on", end_date)\
        .order("spent_on", desc=True)\
        .order("id", desc=True)\
        .execute()
    return _as_list_dicts(res.data)


def delete_expense(expense_id: int) -> None:
    client = get_client()
    if client is None:
        return
    client.table("expenses").delete().eq("id", expense_id).execute()


def get_total_spent_in_range(start_date: str, end_date: str) -> Decimal:
    client = get_client()
    if client is None:
        return Decimal("0.00")

    res = client.table("expenses").select("amount")\
        .gte("spent_on", start_date)\
        .lte("spent_on", end_date)\
        .execute()
    rows = _as_list_dicts(res.data)
    return sum((Decimal(str(r.get("amount", "0"))) for r in rows), Decimal("0.00"))


# ─── Inflows ──────────────────────────────────────────────────────────────────

def add_inflow(amount: Any, inflow_type: str, inflow_date: str, cycle_start: Optional[str] = None, cycle_end: Optional[str] = None, note: str = "") -> int:
    client = get_client()
    if client is None:
        return 0

    payload: dict[str, Any] = {
        "amount": str(amount),
        "type": inflow_type,
        "date": inflow_date,
        "cycle_start": cycle_start,
        "cycle_end": cycle_end,
        "note": (note or "").strip(),
    }
    res = client.table("inflows").insert(payload).execute()
    return _extract_id(res)


def list_inflows(limit: int = 50) -> list[dict[str, Any]]:
    client = get_client()
    if client is None:
        return []
    res = client.table("inflows").select("*")\
        .order("date", desc=True)\
        .order("id", desc=True)\
        .limit(limit)\
        .execute()
    return _as_list_dicts(res.data)


def get_inflows_in_range(start_date: str, end_date: str) -> list[dict[str, Any]]:
    client = get_client()
    if client is None:
        return []
    res = client.table("inflows").select("*")\
        .gte("date", start_date)\
        .lte("date", end_date)\
        .order("date", desc=True)\
        .execute()
    return _as_list_dicts(res.data)


# ─── Liabilities ──────────────────────────────────────────────────────────────

def add_liability(name: str, liability_type: str, emi_amount: Any, principal: Any = 0, interest_rate: Optional[float] = 0.0,
                  tenure_remaining: int = 1, due_day: int = 1, note: Optional[str] = "") -> int:
    client = get_client()
    if client is None:
        return 0

    payload: dict[str, Any] = {
        "name": name,
        "type": liability_type,
        "principal": str(principal),
        "emi_amount": str(emi_amount),
        "interest_rate": float(interest_rate or 0.0),
        "tenure_remaining": tenure_remaining,
        "due_day": due_day,
        "status": "ACTIVE",
        "note": (note or "").strip(),
    }
    res = client.table("liabilities").insert(payload).execute()
    return _extract_id(res)


def list_liabilities(status: Optional[str] = "ACTIVE") -> list[dict[str, Any]]:
    client = get_client()
    if client is None:
        return []

    query = client.table("liabilities").select("*")
    if status:
        query = query.eq("status", status)
    res = query.order("due_day").order("id").execute()
    return _as_list_dicts(res.data)


def update_liability_status(liability_id: int, status: str) -> None:
    client = get_client()
    if client is None:
        return
    client.table("liabilities").update({"status": status}).eq("id", liability_id).execute()


def delete_liability(liability_id: int) -> None:
    client = get_client()
    if client is None:
        return
    client.table("liabilities").delete().eq("id", liability_id).execute()


def decrement_tenure(liability_id: int) -> None:
    client = get_client()
    if client is None:
        return

    res = client.table("liabilities").select("tenure_remaining").eq("id", liability_id).execute()
    rows = _as_list_dicts(res.data)
    if rows:
        cur_tenure = int(rows[0].get("tenure_remaining", 1))
        new_tenure = max(0, cur_tenure - 1)
        up_data: dict[str, Any] = {"tenure_remaining": new_tenure}
        if new_tenure <= 0:
            up_data["status"] = "COMPLETED"
        client.table("liabilities").update(up_data).eq("id", liability_id).execute()


# ─── Liability Payments ──────────────────────────────────────────────────────

def add_liability_payment(liability_id: int, amount: Any, paid_date: Optional[str] = None) -> int:
    client = get_client()
    if client is None:
        return 0

    if paid_date is None:
        paid_date = date.today().isoformat()

    payload: dict[str, Any] = {
        "liability_id": liability_id,
        "paid_date": paid_date,
        "amount": str(amount),
    }
    res = client.table("liability_payments").insert(payload).execute()
    return _extract_id(res)


def list_liability_payments(liability_id: Optional[int] = None) -> list[dict[str, Any]]:
    client = get_client()
    if client is None:
        return []

    query = client.table("liability_payments").select("*")
    if liability_id is not None:
        query = query.eq("liability_id", liability_id)
    res = query.order("paid_date", desc=True).execute()
    return _as_list_dicts(res.data)


def get_pending_emis_total(cycle_start: str, cycle_end: str) -> Decimal:
    client = get_client()
    if client is None:
        return Decimal("0.00")

    res = client.table("liabilities").select("id, emi_amount, due_day").eq("status", "ACTIVE").execute()
    liabilities = _as_list_dicts(res.data)
    total = Decimal("0.00")
    for lia in liabilities:
        paid_res = client.table("liability_payments").select("id")\
            .eq("liability_id", lia.get("id"))\
            .gte("paid_date", cycle_start)\
            .lte("paid_date", cycle_end)\
            .execute()
        if not _as_list_dicts(paid_res.data):
            total += Decimal(str(lia.get("emi_amount", "0")))
    return total


# ─── Savings Goals ────────────────────────────────────────────────────────────

def add_savings_goal(name: str, target_amount: Any, deadline: Optional[str] = None, priority: int = 1) -> int:
    client = get_client()
    if client is None:
        return 0

    payload: dict[str, Any] = {
        "name": name,
        "target_amount": str(target_amount),
        "deadline": deadline,
        "priority": priority,
        "current_amount": "0",
        "status": "ACTIVE",
    }
    res = client.table("savings_goals").insert(payload).execute()
    return _extract_id(res)


def list_savings_goals(status: Optional[str] = "ACTIVE") -> list[dict[str, Any]]:
    client = get_client()
    if client is None:
        return []

    query = client.table("savings_goals").select("*")
    if status:
        query = query.eq("status", status)
    res = query.order("priority").order("id").execute()
    return _as_list_dicts(res.data)


def update_savings_goal_amount(goal_id: int, new_amount: Any) -> None:
    client = get_client()
    if client is None:
        return
    client.table("savings_goals").update({"current_amount": str(new_amount)}).eq("id", goal_id).execute()


def update_savings_goal_status(goal_id: int, status: str) -> None:
    client = get_client()
    if client is None:
        return
    client.table("savings_goals").update({"status": status}).eq("id", goal_id).execute()


def delete_savings_goal(goal_id: int) -> None:
    client = get_client()
    if client is None:
        return
    client.table("savings_goals").delete().eq("id", goal_id).execute()


# ─── Savings Transfers ───────────────────────────────────────────────────────

def add_savings_transfer(goal_id: int, amount: Any, transfer_date: Optional[str] = None) -> int:
    client = get_client()
    if client is None:
        return 0

    if transfer_date is None:
        transfer_date = date.today().isoformat()

    payload: dict[str, Any] = {
        "goal_id": goal_id,
        "amount": str(amount),
        "date": transfer_date,
    }
    res = client.table("savings_transfers").insert(payload).execute()
    new_id = _extract_id(res)

    # Increment goal current_amount
    goal_res = client.table("savings_goals").select("current_amount, target_amount").eq("id", goal_id).execute()
    g_rows = _as_list_dicts(goal_res.data)
    if g_rows:
        g = g_rows[0]
        cur_amt = Decimal(str(g.get("current_amount", "0"))) + Decimal(str(amount))
        target_amt = Decimal(str(g.get("target_amount", "0")))
        up_data: dict[str, Any] = {"current_amount": str(cur_amt)}
        if cur_amt >= target_amt:
            up_data["status"] = "REACHED"
        client.table("savings_goals").update(up_data).eq("id", goal_id).execute()

    return new_id


def list_savings_transfers(goal_id: Optional[int] = None) -> list[dict[str, Any]]:
    client = get_client()
    if client is None:
        return []

    query = client.table("savings_transfers").select("*")
    if goal_id is not None:
        query = query.eq("goal_id", goal_id)
    res = query.order("date", desc=True).execute()
    return _as_list_dicts(res.data)


# ─── Data Reset ──────────────────────────────────────────────────────────────

def clear_all_data() -> None:
    """Wipe all user records in Supabase and reset settings to clean defaults."""
    client = get_client()
    if client is None:
        return

    # Delete all rows from each table safely
    tables = [
        "savings_transfers",
        "savings_goals",
        "liability_payments",
        "liabilities",
        "expenses",
        "plans",
        "inflows",
    ]
    for table_name in tables:
        try:
            client.table(table_name).delete().neq("id", -1).execute()
        except Exception as e:
            logger.debug("Supabase clear table %s: %s", table_name, e)

    try:
        client.table("settings").upsert({
            "id": 1,
            "payday_day_of_month": 1,
            "inflow_type": "SALARY",
            "expected_amount": "0",
            "currency": "₹",
            "use_custom_days": 0,
            "custom_days_left": 30,
            "emergency_reserve": "0",
            "fixed_expenses": "0",
            "total_balance": "0",
        }).execute()
    except Exception as e:
        logger.debug("Supabase clear settings: %s", e)


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
