"""Indian currency formatting utilities.

All amounts display with ₹ and Indian grouping (en-IN: ₹1,50,000).
"""
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")
RUPEE = "₹"


def money(value) -> Decimal:
    """Convert user input to a paisa-precision Decimal without float drift."""
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def format_inr(amount, symbol: str = RUPEE) -> str:
    """Format a number with Indian grouping: ₹1,50,000.00

    Indian grouping places the first comma after three digits from the right,
    then every two digits thereafter (lakh, crore, etc.).
    """
    amt = money(amount)
    negative = amt < 0
    amt = abs(amt)

    integer_part = int(amt)
    decimal_part = f"{amt - integer_part:.2f}"[1:]  # .XX

    s = str(integer_part)
    if len(s) <= 3:
        formatted = s
    else:
        # Last 3 digits
        last3 = s[-3:]
        rest = s[:-3]
        # Group rest in pairs from right
        groups = []
        while rest:
            groups.append(rest[-2:])
            rest = rest[:-2]
        groups.reverse()
        formatted = ",".join(groups) + "," + last3

    sign = "-" if negative else ""
    return f"{sign}{symbol}{formatted}{decimal_part}"


def parse_inr(text: str) -> Decimal:
    """Parse a string like '₹1,50,000.00' or '150000' to Decimal."""
    cleaned = str(text).replace(RUPEE, "").replace(",", "").replace(" ", "").strip()
    if not cleaned:
        return Decimal("0.00")
    return money(cleaned)

