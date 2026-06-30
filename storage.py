"""
Persistence for the app.

If Supabase is configured (SUPABASE_URL + SUPABASE_KEY in st.secrets) it uses the
cloud database, so your POs / costs / settings persist and are shared across devices.
If not, it falls back to local CSV files in ./data — so the app keeps working
exactly as before while you set Supabase up. Nothing else in the app changes.
"""
import os, json, uuid, io
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)
PO_PATH = os.path.join(DATA_DIR, "open_pos.csv")
COST_PATH = os.path.join(DATA_DIR, "unit_costs.csv")
ASSUMP_PATH = os.path.join(DATA_DIR, "assumptions.json")
SETUP_PATH = os.path.join(DATA_DIR, "setup.json")
EXPORTS_DIR = os.path.join(DATA_DIR, "exports")        # local fallback for stored uploads
EXPORTS_BUCKET = "exports"                              # Supabase Storage bucket name

PO_COLS = ["po_id", "product", "color", "size", "qty", "eta", "status", "note"]
# Four numbers + pack/MOQ (mirrors the Open-to-Buy Excel tool). One lead time for
# everything — no swim/apparel split, no max-cover cap.
DEFAULT_ASSUMPTIONS = dict(review=7, lead=21, default_cost=7.0, pack=1, otb=50000)

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

# ---------------------------------------------------------------- setup map (category + lead time)
def load_setup() -> dict:
    """product -> {'category': 'swim'|'apparel', 'lead': int}. Stored in app_settings row id=2."""
    c = _sb()
    if c:
        try:
            data = c.table("app_settings").select("data").eq("id", 2).execute().data
            if data and data[0].get("data"):
                return data[0]["data"]
        except Exception:
            pass
    if os.path.exists(SETUP_PATH):
        try:
            return json.load(open(SETUP_PATH))
        except Exception:
            return {}
    return {}

def save_setup(m: dict):
    c = _sb()
    if c:
        try:
            c.table("app_settings").upsert({"id": 2, "data": m}).execute(); return
        except Exception:
            pass
    json.dump(m, open(SETUP_PATH, "w"), indent=2)

# ---------------------------------------------------------------- uploaded-file persistence
# Stores the RAW Shopify CSVs so they reload automatically next session (no Shopify API needed).
# Uses Supabase Storage when configured, else a local ./data/exports folder.
def _ensure_bucket(c):
    try:
        c.storage.create_bucket(EXPORTS_BUCKET)
    except Exception:
        pass  # already exists (or no permission — caller handles)

def save_uploaded_files(files) -> bool:
    """Persist a list of uploaded files (Streamlit UploadedFile-like) by their filename.
    Same name overwrites (so re-uploading a day can't duplicate). Returns True if saved."""
    saved = False
    c = _sb()
    if c:
        try:
            _ensure_bucket(c)
            for f in files:
                name = getattr(f, "name", None)
                if not name:
                    continue
                data = f.getvalue() if hasattr(f, "getvalue") else f.read()
                if hasattr(f, "seek"):
                    f.seek(0)
                c.storage.from_(EXPORTS_BUCKET).upload(
                    path=name, file=data,
                    file_options={"content-type": "text/csv", "upsert": "true"})
                saved = True
            return saved
        except Exception:
            pass  # fall through to local
    # local fallback
    try:
        os.makedirs(EXPORTS_DIR, exist_ok=True)
        for f in files:
            name = getattr(f, "name", None)
            if not name:
                continue
            data = f.getvalue() if hasattr(f, "getvalue") else f.read()
            if hasattr(f, "seek"):
                f.seek(0)
            with open(os.path.join(EXPORTS_DIR, name), "wb") as out:
                out.write(data)
            saved = True
    except Exception:
        return False
    return saved

def list_stored_files() -> list:
    c = _sb()
    if c:
        try:
            items = c.storage.from_(EXPORTS_BUCKET).list()
            return [it["name"] for it in items if str(it.get("name", "")).lower().endswith(".csv")]
        except Exception:
            pass
    if os.path.isdir(EXPORTS_DIR):
        return [n for n in os.listdir(EXPORTS_DIR) if n.lower().endswith(".csv")]
    return []

def load_stored_bytes() -> list:
    """Return [(filename, bytes), ...] for every stored CSV, or [] if none/none-configured."""
    out = []
    c = _sb()
    if c:
        try:
            for name in list_stored_files():
                data = c.storage.from_(EXPORTS_BUCKET).download(name)
                out.append((name, data))
            return out
        except Exception:
            out = []
    if os.path.isdir(EXPORTS_DIR):
        try:
            for name in list_stored_files():
                with open(os.path.join(EXPORTS_DIR, name), "rb") as fh:
                    out.append((name, fh.read()))
        except Exception:
            return []
    return out

def clear_stored_files() -> bool:
    c = _sb()
    if c:
        try:
            names = list_stored_files()
            if names:
                c.storage.from_(EXPORTS_BUCKET).remove(names)
            return True
        except Exception:
            pass
    try:
        if os.path.isdir(EXPORTS_DIR):
            for n in os.listdir(EXPORTS_DIR):
                os.remove(os.path.join(EXPORTS_DIR, n))
        return True
    except Exception:
        return False
