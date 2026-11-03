"""Unified Database Layer for MonthEnd Sovereign FinTech.

Supports dual backends:
1. Supabase Cloud (PostgreSQL via PostgREST / supabase-py)
2. Local SQLite (survival_budget.db - air-gapped fallback)

Caller interfaces are 100% backward-compatible.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import supabase_backend

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).with_name("survival_budget.db")


# ─── Backend Selection ────────────────────────────────────────────────────────

_supabase_verified: Optional[bool] = None
_last_check_time: float = 0.0


def is_supabase_active(db_path=DB_PATH) -> bool:
    """Determine whether calls should be routed to Supabase Cloud."""
    global _supabase_verified, _last_check_time
    if db_path != DB_PATH:
        return False
    cfg = supabase_backend.get_config()
    mode = cfg.get("mode", "auto").lower()
    if mode == "sqlite":
        return False
    if not supabase_backend.is_configured():
        return False
    if mode == "supabase":
        return True

    # In 'auto' mode, route to Supabase only if connection & schema are verified
    now = time.time()
    if _supabase_verified is None or (now - _last_check_time) > 15.0:
        ok, _, details = supabase_backend.test_connection()
        _supabase_verified = ok and not details.get("schema_missing", False)
        _last_check_time = now
    return bool(_supabase_verified)


def get_backend_info() -> dict[str, Any]:
    """Retrieve detailed diagnostic metadata about the active backend."""
    cfg = supabase_backend.get_config()
    configured = supabase_backend.is_configured()
    mode = cfg.get("mode", "auto").lower()
    connected = False
    message = "Using local SQLite database."
    schema_missing = False

    if configured:
        ok, msg, details = supabase_backend.test_connection()
        connected = ok
        message = msg
        schema_missing = details.get("schema_missing", False)

    if mode == "sqlite":
        active = "sqlite"
    elif mode == "supabase":
        active = "supabase" if connected else "sqlite_fallback"
    else:  # auto
        active = "supabase" if connected else "sqlite"

    return {
        "active_backend": active,
        "mode": mode,
        "configured": configured,
        "connected": connected,
        "message": message,
        "schema_missing": schema_missing,
        "url": cfg.get("url", ""),
        "sqlite_path": str(DB_PATH),
    }


def set_backend_mode(mode: str):
    """Set backend mode preference ('auto', 'supabase', 'sqlite')."""
    cfg = supabase_backend.get_config()
    supabase_backend.save_config(cfg.get("url", ""), cfg.get("key", ""), mode=mode)


def sync_sqlite_to_supabase() -> dict[str, int]:
    """Migrate all data from local SQLite into Supabase."""
    return supabase_backend.sync_sqlite_to_supabase(DB_PATH)


# ─── Local SQLite Connection ──────────────────────────────────────────────────

@contextmanager
def _connection(db_path=DB_PATH):
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def _add_column_if_missing(conn, table, column, typedef):
    cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {typedef}")


# ─── Schema Initialization ───────────────────────────────────────────────────

def init_db(db_path=DB_PATH):
    """Initialize local SQLite schema and check Supabase readiness if configured."""
    with _connection(db_path) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            total_money TEXT NOT NULL,
            fixed_expenses TEXT NOT NULL,
            reserve TEXT NOT NULL,
            days INTEGER NOT NULL,
            daily_allowance TEXT NOT NULL,
            advice_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER REFERENCES plans(id) ON DELETE CASCADE,
            spent_on TEXT NOT NULL,
            amount TEXT NOT NULL,
            category TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS expenses_plan_date ON expenses(plan_id, spent_on);

        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            payday_day_of_month INTEGER NOT NULL DEFAULT 1,
            inflow_type TEXT NOT NULL DEFAULT 'SALARY',
            expected_amount TEXT NOT NULL DEFAULT '0',
            currency TEXT NOT NULL DEFAULT '₹'
        );
        INSERT OR IGNORE INTO settings(id) VALUES(1);

        CREATE TABLE IF NOT EXISTS inflows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'SALARY',
            date TEXT NOT NULL,
            cycle_start TEXT,
            cycle_end TEXT,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS liabilities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'EMI',
            principal TEXT NOT NULL DEFAULT '0',
            emi_amount TEXT NOT NULL,
            interest_rate REAL NOT NULL DEFAULT 0.0,
            tenure_remaining INTEGER NOT NULL DEFAULT 1,
            due_day INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS liability_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            liability_id INTEGER NOT NULL REFERENCES liabilities(id) ON DELETE CASCADE,
            paid_date TEXT NOT NULL,
            amount TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS savings_goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            target_amount TEXT NOT NULL,
            deadline TEXT,
            current_amount TEXT NOT NULL DEFAULT '0',
            priority INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS savings_transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER NOT NULL REFERENCES savings_goals(id) ON DELETE CASCADE,
            amount TEXT NOT NULL,
            date TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """)

        _add_column_if_missing(conn, "expenses", "payment_mode", "TEXT NOT NULL DEFAULT 'UPI'")
        _add_column_if_missing(conn, "expenses", "is_planned", "INTEGER NOT NULL DEFAULT 1")
        _add_column_if_missing(conn, "expenses", "hour_logged", "INTEGER NOT NULL DEFAULT 12")
        _add_column_if_missing(conn, "settings", "use_custom_days", "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "settings", "custom_days_left", "INTEGER NOT NULL DEFAULT 30")


# ─── Settings ─────────────────────────────────────────────────────────────────

def _sqlite_get_settings(db_path=DB_PATH) -> dict:
    with _connection(db_path) as conn:
        row = conn.execute("SELECT * FROM settings WHERE id=1").fetchone()
        if row:
            d = dict(row)
            if "use_custom_days" not in d:
                d["use_custom_days"] = 0
            if "custom_days_left" not in d:
                d["custom_days_left"] = 30
            return d
        return {
            "payday_day_of_month": 1, "inflow_type": "SALARY",
            "expected_amount": "0", "currency": "₹",
            "use_custom_days": 0, "custom_days_left": 30
        }


def get_settings(db_path=DB_PATH) -> dict:
    if is_supabase_active(db_path):
        try:
            return supabase_backend.get_settings()
        except Exception as e:
            logger.warning("Supabase get_settings failed, falling back to SQLite: %s", e)
    return _sqlite_get_settings(db_path)


def _sqlite_save_settings(payday_day: int, inflow_type: str, expected_amount, currency="₹",
                          use_custom_days: int = 0, custom_days_left: int = 30, db_path=DB_PATH):
    with _connection(db_path) as conn:
        conn.execute(
            "UPDATE settings SET payday_day_of_month=?, inflow_type=?, expected_amount=?, currency=?, use_custom_days=?, custom_days_left=? WHERE id=1",
            (payday_day, inflow_type, str(expected_amount), currency, int(use_custom_days), int(custom_days_left)),
        )


def save_settings(payday_day: int, inflow_type: str, expected_amount, currency="₹",
                  use_custom_days: int = 0, custom_days_left: int = 30, db_path=DB_PATH):
    # Always persist locally for offline resilience
    _sqlite_save_settings(payday_day, inflow_type, expected_amount, currency, use_custom_days, custom_days_left, db_path)
    if is_supabase_active(db_path):
        try:
            supabase_backend.save_settings(payday_day, inflow_type, expected_amount, currency, use_custom_days, custom_days_left)
        except Exception as e:
            logger.warning("Supabase save_settings failed: %s", e)


# ─── Plans ────────────────────────────────────────────────────────────────────

def _sqlite_save_plan(name, metrics, advice_json, db_path=DB_PATH) -> int:
    with _connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO plans(name,total_money,fixed_expenses,reserve,days,daily_allowance,advice_json) VALUES(?,?,?,?,?,?,?)",
            (name.strip() or "Survival plan", str(metrics.total_money),
             str(metrics.fixed_expenses), str(metrics.emergency_reserve),
             metrics.days, str(metrics.daily_allowance), advice_json),
        )
        return int(cur.lastrowid or 0)


