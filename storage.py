"""
Persistence for the app.

If Supabase is configured (SUPABASE_URL + SUPABASE_KEY in st.secrets) it uses the
cloud database, so your POs / costs / settings persist and are shared across devices.
If not, it falls back to local CSV files in ./data — so the app keeps working
exactly as before while you set Supabase up. Nothing else in the app changes.
"""
import os, json, uuid
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)
PO_PATH = os.path.join(DATA_DIR, "open_pos.csv")
COST_PATH = os.path.join(DATA_DIR, "unit_costs.csv")
ASSUMP_PATH = os.path.join(DATA_DIR, "assumptions.json")

PO_COLS = ["po_id", "product", "color", "size", "qty", "eta", "status", "note"]
DEFAULT_ASSUMPTIONS = dict(review=7, lead_swim=14, lead_apparel=21, max_cover=30,
                           default_cost=7.0, pack=1, otb=50000)

# ---------------------------------------------------------------- Supabase client
_cache = "unset"
def _sb():
    """Return a Supabase client if configured, else None (use CSV)."""
    global _cache
    if _cache != "unset":
        return _cache
    client = None
    try:
        import streamlit as st
        url = st.secrets.get("SUPABASE_URL", None)
        key = st.secrets.get("SUPABASE_KEY", None)
        if url and key:
            from supabase import create_client
            client = create_client(url, key)
    except Exception:
        client = None
    _cache = client
    return client

def _clean_rows(df):
    rows = df.to_dict("records")
    for r in rows:
        for k, v in r.items():
            if pd.isna(v):
                r[k] = None
    return rows

# ---------------------------------------------------------------- open POs
def load_pos() -> pd.DataFrame:
    c = _sb()
    if c:
        try:
            data = c.table("open_pos").select("*").execute().data
            df = pd.DataFrame(data)
            for col in PO_COLS:
                if col not in df.columns: df[col] = ""
            return df[PO_COLS] if len(df) else pd.DataFrame(columns=PO_COLS)
        except Exception:
            pass
    if os.path.exists(PO_PATH):
        df = pd.read_csv(PO_PATH, dtype={"po_id": str})
        for col in PO_COLS:
            if col not in df.columns: df[col] = ""
        return df[PO_COLS]
    return pd.DataFrame(columns=PO_COLS)

def save_pos(df: pd.DataFrame):
    df = df.copy()
    df["po_id"] = [pid if isinstance(pid, str) and pid.strip() else uuid.uuid4().hex[:8]
                   for pid in df.get("po_id", "")]
    df["status"] = df["status"].fillna("").replace("", "in_transit")
    df = df[PO_COLS]
    c = _sb()
    if c:
        try:
            c.table("open_pos").delete().neq("po_id", "__never__").execute()
            rows = _clean_rows(df)
            if rows: c.table("open_pos").insert(rows).execute()
            return
        except Exception:
            pass
    df.to_csv(PO_PATH, index=False)

def mark_received(po_id: str):
    c = _sb()
    if c:
        try:
            c.table("open_pos").update({"status": "received"}).eq("po_id", po_id).execute()
            return
        except Exception:
            pass
    if os.path.exists(PO_PATH):
        df = pd.read_csv(PO_PATH, dtype={"po_id": str})
        df.loc[df["po_id"] == po_id, "status"] = "received"
        df.to_csv(PO_PATH, index=False)

# ---------------------------------------------------------------- unit costs
def load_costs() -> dict:
    c = _sb()
    if c:
        try:
            data = c.table("unit_costs").select("*").execute().data
            return {str(r["product"]): float(r["unit_cost"]) for r in data
                    if r.get("unit_cost") not in (None, "") and float(r["unit_cost"]) > 0}
        except Exception:
            pass
    if os.path.exists(COST_PATH):
        df = pd.read_csv(COST_PATH)
        return {str(r["product"]): float(r["unit_cost"]) for _, r in df.iterrows()
                if pd.notna(r["unit_cost"]) and float(r["unit_cost"]) > 0}
    return {}

def save_costs(costs: dict):
    c = _sb()
    if c:
        try:
            c.table("unit_costs").delete().neq("product", "__never__").execute()
            rows = [{"product": k, "unit_cost": float(v)} for k, v in costs.items()]
            if rows: c.table("unit_costs").insert(rows).execute()
            return
        except Exception:
            pass
    pd.DataFrame([{"product": k, "unit_cost": v} for k, v in costs.items()]).to_csv(COST_PATH, index=False)

# ---------------------------------------------------------------- assumptions
def load_assumptions() -> dict:
    c = _sb()
    if c:
        try:
            data = c.table("app_settings").select("data").eq("id", 1).execute().data
            if data and data[0].get("data"):
                a = dict(DEFAULT_ASSUMPTIONS); a.update(data[0]["data"]); return a
        except Exception:
            pass
    if os.path.exists(ASSUMP_PATH):
        a = dict(DEFAULT_ASSUMPTIONS); a.update(json.load(open(ASSUMP_PATH))); return a
    return dict(DEFAULT_ASSUMPTIONS)

def save_assumptions(a: dict):
    c = _sb()
    if c:
        try:
            c.table("app_settings").upsert({"id": 1, "data": a}).execute(); return
        except Exception:
            pass
    json.dump(a, open(ASSUMP_PATH, "w"), indent=2)
