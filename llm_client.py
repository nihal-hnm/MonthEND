"""External AI Agent integration powered by Groq Cloud API (Llama 3.3 70B).

High-speed cloud inference replacing local Ollama execution:
- Uses Groq's LPUs for ultra-fast response times (~300ms)
- Native JSON mode enforcement
- Automatic credential resolution (os.environ, .env, st.secrets, fallback)
- Full financial context awareness (cycle status, EMIs, savings goals, impulse velocity)
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-oss-120b"
FALLBACK_MODELS = ["qwen/qwen3.8-27b", "openai/gpt-oss-20b"]

ENV_PATH = Path(__file__).resolve().parent / ".env"
CONFIG_PATH = Path(__file__).resolve().parent / "groq_config.json"

STATIC_GUIDE = {
    "summary": "Track every ₹ spent, protect your emergency reserve, and review your daily safe limit before each spend.",
    "meal_plan": [
        {"meal": "Breakfast", "idea": "Poha / upma / oats with milk – use pantry staples first."},
        {"meal": "Lunch", "idea": "Dal-rice / roti-sabzi from mess or home-cooked batch."},
        {"meal": "Dinner", "idea": "Khichdi / dal-chawal with seasonal vegetables."},
    ],
    "pantry_tips": [
        "Inventory food at home before ordering on Blinkit/Zepto.",
        "Buy kirana staples (atta, dal, rice, oil) monthly in bulk.",
        "Avoid quick-commerce for non-urgent items — markup is 15-30% higher.",
    ],
    "spending_limits": [
        "Check your safe daily limit before each spend.",
        "Use UPI transaction history as a daily expense log.",
        "Set Zomato/Swiggy weekly budget caps in-app.",
    ],
    "emergency_tradeoffs": [
        "Prioritize rent, recharge, essential medicine, and safe food.",
        "Contact landlord/PG early about hardship extensions.",
        "Use student discounts and campus facilities (canteen, gym, library).",
    ],
    "disclaimer": "General framework only; seek local support for urgent needs.",
}


class LLMError(RuntimeError):
    """Raised when the external Groq AI agent fails or cannot be reached."""
    pass


def get_groq_api_key() -> str:
    """Resolve Groq API key from environment, .env file, groq_config.json, or st.secrets."""
    # 1. Environment variable
    if os.environ.get("GROQ_API_KEY"):
        return os.environ["GROQ_API_KEY"].strip()

    # 2. Streamlit secrets (if available)
    try:
        import streamlit as st  # type: ignore
        if hasattr(st, "secrets") and "GROQ_API_KEY" in st.secrets:
            return str(st.secrets["GROQ_API_KEY"]).strip()
    except Exception:
        pass

    # 3. Read .env file
    if ENV_PATH.exists():
        try:
            with open(ENV_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("GROQ_API_KEY="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            return val
        except Exception as exc:
            logger.debug("Failed reading .env for GROQ_API_KEY: %s", exc)

    # 4. Read uncommitted groq_config.json (if present)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                val = data.get("api_key") or data.get("GROQ_API_KEY")
                if val:
                    return str(val).strip()
        except Exception as exc:
            logger.debug("Failed reading groq_config.json: %s", exc)

    return ""


def build_system_prompt(critical: bool = False) -> str:
    austerity = (
        "CRITICAL: Daily allowance is below ₹200/day. Focus on mess/tiffin, bulk dal-rice cooking, and eliminating all discretionary spend. "
        if critical else ""
    )
    return (
        "You are a pragmatic, direct personal finance advisor specializing in Indian cost of living. "
        "Return one valid JSON object only with the following keys:\n"
        "- summary (string): Actionable 2-sentence executive guidance.\n"
        "- meal_plan (array of objects with 'meal' and 'idea'): Practical low-cost Indian meals (breakfast, lunch, dinner).\n"
        "- pantry_tips (array of strings): High-impact bulk grocery/kirana savings tips.\n"
        "- spending_limits (array of strings): Specific rules to prevent impulse spending.\n"
        "- emergency_tradeoffs (array of strings): Clear priorities for bill payments and essential outflows.\n"
        "- disclaimer (string): Brief standard disclaimer.\n"
        "All figures in Indian Rupees (₹). Ground advice in everyday Indian realities: kirana staples, PG/mess fees, "
        "UPI payment habits, quick-commerce traps (Blinkit/Zepto), food delivery (Zomato/Swiggy), transit (Metro/Auto). "
        "Never advise skipping essential medical treatment or inadequate nutrition below 1,200 calories/day. "
        f"{austerity}"
    )


def build_user_context(metrics: Any, pantry: str = "", lifestyle: str = "", location: str = "",
                       cycle_context: Optional[str] = None, liabilities_context: Optional[str] = None,
                       savings_context: Optional[str] = None, impulse_context: Optional[str] = None) -> str:
    data: dict[str, Any] = {
        "days_remaining": getattr(metrics, "days_remaining", metrics.days),
        "total_days_in_cycle": metrics.days,
        "daily_allowance": str(metrics.daily_allowance),
        "safe_daily_limit": str(getattr(metrics, "safe_daily_limit", metrics.daily_allowance)),
        "usable_pool": str(metrics.usable_pool),
        "fixed_expenses": str(metrics.fixed_expenses),
        "emergency_reserve": str(metrics.emergency_reserve),
        "region": location or "India",
    }
    if pantry:
        data["pantry_at_home"] = pantry
    if lifestyle:
        data["lifestyle_notes"] = lifestyle
    if cycle_context:
        data["cycle_context"] = cycle_context
    if liabilities_context:
        data["active_emis"] = liabilities_context
    if savings_context:
        data["savings_goals"] = savings_context
    if impulse_context:
        data["impulse_history"] = impulse_context

    return f"Evaluate this financial situation and provide tailored guidance in JSON:\n{json.dumps(data, ensure_ascii=False)}"


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        res = json.loads(text)
        if isinstance(res, dict):
            return res
    except (json.JSONDecodeError, TypeError):
        pass

    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            res = json.loads(match.group(1))
            if isinstance(res, dict):
                return res
        except (json.JSONDecodeError, TypeError):
            pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            res = json.loads(match.group(0))
            if isinstance(res, dict):
                return res
        except (json.JSONDecodeError, TypeError):
            pass

    for suffix in ["}", '"]}', '"}]}', '"}]}']:
        try:
            res = json.loads(text + suffix)
            if isinstance(res, dict):
                return res
        except (json.JSONDecodeError, TypeError):
            continue

    raise ValueError(f"Could not parse valid JSON from response: {text[:200]}")


def generate_advice(metrics: Any, pantry: str = "", lifestyle: str = "", location: str = "", timeout: int = 30,
                    cycle_context: Optional[str] = None, liabilities_context: Optional[str] = None,
                    savings_context: Optional[str] = None, impulse_context: Optional[str] = None) -> tuple[dict[str, Any], str]:
    """Generate personalized budget guidance via Groq Cloud API."""
    if metrics.usable_pool <= 0 or not metrics.feasible:
        raise LLMError("AI generation is disabled because no positive usable budget remains.")

    api_key = get_groq_api_key()
    if not api_key:
        raise LLMError("Groq API key is not configured.")

    system_prompt = build_system_prompt(critical=bool(getattr(metrics, "critical", False)))
    user_context = build_user_context(
        metrics, pantry, lifestyle, location,
        cycle_context=cycle_context,
        liabilities_context=liabilities_context,
        savings_context=savings_context,
        impulse_context=impulse_context,
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload: dict[str, Any] = {
        "model": DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_context},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "max_tokens": 1200,
    }

    models_to_try = [DEFAULT_MODEL] + [m for m in FALLBACK_MODELS if m != DEFAULT_MODEL]
    last_error: Optional[Exception] = None
    response: Optional[requests.Response] = None
    used_model = DEFAULT_MODEL

    for model_name in models_to_try:
        payload["model"] = model_name
        try:
            resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=timeout)
            if resp.status_code in (400, 404, 429, 500, 502, 503):
                logger.info("Model %s returned HTTP %s, trying fallback...", model_name, resp.status_code)
                continue
            resp.raise_for_status()
            response = resp
            used_model = model_name
            break
        except requests.RequestException as exc:
            last_error = exc
            logger.warning("Groq request failed for model %s: %s", model_name, exc)
            continue

    if response is None:
        raise LLMError(f"Groq API connection error: {last_error or 'All candidate models failed'}")

    try:
        res_json = response.json()
        choices = res_json.get("choices", [])
        if not choices:
            raise ValueError("Groq returned an empty choice set.")

        raw_content = choices[0].get("message", {}).get("content", "")
        advice = _extract_json(raw_content)

        # Fill any missing section with standard fallback
        for key in ["summary", "meal_plan", "pantry_tips", "spending_limits", "emergency_tradeoffs"]:
            if key not in advice or not advice[key]:
                advice[key] = STATIC_GUIDE.get(key, [])

        advice["_model"] = used_model
        return advice, raw_content

    except requests.RequestException as exc:
        logger.warning("Groq API request failed: %s", exc)
        raise LLMError(f"Groq API connection error: {exc}") from exc
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("Failed parsing Groq response: %s", exc)
        raise LLMError(f"Could not parse Groq response: {exc}") from exc