def save_plan(name, metrics, advice_json, db_path=DB_PATH) -> int:
    local_id = _sqlite_save_plan(name, metrics, advice_json, db_path)
    if is_supabase_active(db_path):
        try:
            cloud_id = supabase_backend.save_plan(name, metrics, advice_json)
            return cloud_id or local_id
        except Exception as e:
            logger.warning("Supabase save_plan failed: %s", e)
    return local_id


def list_plans(db_path=DB_PATH) -> list[dict]:
    if is_supabase_active(db_path):
        try:
            return supabase_backend.list_plans()
        except Exception as e:
            logger.warning("Supabase list_plans failed, falling back to SQLite: %s", e)
    with _connection(db_path) as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM plans ORDER BY created_at DESC, id DESC")]


def delete_plan(plan_id, db_path=DB_PATH):
    with _connection(db_path) as conn:
        conn.execute("DELETE FROM plans WHERE id=?", (int(plan_id),))
    if is_supabase_active(db_path):
        try:
            supabase_backend.delete_plan(plan_id)
        except Exception as e:
            logger.warning("Supabase delete_plan failed: %s", e)


# ─── Expenses ─────────────────────────────────────────────────────────────────

def _sqlite_add_expense(plan_id, amount, category, note="", spent_on=None,
                        payment_mode="UPI", is_planned=True, hour_logged=None, db_path=DB_PATH) -> int:
    if hour_logged is None:
        hour_logged = datetime.now().hour
    with _connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO expenses(plan_id,spent_on,amount,category,note,payment_mode,is_planned,hour_logged) VALUES(?,?,?,?,?,?,?,?)",
            (plan_id, (spent_on or date.today().isoformat()), str(amount),
             category, (note or "").strip(), payment_mode, int(is_planned), hour_logged),
        )
        return int(cur.lastrowid or 0)


