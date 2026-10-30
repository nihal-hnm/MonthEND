"""Local-only Ollama integration. No remote provider or telemetry is used.

Extended with cycle context, liability info, savings goals, and impulse patterns.
"""
import json
import re
import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"
MODEL = "qwen2.5:7b"


def get_available_model():
    """Detect available local Ollama model without remote internet."""
    global MODEL
    try:
        r = requests.get(OLLAMA_TAGS_URL, timeout=1.5)
        if r.status_code == 200:
            installed = [m.get("name", "") for m in r.json().get("models", [])]
            for candidate in ["qwen2.5:7b", "qwen2.5-coder:7b", "qwen2.5:latest", "gemma4:e4b"]:
                if candidate in installed:
                    return candidate
            if installed:
                return installed[0]
    except Exception:
        pass
    return MODEL

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
    pass


def build_prompt(metrics, pantry="", lifestyle="", location="",
                 critical=None, cycle_context=None, liabilities_context=None,
                 savings_context=None, impulse_context=None):
    """Build the LLM prompt with full financial context."""
    critical = metrics.critical if critical is None else critical
    austerity = (
        "CRITICAL: allowance is below ₹200/day. Focus on mess/tiffin, home-cooked bulk meals (dal-rice, khichdi), and eliminating all discretionary spend. "
        if critical else ""
    )

    data = {
        "days": metrics.days,
        "daily_allowance": str(metrics.daily_allowance),
        "usable_pool": str(metrics.usable_pool),
        "safe_daily_limit": str(getattr(metrics, 'safe_daily_limit', metrics.daily_allowance)),
        "pantry": pantry,
        "lifestyle": lifestyle,
        "region": location or "India",
    }

    # Add extended context (keep it compact for 2048 token window)
    if cycle_context:
        data["cycle"] = cycle_context
    if liabilities_context:
        data["emis"] = liabilities_context
    if savings_context:
        data["goals"] = savings_context
    if impulse_context:
        data["impulse_pattern"] = impulse_context

    return (
        "You are a pragmatic Indian personal finance assistant. Return one JSON object only with keys: "
        "summary (string), meal_plan (array of objects with meal and idea), "
        "pantry_tips (array of strings), spending_limits (array of strings), "
        "emergency_tradeoffs (array of strings), disclaimer (string). "
        "All amounts in ₹ (Indian Rupees). Use Indian context: mess/tiffin, kirana, "
        "UPI payments, Blinkit/Zepto, Zomato/Swiggy, metro/auto fares. "
        "Never do arithmetic or invent prices. "
        f"{austerity}"
        "Suggest practical, low-cost Indian meals. Never recommend a diet below 1,200 calories/day; "
        "if budget constraints make adequate calories difficult, state severe health risks and direct to "
        "local food assistance. Never advise skipping essential medication. "
        f"Context: {json.dumps(data, ensure_ascii=False)}"
    )


def _extract_json(text):
    text = text.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError):
            pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except (json.JSONDecodeError, TypeError):
            pass

    # If truncated at the end, attempt to close braces
    for suffix in ["}", '"]}', '"}]}', '"}]}']:
        try:
            return json.loads(text + suffix)
        except (json.JSONDecodeError, TypeError):
            continue

    raise ValueError(f"Could not extract valid JSON from response: {text[:200]}...")


def generate_advice(metrics, pantry="", lifestyle="", location="", timeout=90,
                    cycle_context=None, liabilities_context=None,
                    savings_context=None, impulse_context=None):
    """Generate personalized budget advice via local Ollama."""
    if metrics.usable_pool <= 0 or not metrics.feasible:
        raise LLMError("LLM generation is disabled because no positive usable budget remains.")
    if timeout <= 0:
        timeout = 90

    active_model = get_available_model()
    payload = {
        "model": active_model,
        "prompt": build_prompt(
            metrics, pantry, lifestyle, location,
            cycle_context=cycle_context,
            liabilities_context=liabilities_context,
            savings_context=savings_context,
            impulse_context=impulse_context,
        ),
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.2,
            "num_ctx": 2048,
            "num_predict": 1024,
            "num_gpu": 99,
        },
    }
    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
        response.raise_for_status()
        result = response.json()
        raw = result.get("response", "")
        advice = _extract_json(raw)
        if not isinstance(advice, dict):
            raise ValueError("The model returned non-dictionary JSON.")

        # Ensure all core sections are populated; fill any missing section with fallback
        for k in ["summary", "meal_plan", "pantry_tips", "spending_limits", "emergency_tradeoffs"]:
            if k not in advice or not advice[k]:
                advice[k] = STATIC_GUIDE.get(k, [])

        return advice, raw
    except requests.RequestException as exc:
        raise LLMError(f"Ollama is unavailable or timed out ({exc})") from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raw = locals().get("raw", "")
        raise LLMError(f"Could not validate the model response: {exc}\n{raw[:300]}") from exc
