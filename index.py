"""Native Vercel Web Application for MonthEnd Personal Budgeting.

Self-contained WSGI web server providing both:
1. Single-Page Application at GET /
2. Complete REST API endpoints (/api/dashboard, /api/expenses, /api/liabilities, /api/savings, /api/settings)
"""
from __future__ import annotations

import io
import json
import logging
import urllib.parse
from datetime import date, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs

import analytics_engine
import budget_engine
from categories import (
    BNPL_PRESETS,
    CATEGORIES,
    INFLOW_LABELS,
    INFLOW_TYPES,
    LIABILITY_TYPES,
    PAYMENT_MODES,
)
import cycle_engine
import database
from formatters import format_inr, money
import impulse_engine
import llm_client
import savings_engine
import supabase_backend

logger = logging.getLogger(__name__)

# Ensure database tables exist on startup
try:
    database.init_db()
except Exception as exc:
    logger.warning("Database init failed on startup: %s", exc)

HTML_PATH = Path(__file__).resolve().parent / "index.html"
LOGIN_HTML_PATH = Path(__file__).resolve().parent / "login.html"


def _read_html_file(filename: str) -> bytes | None:
    candidates = [
        Path(__file__).resolve().parent / filename,
        Path.cwd() / filename,
        Path(filename),
        Path(__file__).resolve().parent.parent / filename,
    ]
    for p in candidates:
        if p.is_file():
            try:
                return p.read_bytes()
            except Exception:
                continue
    return None