def add_expense(plan_id, amount, category, note="", spent_on=None,
                payment_mode="UPI", is_planned=True, hour_logged=None, db_path=DB_PATH) -> int:
    local_id = _sqlite_add_expense(plan_id, amount, category, note, spent_on, payment_mode, is_planned, hour_logged, db_path)
    if is_supabase_active(db_path):
        try:
            cloud_id = supabase_backend.add_expense(plan_id, amount, category, note, spent_on, payment_mode, is_planned, hour_logged)
            return cloud_id or local_id
        except Exception as e:
            logger.warning("Supabase add_expense failed: %s", e)
    return local_id


def list_expenses(plan_id=None, spent_on=None, db_path=DB_PATH) -> list[dict]:
    if is_supabase_active(db_path):
        try:
            return supabase_backend.list_expenses(plan_id, spent_on)
        except Exception as e:
            logger.warning("Supabase list_expenses failed, falling back to SQLite: %s", e)
    clauses, values = [], []
    if plan_id is not None:
        clauses.append("plan_id=?"); values.append(int(plan_id))
    if spent_on is not None:
        clauses.append("spent_on=?"); values.append(str(spent_on))
    sql = "SELECT * FROM expenses" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY spent_on DESC, id DESC"
    with _connection(db_path) as conn:
        return [dict(row) for row in conn.execute(sql, values)]


def list_expenses_range(start_date: str, end_date: str, db_path=DB_PATH) -> list[dict]:
    if is_supabase_active(db_path):
        try:
            return supabase_backend.list_expenses_range(start_date, end_date)
        except Exception as e:
            logger.warning("Supabase list_expenses_range failed, falling back to SQLite: %s", e)
    with _connection(db_path) as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM expenses WHERE spent_on >= ? AND spent_on <= ? ORDER BY spent_on DESC, id DESC",
            (start_date, end_date),
        )]


def delete_expense(expense_id, db_path=DB_PATH):
    with _connection(db_path) as conn:
        conn.execute("DELETE FROM expenses WHERE id=?", (int(expense_id),))
    if is_supabase_active(db_path):
        try:
            supabase_backend.delete_expense(expense_id)
        except Exception as e:
            logger.warning("Supabase delete_expense failed: %s", e)


def get_total_spent_in_range(start_date: str, end_date: str, db_path=DB_PATH) -> Decimal:
    if is_supabase_active(db_path):
        try:
            return supabase_backend.get_total_spent_in_range(start_date, end_date)
        except Exception as e:
            logger.warning("Supabase get_total_spent_in_range failed, falling back to SQLite: %s", e)
    with _connection(db_path) as conn:
        rows = conn.execute(
            "SELECT amount FROM expenses WHERE spent_on >= ? AND spent_on <= ?",
            (start_date, end_date),
        ).fetchall()
    return sum((Decimal(r["amount"]) for r in rows), Decimal("0.00"))


# ─── Inflows ──────────────────────────────────────────────────────────────────

def _sqlite_add_inflow(amount, inflow_type, inflow_date, cycle_start=None, cycle_end=None, note="", db_path=DB_PATH) -> int:
    with _connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO inflows(amount,type,date,cycle_start,cycle_end,note) VALUES(?,?,?,?,?,?)",
            (str(amount), inflow_type, inflow_date, cycle_start, cycle_end, (note or "").strip()),
        )
        return int(cur.lastrowid or 0)


