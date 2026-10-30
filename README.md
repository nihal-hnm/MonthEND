# MONTHEND (मंथ-एंड)

### The Private, Local-First Financial Pacing & Anti-Impulse Engine for India

---

## OVERVIEW

MonthEnd is a private, air-gapped financial survival app designed specifically for Indian students, hostelites, and young working professionals. Instead of just recording past expenses like traditional apps, MonthEnd actively controls your burn rate to prevent the dreaded "month-end crunch."

---

## THE PROBLEM WE SOLVE

1. **The Calendar Trap:** Income arrives on custom dates (e.g., salary on the 5th, pocket money on the 10th), making standard 1st-to-30th budgeting misleading.
2. **Phantom Balances:** Bank balances look safe until pending BNPL dues (Simpl, LazyPay) and split expenses hit.
3. **Impulse Micro-Leaks:** High-frequency ₹150 UPI transfers, 10-minute quick commerce (Zepto, Blinkit), and food delivery (Zomato, Swiggy) drain money unnoticed.

---

## KEY FEATURES

* **Custom Inflow Cycles:**  
  Anchors budgeting to your actual payday (e.g., 5th of this month to 4th of next month) rather than standard calendar months.

* **True Disposable Income:**  
  Calculates actual spendable cash by factoring in pending dues:  
  `True Disposable = Total Funds - (Fixed Bills + Reserve + Pending BNPL/EMIs)`

* **Real-Time Safe Daily Limit:**  
  Dynamically calculates what you can spend today without running out of cash:  
  `Safe Daily Limit = (True Disposable - Spent So Far) / Days Remaining`

* **Anti-Impulse Defense Engine:**  
  - Flags single spends > 1.5x your daily ceiling.  
  - Warns about late-night ordering risks (11:00 PM – 4:00 AM).  
  - Enforces a 5-second cooldown period before logging high-velocity spends.

* **Local AI & Spending Personas:**  
  Categorizes spending behavior (e.g., "Weekend Spiker", "Mid-Month Burner") and offers hyper-local insights tailored for Indian everyday expenses (mess/tiffin, kirana, transit) powered by an on-device AI.

---

## PRIVACY & ARCHITECTURE

- **100% Offline & Air-Gapped:** Zero cloud dependencies, zero banking API links, and no SMS scraping. Your financial data never leaves your device.
- **Tech Stack:** Python 3.11+, Streamlit UI, SQLite Database, local LLM via Ollama `qwen2.5:7b`.
