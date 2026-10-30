"""Indian market category presets and payment mode definitions."""

# ---------- Expense categories ----------
CATEGORIES = {
    "Fixed / Living": [
        "PG / Hostel Fee",
        "Mess / Tiffin",
        "Rent",
        "Kirana (Groceries)",
        "Mobile / Wi-Fi Recharge",
        "Electricity / Water",
        "Gas (Cooking)",
        "Laundry",
    ],
    "Food & Dining": [
        "Zomato / Swiggy",
        "Blinkit / Zepto / Instamart",
        "Chai / Canteen",
        "Restaurant / Café",
        "Home Cooking Supplies",
    ],
    "Transport": [
        "Metro / Local Train",
        "Auto / Rickshaw",
        "Cab (Ola / Uber)",
        "Bus",
        "Fuel / Petrol",
    ],
    "Shopping & Lifestyle": [
        "Clothing / Accessories",
        "Electronics / Gadgets",
        "Personal Care / Grooming",
        "Subscriptions (OTT, Music)",
    ],
    "Education": [
        "Books / Stationery",
        "Course / Exam Fees",
        "Printing / Xerox",
    ],
    "Health": [
        "Medicine / Pharmacy",
        "Doctor / Clinic",
        "Gym / Fitness",
    ],
    "Social & Entertainment": [
        "Movies / Events",
        "Gifting",
        "UPI P2P Transfer",
        "Parties / Outings",
    ],
    "Other": [
        "Miscellaneous",
    ],
}

# Flat list for dropdowns
FLAT_CATEGORIES: list[str] = []
for _group, _items in CATEGORIES.items():
    FLAT_CATEGORIES.extend(_items)

# Categories considered discretionary (for impulse detection)
DISCRETIONARY_CATEGORIES = {
    "Zomato / Swiggy",
    "Blinkit / Zepto / Instamart",
    "Chai / Canteen",
    "Restaurant / Café",
    "Cab (Ola / Uber)",
    "Clothing / Accessories",
    "Electronics / Gadgets",
    "Personal Care / Grooming",
    "Subscriptions (OTT, Music)",
    "Movies / Events",
    "Gifting",
    "Parties / Outings",
    "Miscellaneous",
}

# Late-night impulse high-risk categories
LATE_NIGHT_RISK_CATEGORIES = {
    "Zomato / Swiggy",
    "Blinkit / Zepto / Instamart",
    "Clothing / Accessories",
    "Electronics / Gadgets",
}

# ---------- Payment modes ----------
PAYMENT_MODES = [
    "UPI",
    "Cash",
    "Debit Card",
    "Credit Card",
    "BNPL (Simpl / LazyPay)",
    "Net Banking",
    "Wallet",
]

# ---------- Inflow types ----------
INFLOW_TYPES = [
    "SALARY",
    "POCKET_MONEY",
    "STIPEND",
    "FREELANCE",
    "OTHER",
]

# Human-readable labels
INFLOW_LABELS = {
    "SALARY": "Salary",
    "POCKET_MONEY": "Pocket Money",
    "STIPEND": "Stipend",
    "FREELANCE": "Freelance Income",
    "OTHER": "Other Income",
}

# ---------- BNPL / liability presets ----------
LIABILITY_TYPES = [
    "EMI",
    "BNPL",
    "PEER_LOAN",
    "CREDIT_CARD_DUE",
    "STUDENT_LOAN",
    "DEVICE_EMI",
    "OTHER",
]

BNPL_PRESETS = {
    "Simpl": {"type": "BNPL", "interest_rate": 0.0, "note": "Pay next bill cycle"},
    "LazyPay": {"type": "BNPL", "interest_rate": 0.0, "note": "Pay by 3rd of next month"},
    "Amazon Pay Later": {"type": "BNPL", "interest_rate": 0.0, "note": "Pay by bill date"},
    "Slice Card": {"type": "BNPL", "interest_rate": 0.0, "note": "3-month no-cost EMI available"},
    "Custom EMI": {"type": "EMI", "interest_rate": 0.0, "note": ""},
    "Peer / Hand Loan": {"type": "PEER_LOAN", "interest_rate": 0.0, "note": ""},
}