def add_inflow(amount, inflow_type, inflow_date, cycle_start=None, cycle_end=None, note="", db_path=DB_PATH) -> int:
    local_id = _sqlite_add_inflow(amount, inflow_type, inflow_date, cycle_start, cycle_end, note, db_path)
    if is_supabase_active(db_path):
        try:
            cloud_id = supabase_backend.add_inflow(amount, inflow_type, inflow_date, cycle_start, cycle_end, note)
            return cloud_id or local_id
        except Exception as e:
            logger.warning("Supabase add_inflow failed: %s", e)
    return local_id


def list_inflows(limit=50, db_path=DB_PATH) -> list[dict]:
    if is_supabase_active(db_path):
        try:
            return supabase_backend.list_inflows(limit)
        except Exception as e:
            logger.warning("Supabase list_inflows failed, falling back to SQLite: %s", e)
    with _connection(db_path) as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM inflows ORDER BY date DESC, id DESC LIMIT ?", (limit,)
        )]


def get_inflows_in_range(start_date: str, end_date: str, db_path=DB_PATH) -> list[dict]:
    if is_supabase_active(db_path):
        try:
            return supabase_backend.get_inflows_in_range(start_date, end_date)
        except Exception as e:
            logger.warning("Supabase get_inflows_in_range failed, falling back to SQLite: %s", e)
    with _connection(db_path) as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM inflows WHERE date >= ? AND date <= ? ORDER BY date DESC",
            (start_date, end_date),
        )]


# ─── Liabilities ──────────────────────────────────────────────────────────────

def _sqlite_add_liability(name, liability_type, emi_amount, principal=0, interest_rate=0.0,
                          tenure_remaining=1, due_day=1, note="", db_path=DB_PATH) -> int:
    with _connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO liabilities(name,type,principal,emi_amount,interest_rate,tenure_remaining,due_day,note) VALUES(?,?,?,?,?,?,?,?)",
            (name, liability_type, str(principal), str(emi_amount), interest_rate,
             tenure_remaining, due_day, (note or "").strip()),
        )
        return int(cur.lastrowid or 0)


def add_liability(name, liability_type, emi_amount, principal=0, interest_rate=0.0,
                  tenure_remaining=1, due_day=1, note="", db_path=DB_PATH) -> int:
    local_id = _sqlite_add_liability(name, liability_type, emi_amount, principal, interest_rate, tenure_remaining, due_day, note, db_path)
    if is_supabase_active(db_path):
        try:
            cloud_id = supabase_backend.add_liability(name, liability_type, emi_amount, principal, interest_rate, tenure_remaining, due_day, note)
            return cloud_id or local_id
        except Exception as e:
            logger.warning("Supabase add_liability failed: %s", e)
    return local_id


def list_liabilities(status="ACTIVE", db_path=DB_PATH) -> list[dict]:
    if is_supabase_active(db_path):
        try:
            return supabase_backend.list_liabilities(status)
        except Exception as e:
            logger.warning("Supabase list_liabilities failed, falling back to SQLite: %s", e)
    with _connection(db_path) as conn:
        if status:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM liabilities WHERE status=? ORDER BY due_day, id", (status,)
            )]
        return [dict(row) for row in conn.execute("SELECT * FROM liabilities ORDER BY due_day, id")]


def update_liability_status(liability_id, status, db_path=DB_PATH):
    with _connection(db_path) as conn:
        conn.execute("UPDATE liabilities SET status=? WHERE id=?", (status, int(liability_id)))
    if is_supabase_active(db_path):
        try:
            supabase_backend.update_liability_status(liability_id, status)
        except Exception as e:
            logger.warning("Supabase update_liability_status failed: %s", e)


def delete_liability(liability_id, db_path=DB_PATH):
    with _connection(db_path) as conn:
        conn.execute("DELETE FROM liabilities WHERE id=?", (int(liability_id),))
    if is_supabase_active(db_path):
        try:
            supabase_backend.delete_liability(liability_id)
        except Exception as e:
            logger.warning("Supabase delete_liability failed: %s", e)


