# TOC Inventory (Matte Collection)

A small web app that turns daily Shopify exports into a **Theory-of-Constraints reorder list** —
no forecast, no black box. It reacts to what actually sold, buffers to your lead time, nets out
what's already on the way, and funds your winners until the cash budget runs out.

This is the **Phase 1 prototype**: pure Python + Streamlit, single user, runs on your laptop,
data stored in local CSV files. Phase 2 swaps storage for Supabase (database + sign-in) and deploys.

---

## Run it (first time)

```bash
# 1) make a clean environment (recommended)
python3 -m venv .venv
source .venv/bin/activate

# 2) install
pip install -r requirements.txt

# 3) launch
streamlit run app.py
```

It opens in your browser. In the sidebar:
1. **Data** → "Folder path" → point at your Shopify `exports` folder (the one with
   `daily_sales_history/` and `daily_inventory_history/`) → **Load from folder**.
   (Or choose "Upload CSVs" and drag files in.)
2. **Rules** → set cadence, lead times, Open-to-Buy, etc.

## What's in it

| Tab | What it does |
|---|---|
| 🛒 **Order** | The reorder list by colorway, funded by your Open-to-Buy. Download the **Factory PO** (this week's BUY rows) as CSV. |
| 🔬 **By size** | Every SKU with its own pace, in-stock days, reorder point, order, and buffer zone. |
| 🔎 **Audit a SKU** | Pick a SKU → chart of daily sales vs daily on-hand, so you can see the pace ignores stockout days. |
| 🚚 **Open POs** | Add/edit what's inbound. ETA-aware: a PO only offsets an order if it lands within lead + cadence. |
| ✅ **Receipts to confirm** | The app watches on-hand jumps between snapshots, matches them to open POs, and lets you **confirm received** in one click — so you never hunt for a line to delete. |
| 🆕 **New products** | New SKUs in the feed are flagged with a default cost; set real costs here. |
| 💲 **Unit costs** | Edit costs per product (drives the profit ranking). |

## The method (the rules)

1. Constraint = **cash** → fund winners first (Open-to-Buy).
2. Don't forecast — **react**: units ÷ the days actually in stock.
3. Buffer to the **lead time + cadence**, not a year.
4. Order to **real size demand**, not a fixed grid.
5. **Small and often.**
6. **Taper as it fades; liquidate the dead.**

## Files

```
app.py          # the Streamlit UI
engine.py       # the TOC math (pure functions, no Excel)
storage.py      # CSV persistence (POs, costs, assumptions) — swap to Supabase in Phase 2
data/           # your saved POs/costs/assumptions (git-ignored)
requirements.txt
```

## Roadmap

- **Phase 1 (this):** local app, CSV upload/folder, manual data refresh. ✅
- **Phase 2:** Supabase (database so data persists in the cloud + sign-in), deploy to Streamlit
  Community Cloud or Render from a GitHub repo. Then it's a real signed-in app.
- **Phase 3:** Shopify Admin API → automatic daily pulls (replace manual CSV refresh) + a scheduled job.

> Note: GitHub stores the **code**. The running app (with sign-in + database) lives on a host
> (Streamlit Cloud / Render) connected to the repo. GitHub Pages alone can't run this.