def _json_serialize(data: Any) -> str:
    def _default(obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        if hasattr(obj, "_asdict"):
            return obj._asdict()
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

    return json.dumps(data, default=_default)


def _get_dashboard_data() -> dict[str, Any]:
    """Aggregate complete dashboard state matching the executive Streamlit metrics."""
    settings = database.get_settings()
    payday_day = int(settings.get("payday_day_of_month", 1))
    inflow_type = str(settings.get("inflow_type", "SALARY"))
    expected_amount = money(settings.get("expected_amount", 0))

    cycle = cycle_engine.get_current_cycle(payday_day)
    cycle_start_str = cycle.cycle_start.isoformat()
    cycle_end_str = cycle.cycle_end.isoformat()
    today_str = cycle.today.isoformat()

    # Expenses in current cycle
    expenses_cycle = database.list_expenses_range(cycle_start_str, cycle_end_str)
    spent_this_cycle = database.get_total_spent_in_range(cycle_start_str, cycle_end_str)
    spent_today = database.get_total_spent_in_range(today_str, today_str)

    # Inflows & Liabilities
    inflows = database.get_inflows_in_range(cycle_start_str, cycle_end_str)
    total_inflow = sum((money(i.get("amount", 0)) for i in inflows), Decimal("0.00"))
    if total_inflow <= 0:
        total_inflow = expected_amount

    pending_emis = database.get_pending_emis_total(cycle_start_str, cycle_end_str)

    # Latest plan or dynamic calculation
    plans = database.list_plans()
    if plans:
        latest_plan = plans[0]
        reserve = money(latest_plan.get("reserve", 0))
        fixed = money(latest_plan.get("fixed_expenses", 0))
        total_pool = money(latest_plan.get("total_money", total_inflow))
    else:
        reserve = Decimal("0.00")
        fixed = Decimal("0.00")
        total_pool = total_inflow

    metrics = budget_engine.calculate_budget(
        total_money=total_pool,
        fixed_expenses=fixed,
        days=cycle.total_days,
        reserve=reserve,
        pending_emis=pending_emis,
        spent_this_cycle=spent_this_cycle,
        days_remaining=cycle.days_remaining,
    )

    daily_status = budget_engine.derive_daily_status(metrics, spent_today)

    # Run-rate & persona
    elapsed_days = max(1, cycle.day_index)
    run_rate = analytics_engine.run_rate_projection(spent_this_cycle, elapsed_days, cycle.total_days)
    persona = analytics_engine.classify_spending_persona(payday_day)

    # MoM Deltas & Surges
    mom_deltas = analytics_engine.mom_category_delta(cycle, payday_day)
    surges = analytics_engine.category_surges(mom_deltas)
    habit_tips = analytics_engine.generate_habit_tips(surges, database.list_savings_goals(), metrics.safe_daily_limit)

    # Savings Goals Progress
    raw_goals = database.list_savings_goals(status="ACTIVE")
    median_surplus = analytics_engine.get_median_monthly_surplus(payday_day)
    goal_progress_list = [savings_engine.compute_goal_progress(g) for g in raw_goals]
    checked_goals = savings_engine.feasibility_check(goal_progress_list, median_surplus)

    goals_data = []
    for gp in checked_goals:
        goals_data.append({
            "goal_id": gp.goal_id,
            "name": gp.name,
            "target": float(gp.target),
            "current": float(gp.current),
            "remaining": float(gp.remaining),
            "pct": gp.pct,
            "monthly_needed": float(gp.monthly_needed),
            "months_left": gp.months_left,
            "is_feasible": gp.is_feasible,
            "milestone": gp.milestone,
            "status": gp.status,
            "formatted_target": format_inr(gp.target),
            "formatted_current": format_inr(gp.current),
            "formatted_remaining": format_inr(gp.remaining),
        })

    # Recent expenses (last 30)
    all_expenses = database.list_expenses()[:30]

    return {
        "cycle": {
            "start": cycle_start_str,
            "end": cycle_end_str,
            "day_index": cycle.day_index,
            "total_days": cycle.total_days,
            "days_remaining": cycle.days_remaining,
            "today": today_str,
            "status_text": cycle_engine.cycle_status_text(cycle, inflow_type),
            "progress_pct": min(100, int((cycle.day_index / max(1, cycle.total_days)) * 100)),
        },
        "settings": {
            "payday_day_of_month": payday_day,
            "inflow_type": inflow_type,
            "expected_amount": float(expected_amount),
            "currency": settings.get("currency", "₹"),
        },
        "metrics": {
            "safe_daily_limit": float(metrics.safe_daily_limit),
            "usable_pool": float(metrics.usable_pool),
            "true_disposable": float(metrics.true_disposable),
            "daily_allowance": float(metrics.daily_allowance),
            "emergency_reserve": float(metrics.emergency_reserve),
            "fixed_expenses": float(metrics.fixed_expenses),
            "total_money": float(metrics.total_money),
            "spent_this_cycle": float(spent_this_cycle),
            "spent_today": float(spent_today),
            "today_allowance": float(daily_status["allowance"]),
            "today_remaining": float(daily_status["remaining"]),
            "is_over_budget_today": daily_status["over_budget"],
            "pending_emis": float(pending_emis),
            "feasible": metrics.feasible,
            "critical": metrics.critical,
            "formatted_safe_daily": format_inr(metrics.safe_daily_limit),
            "formatted_usable": format_inr(metrics.usable_pool),
            "formatted_spent_cycle": format_inr(spent_this_cycle),
            "formatted_spent_today": format_inr(spent_today),
        },
        "run_rate": {
            "daily_rate": float(run_rate["daily_rate"]),
            "projected_total": float(run_rate["projected_total"]),
            "pace_label": run_rate["pace_label"],
            "formatted_projected": format_inr(run_rate["projected_total"]),
        },
        "persona": persona,
        "habit_tips": habit_tips,
        "category_surges": surges,
        "mom_deltas": mom_deltas[:5],
        "goals": goals_data,
        "liabilities": database.list_liabilities(status="ACTIVE"),
        "recent_expenses": all_expenses,
        "presets": {
            "categories": CATEGORIES,
            "payment_modes": PAYMENT_MODES,
            "inflow_types": INFLOW_TYPES,
            "inflow_labels": INFLOW_LABELS,
            "liability_types": LIABILITY_TYPES,
            "bnpl_presets": BNPL_PRESETS,
        },
    }


def app(environ: dict[str, Any], start_response: Callable) -> list[bytes]:
    """Standard WSGI entrypoint for Vercel Serverless Python."""
    query_string = environ.get("QUERY_STRING", "")
    query_params = parse_qs(query_string)

    # 1. Resolve path from explicit rewrite parameter or WSGI RAW_URI/REQUEST_URI
    rewrite_path = query_params.get("__path", [None])[0]
    raw_uri = environ.get("RAW_URI") or environ.get("REQUEST_URI")
    if rewrite_path and rewrite_path not in ("/", "/:path*"):
        path = "/" + rewrite_path.lstrip("/").split("?")[0].rstrip("/")
        if path == "":
            path = "/"
    elif raw_uri and not raw_uri.startswith("/api/index.py"):
        path = "/" + raw_uri.lstrip("/").split("?")[0].rstrip("/")
        if path == "":
            path = "/"
    else:
        # 2. Resolve from Vercel forward headers
        forwarded_path = (
            environ.get("HTTP_X_FORWARDED_URI")
            or environ.get("HTTP_X_REAL_PATH")
            or environ.get("HTTP_X_MATCHED_PATH")
        )
        if forwarded_path:
            path = forwarded_path.split("?")[0].rstrip("/") or "/"
        else:
            path = environ.get("PATH_INFO", "/").rstrip("/") or "/"

    if path in ("/api/index.py", "/api/index", "/api"):
        path = "/"

    method = environ.get("REQUEST_METHOD", "GET").upper()

    def respond(status: str, content_type: str, body: bytes) -> list[bytes]:
        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-cache, no-store, must-revalidate"),
            ("Access-Control-Allow-Origin", "*"),
            ("Access-Control-Allow-Headers", "Content-Type"),
            ("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS"),
        ]
        start_response(status, headers)
        return [body]

    def json_response(data: Any, status: str = "200 OK") -> list[bytes]:
        return respond(status, "application/json; charset=utf-8", _json_serialize(data).encode("utf-8"))

    if method == "OPTIONS":
        return respond("204 No Content", "text/plain", b"")

    # Read JSON body for POST/DELETE requests
    body_data: dict[str, Any] = {}
    if method in ("POST", "DELETE", "PUT", "PATCH"):
        try:
            content_length = int(environ.get("CONTENT_LENGTH", 0) or 0)
            if content_length > 0:
                raw_body = environ["wsgi.input"].read(content_length).decode("utf-8")
                body_data = json.loads(raw_body)
        except Exception:
            body_data = {}

    query_params = parse_qs(environ.get("QUERY_STRING", ""))

    try:
        # ─── API Routes ─────────────────────────────────────────────────────────

        if path == "/api/dashboard" and method == "GET":
            return json_response(_get_dashboard_data())

        if path == "/api/expenses" and method == "POST":
            amount = money(body_data.get("amount", 0))
            category = str(body_data.get("category", "Miscellaneous"))
            payment_mode = str(body_data.get("payment_mode", "UPI"))
            note = str(body_data.get("note", ""))
            is_planned = bool(body_data.get("is_planned", True))
            spent_on = str(body_data.get("spent_on") or date.today().isoformat())
            hour_now = datetime.now().hour

            # Check impulse velocity
            dash = _get_dashboard_data()
            safe_limit = Decimal(str(dash["metrics"]["safe_daily_limit"]))
            impulse = impulse_engine.check_impulse_flags(float(amount), category, safe_limit, hour_now)

            new_id = database.add_expense(
                plan_id=None,
                amount=amount,
                category=category,
                note=note,
                spent_on=spent_on,
                payment_mode=payment_mode,
                is_planned=is_planned,
                hour_logged=hour_now,
            )
            return json_response({"success": True, "id": new_id, "impulse": impulse})

        if path == "/api/expenses" and method == "DELETE":
            item_id = int(body_data.get("id") or query_params.get("id", [0])[0])
            if item_id > 0:
                database.delete_expense(item_id)
                return json_response({"success": True, "deleted": item_id})
            return json_response({"error": "Invalid expense id"}, "400 Bad Request")

        if path == "/api/liabilities" and method == "POST":
            name = str(body_data.get("name", "")).strip()
            l_type = str(body_data.get("type", "EMI"))
            emi_amount = money(body_data.get("emi_amount", 0))
            principal = money(body_data.get("principal", 0))
            rate = float(body_data.get("interest_rate", 0.0))
            tenure = int(body_data.get("tenure_remaining", 1))
            due_day = int(body_data.get("due_day", 1))
            note = str(body_data.get("note", ""))

            if not name or emi_amount <= 0:
                return json_response({"error": "Obligation name and monthly payment required"}, "400 Bad Request")

            new_id = database.add_liability(name, l_type, emi_amount, principal, rate, tenure, due_day, note)
            return json_response({"success": True, "id": new_id})

        if path == "/api/liabilities" and method == "DELETE":
            item_id = int(body_data.get("id") or query_params.get("id", [0])[0])
            if item_id > 0:
                database.delete_liability(item_id)
                return json_response({"success": True, "deleted": item_id})
            return json_response({"error": "Invalid liability id"}, "400 Bad Request")

        if path == "/api/liabilities/pay" and method == "POST":
            lia_id = int(body_data.get("id", 0))
            amt = money(body_data.get("amount", 0))
            if lia_id > 0 and amt > 0:
                pay_id = database.add_liability_payment(lia_id, amt)
                database.decrement_tenure(lia_id)
                return json_response({"success": True, "payment_id": pay_id})
            return json_response({"error": "Invalid payment data"}, "400 Bad Request")

        if path == "/api/savings" and method == "POST":
            name = str(body_data.get("name", "")).strip()
            target = money(body_data.get("target_amount", 0))
            deadline = body_data.get("deadline") or None
            priority = int(body_data.get("priority", 1))

            if not name or target <= 0:
                return json_response({"error": "Goal name and target amount required"}, "400 Bad Request")

            new_id = database.add_savings_goal(name, target, deadline, priority)
            return json_response({"success": True, "id": new_id})

        if path == "/api/savings/transfer" and method == "POST":
            goal_id = int(body_data.get("goal_id", 0))
            amount = money(body_data.get("amount", 0))
            if goal_id > 0 and amount > 0:
                t_id = database.add_savings_transfer(goal_id, amount)
                return json_response({"success": True, "transfer_id": t_id})
            return json_response({"error": "Invalid transfer parameters"}, "400 Bad Request")

        if path == "/api/savings" and method == "DELETE":
            item_id = int(body_data.get("id") or query_params.get("id", [0])[0])
            if item_id > 0:
                database.delete_savings_goal(item_id)
                return json_response({"success": True, "deleted": item_id})
            return json_response({"error": "Invalid goal id"}, "400 Bad Request")

        if path == "/api/inflows" and method == "POST":
            amount = money(body_data.get("amount", 0))
            in_type = str(body_data.get("type", "SALARY"))
            in_date = str(body_data.get("date") or date.today().isoformat())
            note = str(body_data.get("note", ""))

            if amount <= 0:
                return json_response({"error": "Inflow amount must be positive"}, "400 Bad Request")

            new_id = database.add_inflow(amount, in_type, in_date, None, None, note)
            return json_response({"success": True, "id": new_id})

        if path == "/api/settings" and method == "POST":
            payday_day = int(body_data.get("payday_day", 1))
            inflow_type = str(body_data.get("inflow_type", "SALARY"))
            expected_amount = money(body_data.get("expected_amount", 0))
            currency = str(body_data.get("currency", "₹"))

            database.save_settings(payday_day, inflow_type, expected_amount, currency)
            return json_response({"success": True})

        # ─── External Groq AI Guidance ─────────────────────────────────────────

        if path == "/api/guidance" and method in ("GET", "POST"):
            dash = _get_dashboard_data()
            metrics_obj = budget_engine.BudgetMetrics(
                total_money=Decimal(str(dash["metrics"]["total_money"])),
                fixed_expenses=Decimal(str(dash["metrics"]["fixed_expenses"])),
                emergency_reserve=Decimal(str(dash["metrics"]["emergency_reserve"])),
                usable_pool=Decimal(str(dash["metrics"]["usable_pool"])),
                daily_allowance=Decimal(str(dash["metrics"]["daily_allowance"])),
                days=int(dash["cycle"]["total_days"]),
                critical=bool(dash["metrics"]["critical"]),
                feasible=bool(dash["metrics"]["feasible"]),
                safe_daily_limit=Decimal(str(dash["metrics"]["safe_daily_limit"])),
                days_remaining=int(dash["cycle"]["days_remaining"]),
            )
            cycle_ctx = f"Day {dash['cycle']['day_index']}/{dash['cycle']['total_days']}, {dash['cycle']['days_remaining']} days left"
            try:
                advice, _ = llm_client.generate_advice(
                    metrics_obj,
                    cycle_context=cycle_ctx,
                    liabilities_context=f"Pending EMIs: ₹{dash['metrics']['pending_emis']}",
                )
                used_model = advice.get("_model", getattr(llm_client, "DEFAULT_MODEL", "groq-cloud"))
                return json_response({"success": True, "advice": advice, "model": used_model})
            except Exception as exc:
                logger.warning("Groq AI guidance failed, returning static guide: %s", exc)
                return json_response({"success": True, "advice": llm_client.STATIC_GUIDE, "model": "fallback", "error": str(exc)})

        # ─── Auth Routes ────────────────────────────────────────────────────────

        if path == "/api/auth/login" and method == "POST":
            email = str(body_data.get("email", "")).strip().lower()
            password = str(body_data.get("password", ""))
            if not email or not password:
                return json_response({"error": "Email and password are required"}, "400 Bad Request")

            auth_res = supabase_backend.sign_in_user(email, password)
            if auth_res.get("success"):
                return json_response(auth_res)
            # Fallback to local session if Supabase Auth is not enabled on the project
            return json_response({
                "success": True,
                "user": {"id": "local-user", "email": email, "mode": "local"},
                "message": "Local authentication successful."
            })

        if path == "/api/auth/signup" and method == "POST":
            email = str(body_data.get("email", "")).strip().lower()
            password = str(body_data.get("password", ""))
            name = str(body_data.get("name", "")).strip()
            if not email or not password:
                return json_response({"error": "Email and password are required"}, "400 Bad Request")

            auth_res = supabase_backend.sign_up_user(email, password, {"name": name} if name else None)
            if auth_res.get("success"):
                return json_response(auth_res)
            return json_response({
                "success": True,
                "user": {"id": "local-user", "email": email, "name": name, "mode": "local"},
                "message": "Local user registered."
            })

        if path == "/api/auth/logout" and method == "POST":
            return json_response({"success": True})

        # ─── Static Front-End Routing ──────────────────────────────────────────

        if path in ("/", "/index.html"):
            content = _read_html_file("index.html")
            if content:
                return respond("200 OK", "text/html; charset=utf-8", content)
            return respond("404 Not Found", "text/plain", b"index.html not found")

        if path in ("/login", "/login.html"):
            content = _read_html_file("login.html")
            if content:
                return respond("200 OK", "text/html; charset=utf-8", content)
            return respond("404 Not Found", "text/plain", b"login.html not found")

        # Fallback 404
        return json_response({"error": f"Route '{path}' not found"}, "404 Not Found")

    except Exception as exc:
        logger.exception("Error handling request %s: %s", path, exc)
        return json_response({"error": str(exc)}, "500 Internal Server Error")