def decrement_tenure(liability_id, db_path=DB_PATH):
    with _connection(db_path) as conn:
        conn.execute("UPDATE liabilities SET tenure_remaining = MAX(0, tenure_remaining - 1) WHERE id=?", (int(liability_id),))
        row = conn.execute("SELECT tenure_remaining FROM liabilities WHERE id=?", (int(liability_id),)).fetchone()
        if row and row["tenure_remaining"] <= 0:
            conn.execute("UPDATE liabilities SET status='COMPLETED' WHERE id=?", (int(liability_id),))
    if is_supabase_active(db_path):
        try:
            supabase_backend.decrement_tenure(liability_id)
        except Exception as e:
            logger.warning("Supabase decrement_tenure failed: %s", e)


# ─── Liability Payments ──────────────────────────────────────────────────────

def _sqlite_add_liability_payment(liability_id, amount, paid_date=None, db_path=DB_PATH) -> int:
    if paid_date is None:
        paid_date = date.today().isoformat()
    with _connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO liability_payments(liability_id,paid_date,amount) VALUES(?,?,?)",
            (int(liability_id), paid_date, str(amount)),
        )
        return int(cur.lastrowid or 0)


def add_liability_payment(liability_id, amount, paid_date=None, db_path=DB_PATH) -> int:
    local_id = _sqlite_add_liability_payment(liability_id, amount, paid_date, db_path)
    if is_supabase_active(db_path):
        try:
            cloud_id = supabase_backend.add_liability_payment(liability_id, amount, paid_date)
            return cloud_id or local_id
        except Exception as e:
            logger.warning("Supabase add_liability_payment failed: %s", e)
    return local_id


def list_liability_payments(liability_id=None, db_path=DB_PATH) -> list[dict]:
    if is_supabase_active(db_path):
        try:
            return supabase_backend.list_liability_payments(liability_id)
        except Exception as e:
            logger.warning("Supabase list_liability_payments failed, falling back to SQLite: %s", e)
    with _connection(db_path) as conn:
        if liability_id is not None:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM liability_payments WHERE liability_id=? ORDER BY paid_date DESC", (int(liability_id),)
            )]
        return [dict(row) for row in conn.execute("SELECT * FROM liability_payments ORDER BY paid_date DESC")]


def get_pending_emis_total(cycle_start: str, cycle_end: str, db_path=DB_PATH) -> Decimal:
    if is_supabase_active(db_path):
        try:
            return supabase_backend.get_pending_emis_total(cycle_start, cycle_end)
        except Exception as e:
            logger.warning("Supabase get_pending_emis_total failed, falling back to SQLite: %s", e)
    with _connection(db_path) as conn:
        liabilities = conn.execute(
            "SELECT id, emi_amount, due_day FROM liabilities WHERE status='ACTIVE'"
        ).fetchall()
        total = Decimal("0.00")
        for lia in liabilities:
            paid = conn.execute(
                "SELECT COUNT(*) as cnt FROM liability_payments WHERE liability_id=? AND paid_date>=? AND paid_date<=?",
                (lia["id"], cycle_start, cycle_end),
            ).fetchone()
            if paid["cnt"] == 0:
                total += Decimal(lia["emi_amount"])
        return total


# ─── Savings Goals ────────────────────────────────────────────────────────────

def _sqlite_add_savings_goal(name, target_amount, deadline=None, priority=1, db_path=DB_PATH) -> int:
    with _connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO savings_goals(name,target_amount,deadline,priority) VALUES(?,?,?,?)",
            (name, str(target_amount), deadline, priority),
        )
        return int(cur.lastrowid or 0)


def add_savings_goal(name, target_amount, deadline=None, priority=1, db_path=DB_PATH) -> int:
    local_id = _sqlite_add_savings_goal(name, target_amount, deadline, priority, db_path)
    if is_supabase_active(db_path):
        try:
            cloud_id = supabase_backend.add_savings_goal(name, target_amount, deadline, priority)
            return cloud_id or local_id
        except Exception as e:
            logger.warning("Supabase add_savings_goal failed: %s", e)
    return local_id


def list_savings_goals(status="ACTIVE", db_path=DB_PATH) -> list[dict]:
    if is_supabase_active(db_path):
        try:
            return supabase_backend.list_savings_goals(status)
        except Exception as e:
            logger.warning("Supabase list_savings_goals failed, falling back to SQLite: %s", e)
    with _connection(db_path) as conn:
        if status:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM savings_goals WHERE status=? ORDER BY priority, id", (status,)
            )]
        return [dict(row) for row in conn.execute("SELECT * FROM savings_goals ORDER BY priority, id")]


