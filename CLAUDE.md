# CLAUDE.md — TOC Inventory app

Project context for Claude Code. The owner (Kyle) is a **coding beginner** — explain changes simply, and always show diffs before applying.

## What this is
A single-user web app that tells an 8-figure DTC apparel/swim brand (**Matte Collection**, the owner's client — data is real and confidential, don't expose it) **what inventory to reorder each week**, using **Theory of Constraints** math. Deployed on **Streamlit Community Cloud**, which auto-redeploys on every push to `main`.

Stack: **Streamlit** (UI) + **Supabase** (Postgres + Storage) + GitHub. No Shopify API yet — data comes from uploaded Shopify CSVs.

## 🚨 The #1 rule: push changed files TOGETHER
`app.py` and `engine.py` are tightly coupled — the UI calls functions and expects columns that live in the engine. **If you change one, you almost always change/redeploy the other.** Pushing one without the other causes live errors like `KeyError: "['SKU'] not in index"` or `module 'engine' has no attribute 'product_rollup'`. When committing, stage **all** changed files.

## Files
- **`app.py`** — Streamlit UI: cookie login, sidebar (load/save data + rules), top metrics + Data-health panel, and 8 tabs: Order (Product/Colorway/Size grain toggle), Audit a SKU, Inactive SKUs, Open POs, Receipts to confirm, New products, Unit costs, Setup.
- **`engine.py`** — pure-Python TOC engine. Key fns: `load_from_folder`, `load_from_uploads`, `load_from_named_bytes`, `build_sku_table`, `recommend(sku,A,pos,costs,asof,setup)`, `colorway_rollup`, `product_rollup`, `inactive_skus`, `detect_receipts`, `detect_new_products`, `value_summary`, `data_health`, `has_inventory`.
- **`storage.py`** — persistence. Supabase if `SUPABASE_URL`+`SUPABASE_KEY` are in `st.secrets`, else local CSV/JSON fallback. Persists POs, unit costs, assumptions, the Setup map, and (in a Storage bucket `exports`) the raw uploaded CSVs.
- **`requirements.txt`** — streamlit, pandas, numpy, supabase, extra-streamlit-components.
- **`.streamlit/config.toml`** — dark theme + `maxUploadSize=500`. **Do NOT** put secrets here.
- **`supabase_setup.sql`** — creates tables `open_pos`, `unit_costs`, `app_settings` (id=1 rules, id=2 setup map).

## The TOC math (engine)
- **Pace** = units sold ÷ days the SKU was actually *in stock*. If no in-stock days (DIS30=0, usually means inventory wasn't loaded), fall back to **÷30 calendar days** — never ÷1. (This was a real bug; keep the guard.)
- **Reorder point** = `min(pace × (lead + review), pace × max_cover)`.
- **Order** = `ceil(max(0, reorderPt − on_hand − valid_on_order) / pack) × pack`.
- **On-order** counts a PO only if its ETA lands within `lead + review` (ETA-aware netting).
- **Open-to-Buy**: rank colorways by **profit-velocity** (pace × margin), fund top-down until the cash cap → BUY / DEFER.
- **Category** (swim vs apparel → lead time) is inferred from the product title but can be overridden per-product in the **Setup tab** (`setup` map). Lead times come from the sidebar unless overridden.

## Persistence model
- POs / unit costs / assumptions / setup → Supabase tables (or local files).
- Uploaded sales/inventory CSVs → Supabase **Storage** bucket `exports`; auto-loaded on open, re-parsed each session. Re-uploading a same-named file overwrites (no duplicates). **Supabase free tier caps files at ~50MB** — the multi-year sales file (~49MB) is near that edge.
- Login: signed cookie via `extra-streamlit-components`, 10-min idle timeout, shared across tabs; falls back to an in-session password if the cookie component is unavailable.

## Secrets (set in Streamlit Cloud → Manage app → Settings → Secrets — NOT in the repo)
`APP_PASSWORD`, `SUPABASE_URL`, `SUPABASE_KEY` (the `sb_secret_…` key). `.streamlit/secrets.toml` is gitignored — never commit it.

## How to verify before pushing
- Run `python -m py_compile app.py engine.py storage.py`.
- Streamlit Cloud runs the real app (full pandas); a local sandbox may not. The true test is the deployed reload — load **both** sales and inventory; numbers should be sane (e.g. a jacket paces in the single digits/day, not hundreds).

## Deploy loop
Edit → review diff → commit → **push to `main`** → Streamlit Cloud redeploys (~1 min) → refresh the app. If it looks stale, Manage app → Reboot.

## Status / roadmap
Done: Batch 1 (correctness), 2 (grain toggle, inactive SKUs, SKU codes), 3 (saved uploads + cookie login), 4 (dark UI + Setup tab). Next: Phase B (Shopify API auto-pull), Phase C (multi-client Supabase Auth + `client_id` + Row-Level Security + roles).
