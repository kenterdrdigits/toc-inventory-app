"""
Simple CSV-file persistence for the prototype (single user, local).
Open POs, unit costs, and assumptions live as files in ./data so they survive
between runs and are easy to inspect/edit. When you deploy and add Supabase,
only this file changes — the rest of the app stays the same.
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

def load_pos() -> pd.DataFrame:
    if os.path.exists(PO_PATH):
        df = pd.read_csv(PO_PATH, dtype={"po_id": str})
        for c in PO_COLS:
            if c not in df.columns: df[c] = ""
        return df[PO_COLS]
    return pd.DataFrame(columns=PO_COLS)

def save_pos(df: pd.DataFrame):
    df = df.copy()
    # assign ids to any new rows, default status
    df["po_id"] = [pid if isinstance(pid, str) and pid.strip() else uuid.uuid4().hex[:8]
                   for pid in df.get("po_id", "")]
    df["status"] = df["status"].fillna("").replace("", "in_transit")
    df[PO_COLS].to_csv(PO_PATH, index=False)

def mark_received(po_id: str):
    df = load_pos()
    df.loc[df["po_id"] == po_id, "status"] = "received"
    df.to_csv(PO_PATH, index=False)

def load_costs() -> dict:
    if os.path.exists(COST_PATH):
        df = pd.read_csv(COST_PATH)
        return {str(r["product"]): float(r["unit_cost"]) for _, r in df.iterrows()
                if pd.notna(r["unit_cost"]) and float(r["unit_cost"]) > 0}
    return {}

def save_costs(costs: dict):
    pd.DataFrame([{"product": k, "unit_cost": v} for k, v in costs.items()]).to_csv(COST_PATH, index=False)

def load_assumptions() -> dict:
    if os.path.exists(ASSUMP_PATH):
        a = dict(DEFAULT_ASSUMPTIONS); a.update(json.load(open(ASSUMP_PATH))); return a
    return dict(DEFAULT_ASSUMPTIONS)

def save_assumptions(a: dict):
    json.dump(a, open(ASSUMP_PATH, "w"), indent=2)