def update_savings_goal_amount(goal_id, new_amount, db_path=DB_PATH):
    with _connection(db_path) as conn:
        conn.execute("UPDATE savings_goals SET current_amount=? WHERE id=?", (str(new_amount), int(goal_id)))
    if is_supabase_active(db_path):
        try:
            supabase_backend.update_savings_goal_amount(goal_id, new_amount)
        except Exception as e:
            logger.warning("Supabase update_savings_goal_amount failed: %s", e)


def update_savings_goal_status(goal_id, status, db_path=DB_PATH):
    with _connection(db_path) as conn:
        conn.execute("UPDATE savings_goals SET status=? WHERE id=?", (status, int(goal_id)))
    if is_supabase_active(db_path):
        try:
            supabase_backend.update_savings_goal_status(goal_id, status)
        except Exception as e:
            logger.warning("Supabase update_savings_goal_status failed: %s", e)


def delete_savings_goal(goal_id, db_path=DB_PATH):
    with _connection(db_path) as conn:
        conn.execute("DELETE FROM savings_goals WHERE id=?", (int(goal_id),))
    if is_supabase_active(db_path):
        try:
            supabase_backend.delete_savings_goal(goal_id)
        except Exception as e:
            logger.warning("Supabase delete_savings_goal failed: %s", e)


# ─── Savings Transfers ───────────────────────────────────────────────────────

def _sqlite_add_savings_transfer(goal_id, amount, transfer_date=None, db_path=DB_PATH) -> int:
    if transfer_date is None:
        transfer_date = date.today().isoformat()
    with _connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO savings_transfers(goal_id,amount,date) VALUES(?,?,?)",
            (int(goal_id), str(amount), transfer_date),
        )
        row = conn.execute("SELECT current_amount FROM savings_goals WHERE id=?", (int(goal_id),)).fetchone()
        if row:
            new_amt = Decimal(row["current_amount"]) + Decimal(str(amount))
            conn.execute("UPDATE savings_goals SET current_amount=? WHERE id=?", (str(new_amt), int(goal_id)))
            target_row = conn.execute("SELECT target_amount FROM savings_goals WHERE id=?", (int(goal_id),)).fetchone()
            if target_row and new_amt >= Decimal(target_row["target_amount"]):
                conn.execute("UPDATE savings_goals SET status='REACHED' WHERE id=?", (int(goal_id),))
        return int(cur.lastrowid or 0)


def add_savings_transfer(goal_id, amount, transfer_date=None, db_path=DB_PATH) -> int:
    local_id = _sqlite_add_savings_transfer(goal_id, amount, transfer_date, db_path)
    if is_supabase_active(db_path):
        try:
            cloud_id = supabase_backend.add_savings_transfer(goal_id, amount, transfer_date)
            return cloud_id or local_id
        except Exception as e:
            logger.warning("Supabase add_savings_transfer failed: %s", e)
    return local_id


def list_savings_transfers(goal_id=None, db_path=DB_PATH) -> list[dict]:
    if is_supabase_active(db_path):
        try:
            return supabase_backend.list_savings_transfers(goal_id)
        except Exception as e:
            logger.warning("Supabase list_savings_transfers failed, falling back to SQLite: %s", e)
    with _connection(db_path) as conn:
        if goal_id is not None:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM savings_transfers WHERE goal_id=? ORDER BY date DESC", (int(goal_id),)
            )]
        return [dict(row) for row in conn.execute("SELECT * FROM savings_transfers ORDER BY date DESC")]


# ─── Data Reset ──────────────────────────────────────────────────────────────

def clear_all_data(db_path=DB_PATH):
    """Wipe all user records in local SQLite and Supabase (if active)."""
    with _connection(db_path) as conn:
        conn.execute("DELETE FROM expenses")
        conn.execute("DELETE FROM plans")
        conn.execute("DELETE FROM inflows")
        conn.execute("DELETE FROM liability_payments")
        conn.execute("DELETE FROM liabilities")
        conn.execute("DELETE FROM savings_transfers")
        conn.execute("DELETE FROM savings_goals")
        conn.execute("UPDATE settings SET payday_day_of_month=1, inflow_type='SALARY', expected_amount='0', currency='₹', use_custom_days=0, custom_days_left=30 WHERE id=1")
        conn.execute("DELETE FROM sqlite_sequence")
    if is_supabase_active(db_path):
        try:
            supabase_backend.clear_all_data()
        except Exception as e:
            logger.warning("Supabase clear_all_data failed: %s", e)