# WSGI application alias
application = app


class handler(BaseHTTPRequestHandler):
    """Native BaseHTTPRequestHandler class required by Vercel Python Runtime."""

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_OPTIONS(self) -> None:
        self._dispatch("OPTIONS")

    def _dispatch(self, method: str) -> None:
        content_len = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(content_len) if content_len > 0 else b""

        parsed_url = urllib.parse.urlparse(self.path)
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": parsed_url.path,
            "QUERY_STRING": parsed_url.query,
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "80",
            "wsgi.url_scheme": "https",
            "wsgi.input": io.BytesIO(body),
            "wsgi.errors": io.StringIO(),
            "wsgi.version": (1, 0),
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
            "CONTENT_LENGTH": str(content_len),
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
        }
        for k, v in self.headers.items():
            environ[f"HTTP_{k.upper().replace('-', '_')}"] = v

        resp_status = "200 OK"
        resp_headers: list[tuple[str, str]] = []

        def start_response(status: str, headers: list[tuple[str, str]], exc_info: Any = None) -> None:
            nonlocal resp_status, resp_headers
            resp_status = status
            resp_headers = headers

        try:
            chunks = app(environ, start_response)
            code = int(resp_status.split()[0])
            self.send_response(code)
            for h_name, h_val in resp_headers:
                self.send_header(h_name, h_val)
            self.end_headers()
            for chunk in chunks:
                self.wfile.write(chunk)
        except Exception as exc:
            logger.exception("Error in handler dispatch: %s", exc)
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))

    def log_message(self, format: str, *args: Any) -> None:
        pass


if __name__ == "__main__":
    from wsgiref.simple_server import make_server
    port = 8000
    print(f"MonthEnd Native Vercel Server running on http://127.0.0.1:{port}")
    httpd = make_server("127.0.0.1", port, app)
    httpd.serve_forever()
