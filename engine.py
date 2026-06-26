"""
TOC inventory engine — pure Python, no Excel.
Reads Shopify CSV exports (sales + month-end inventory snapshots), computes the
size-level reorder recommendation, the colorway roll-up, ETA-aware PO netting,
receipt detection, and new-product detection.

This is the same logic as the spreadsheet engine, refactored into functions the
Streamlit app calls. Returns pandas DataFrames.
"""
from __future__ import annotations
import pandas as pd, numpy as np, re, glob, os, datetime, io

SIZE_BUCKETS = ['XS', 'S', 'M', 'L', 'XL', 'Oth']
_SM = {'xs':'XS','x-small':'XS','extra small':'XS','s':'S','small':'S','m':'M','medium':'M',
       'l':'L','large':'L','xl':'XL','x-large':'XL','extra large':'XL'}
SWIM = re.compile(r'one piece|bikini|monokini|cover up|cover-up|sarong|swim|tankini|crossflow tank', re.I)
NONMERCH = re.compile(r'guide|package protection|route|mystery box|gift card|store credit|\btip\b|sample|donation|shipping', re.I)

def clean_size(v): return re.sub(r'\s+', ' ', str(v)).strip().upper()[:8]
def size_bucket(v): return _SM.get(re.sub(r'\s+', ' ', str(v)).strip().lower(), 'Oth')
def split_product_color(title):
    t = re.sub(r'\s+', ' ', str(title)).strip(); t = re.sub(r'\s*-\s*', ' - ', t)
    if ' - ' in t:
        p, c = t.rsplit(' - ', 1); return p.strip(), c.strip()
    return t.strip(), '(single)'

# ---------------------------------------------------------------- loaders
def _read_csv(obj):
    """obj can be a filepath or a file-like (Streamlit UploadedFile)."""
    return pd.read_csv(obj)

def load_sales_from_frames(frames: list[pd.DataFrame]) -> tuple[pd.DataFrame, pd.Timestamp]:
    """frames: list of sales DataFrames (the big multi-year file and/or single-day files).
    Single-day Shopify exports have no 'Day' column — caller must add it (see load_from_folder)."""
    df = pd.concat(frames, ignore_index=True)
    df = df.dropna(subset=['Day'])
    df['Day'] = pd.to_datetime(df['Day'])
    df = df.drop_duplicates(subset=['Day', 'Product variant SKU'], keep='last')
    asof = df['Day'].max()
    df['units'] = pd.to_numeric(df['Net items sold'], errors='coerce').fillna(0)
    df['ns'] = pd.to_numeric(df['Net sales'], errors='coerce').fillna(0)
    pc = df['Product title'].map(split_product_color)
    df['Product'] = [a for a, b in pc]; df['Color'] = [b for a, b in pc]
    df['Size'] = df['Product variant title'].map(clean_size)
    df['key'] = df['Product'] + ' | ' + df['Color'] + ' | ' + df['Size']
    df['cat'] = np.where(df['Product'].str.contains(SWIM, na=False), 'swim', 'apparel')
    return df, asof

def _inv_frame(df: pd.DataFrame, date) -> pd.DataFrame:
    df = df.copy()
    df['oh'] = pd.to_numeric(df['Ending inventory units'], errors='coerce').fillna(0)
    df['cost'] = pd.to_numeric(df.get('Inventory item cost', np.nan), errors='coerce')
    pc = df['Product title'].map(split_product_color)
    df['Product'] = [a for a, b in pc]; df['Color'] = [b for a, b in pc]
    df['Size'] = df['Product variant title'].map(clean_size)
    df['key'] = df['Product'] + ' | ' + df['Color'] + ' | ' + df['Size']
    df['date'] = pd.to_datetime(date)
    return df[['date', 'key', 'Product', 'Color', 'Size', 'oh', 'cost']]

# ---- folder loader (the practical path: point at the exports folder) ----
def load_from_folder(exports_dir: str):
    sfiles = glob.glob(os.path.join(exports_dir, 'daily_sales_history', 'Total sales by product variant*.csv'))
    ranges, singles = [], []
    for f in sfiles:
        ds = re.findall(r'(\d{4}-\d{2}-\d{2})', os.path.basename(f))
        if len(ds) >= 2 and ds[0] != ds[1]: ranges.append((ds[1], f))
        elif len(ds) >= 2: singles.append((ds[0], f))
    if not ranges:
        raise FileNotFoundError(f"No multi-day sales file in {exports_dir}/daily_sales_history/")
    ranges.sort(); big, big_end = ranges[-1][1], ranges[-1][0]
    cols = ['Day','Product title','Product variant title','Product variant SKU','Net items sold','Net sales']
    frames = [pd.read_csv(big, usecols=cols)]
    for d, f in singles:
        if d > big_end:
            t = pd.read_csv(f, usecols=[c for c in cols if c != 'Day']); t['Day'] = d; frames.append(t)
    sales, asof = load_sales_from_frames(frames)

    invfiles = []
    for f in glob.glob(os.path.join(exports_dir, '**', 'Month-end inventory snapshot*.csv'), recursive=True):
        ds = re.findall(r'(\d{4}-\d{2}-\d{2})', os.path.basename(f))
        if ds and ds[0] == ds[-1] and ds[0] >= (asof - pd.Timedelta(days=70)).strftime('%Y-%m-%d'):
            invfiles.append((ds[0], f))
    invfiles = sorted(set(invfiles))
    inv_frames = [_inv_frame(pd.read_csv(f), d) for d, f in invfiles]
    inv = pd.concat(inv_frames, ignore_index=True) if inv_frames else pd.DataFrame()
    return sales, inv, asof

# ---- upload loader (classify uploaded files by their columns) ----
def load_from_uploads(files):
    sales_frames, inv_frames = [], []
    for f in files:
        name = getattr(f, 'name', str(f))
        head = pd.read_csv(f, nrows=0); f.seek(0) if hasattr(f, 'seek') else None
        cols = set(head.columns)
        ds = re.findall(r'(\d{4}-\d{2}-\d{2})', name)
        if 'Ending inventory units' in cols:                      # inventory snapshot
            date = ds[-1] if ds else None
            inv_frames.append(_inv_frame(pd.read_csv(f), date))
        elif 'Net items sold' in cols:                            # sales
            t = pd.read_csv(f)
            if 'Day' not in t.columns and len(ds) == 2 and ds[0] == ds[1]:
                t['Day'] = ds[0]
            sales_frames.append(t)
    if not sales_frames:
        raise ValueError("No sales files found in the upload (need a 'Total sales by product variant' export).")
    sales, asof = load_sales_from_frames(sales_frames)
    inv = pd.concat(inv_frames, ignore_index=True) if inv_frames else pd.DataFrame()
    return sales, inv, asof

# ---------------------------------------------------------------- core build
def build_sku_table(sales: pd.DataFrame, inv: pd.DataFrame, asof: pd.Timestamp) -> pd.DataFrame:
    # De-dupe inventory: if the same snapshot date is loaded twice (e.g. the same
    # file uploaded twice, or a corrected re-export), keep the last copy so on-hand
    # is never doubled. Sales is already de-duped in load_sales_from_frames.
    if inv is not None and not inv.empty:
        inv = inv.drop_duplicates(subset=['date', 'key'], keep='last')
    def win(d): cut = asof - pd.Timedelta(days=d - 1); return sales[sales['Day'] >= cut]
    U = {d: win(d).groupby('key')['units'].sum() for d in (30, 60, 90)}
    NS90 = win(90).groupby('key')['ns'].sum()

    # latest on-hand + daily on-hand matrix (forward-filled) for in-stock days
    latest_oh = pd.Series(dtype=float); dis30 = pd.Series(dtype=float); latest_cost = pd.DataFrame()
    if not inv.empty:
        latest_date = inv['date'].max()
        latest = inv[inv['date'] == latest_date]
        latest_oh = latest.groupby('key')['oh'].sum()
        latest_cost = latest
        # daily matrix over last 42 days
        keys = sorted(set(U[90][U[90] > 0].index) | set(latest_oh[latest_oh > 0].index))
        didx = pd.date_range(asof - pd.Timedelta(days=41), asof)
        snaps = {d.strftime('%Y-%m-%d'): g.groupby('key')['oh'].sum()
                 for d, g in inv.groupby('date')}
        snap_dates = sorted(snaps)
        mat = pd.DataFrame(index=keys, columns=[d.strftime('%Y-%m-%d') for d in didx], dtype=float)
        for d in didx:
            ds = d.strftime('%Y-%m-%d'); use = [x for x in snap_dates if x <= ds]
            mat[ds] = snaps[use[-1]].reindex(keys) if use else np.nan
        mat = mat.ffill(axis=1).fillna(0)
        w30 = [d.strftime('%Y-%m-%d') for d in didx if d >= asof - pd.Timedelta(days=29)]
        dis30 = (mat[w30] > 0).sum(axis=1)

    keys = sorted(set(U[90][U[90] > 0].index) | set(latest_oh[latest_oh > 0].index))
    rows = []
    for k in keys:
        P, C, S = k.split(' | ', 2)
        if NONMERCH.search(P): continue
        u30, u60, u90 = float(U[30].get(k, 0)), float(U[60].get(k, 0)), float(U[90].get(k, 0))
        rows.append(dict(key=k, Product=P, Color=C, Size=S, Bucket=size_bucket(S),
                         Category='swim' if SWIM.search(P) else 'apparel',
                         OnHand=float(latest_oh.get(k, 0)), U30=u30, U60=u60, U90=u90,
                         DIS30=int(dis30.get(k, 0)),
                         Price=round(float(NS90.get(k, 0)) / u90, 2) if u90 > 0 else 0.0))
    return pd.DataFrame(rows)

def recommend(sku: pd.DataFrame, A: dict, open_pos: pd.DataFrame, costs: dict, asof) -> pd.DataFrame:
    """A = assumptions dict: review, lead_swim, lead_apparel, max_cover, default_cost, pack, otb."""
    df = sku.copy()
    df['Lead'] = np.where(df['Category'] == 'swim', A['lead_swim'], A['lead_apparel'])
    def pace(r):
        # Divide units by days the SKU was actually IN STOCK. If we have no
        # in-stock days (DIS30 == 0 — usually because no inventory was loaded),
        # fall back to calendar days so we never divide a whole month by 1 day.
        if r.U30 > 0: return r.U30 / (r.DIS30 if r.DIS30 > 0 else 30)
        if r.U60 > 0: return r.U60 / 60
        if r.U90 > 0: return r.U90 / 90
        return 0.0
    df['Pace'] = df.apply(pace, axis=1)
    df['ReorderPt'] = np.minimum(df['Pace'] * (df['Lead'] + A['review']), df['Pace'] * A['max_cover'])
    # ETA-aware on-order from open POs (status == in_transit, ETA within lead+review)
    if open_pos is not None and len(open_pos):
        op = open_pos.copy()
        op = op[op.get('status', 'in_transit').fillna('in_transit') == 'in_transit']
        op['eta'] = pd.to_datetime(op['eta'], errors='coerce')
        op['qty'] = pd.to_numeric(op['qty'], errors='coerce').fillna(0)
        op['key'] = op['product'].astype(str)+' | '+op['color'].astype(str)+' | '+op['size'].map(clean_size)
        def onorder(r):
            cutoff = asof + pd.Timedelta(days=int(r.Lead) + int(A['review']))
            m = op[(op['key'] == r.key) & ((op['eta'].isna()) | (op['eta'] <= cutoff))]
            return float(m['qty'].sum())
        df['OnOrder'] = df.apply(onorder, axis=1)
    else:
        df['OnOrder'] = 0.0
    pack = max(int(A['pack']), 1)
    df['Order'] = np.ceil(np.maximum(0, df['ReorderPt'] - df['OnHand'].clip(lower=0) - df['OnOrder']) / pack) * pack
    def zone(r):
        if r.ReorderPt <= 0: return '—'
        p = (max(r.OnHand, 0) + r.OnOrder) / r.ReorderPt
        return 'RED' if p < 1/3 else 'YELLOW' if p < 2/3 else 'GREEN' if p <= 1 else 'OVER'
    df['Zone'] = df.apply(zone, axis=1)
    df['UnitCost'] = df['Product'].map(lambda p: costs.get(p) if costs.get(p) else A['default_cost'])
    df['ProfitPerUnit'] = (df['Price'] - df['UnitCost']).clip(lower=0)
    df['ProfitVelocity'] = df['Pace'] * df['ProfitPerUnit']
    df['OrderCost'] = df['Order'] * df['UnitCost']
    return df

def colorway_rollup(rec: pd.DataFrame, otb: float) -> pd.DataFrame:
    cw = rec.groupby(['Product', 'Color']).agg(
        Pace=('Pace','sum'), OnHand=('OnHand','sum'), OnOrder=('OnOrder','sum'),
        ReorderPt=('ReorderPt','sum'), Order=('Order','sum'),
        ProfitVelocity=('ProfitVelocity','sum'), OrderCost=('OrderCost','sum')).reset_index()
    piv = rec.pivot_table(index=['Product','Color'], columns='Bucket', values='Order',
                          aggfunc='sum', fill_value=0).reset_index()
    for b in SIZE_BUCKETS:
        if b not in piv.columns: piv[b] = 0
    cw = cw.merge(piv[['Product','Color'] + SIZE_BUCKETS], on=['Product','Color'], how='left')
    cw = cw.sort_values(['Order','ProfitVelocity'], ascending=False)
    cw['CumCost'] = cw['OrderCost'].cumsum()
    cw['InBudget'] = np.where(cw['Order'] == 0, '—', np.where(cw['CumCost'] <= otb, 'BUY', 'DEFER'))
    return cw

# ---------------------------------------------------------------- reconciliation
def detect_receipts(inv: pd.DataFrame, open_pos: pd.DataFrame, tol_frac=0.25) -> pd.DataFrame:
    """Compare the two most recent inventory snapshots; positive on-hand jumps that match an
    open PO (same SKU, qty within tolerance) become candidate receipts to confirm."""
    if inv.empty or open_pos is None or len(open_pos) == 0:
        return pd.DataFrame(columns=['po_id','key','product','color','size','po_qty','jump','jump_date'])
    dates = sorted(inv['date'].unique())
    if len(dates) < 2:
        return pd.DataFrame(columns=['po_id','key','product','color','size','po_qty','jump','jump_date'])
    d_now, d_prev = dates[-1], dates[-2]
    now = inv[inv['date'] == d_now].groupby('key')['oh'].sum()
    prev = inv[inv['date'] == d_prev].groupby('key')['oh'].sum()
    delta = (now - prev.reindex(now.index).fillna(0)).fillna(0)
    op = open_pos.copy()
    op = op[op.get('status', 'in_transit').fillna('in_transit') == 'in_transit']
    op['qty'] = pd.to_numeric(op['qty'], errors='coerce').fillna(0)
    op['key'] = op['product'].astype(str)+' | '+op['color'].astype(str)+' | '+op['size'].map(clean_size)
    out = []
    for _, r in op.iterrows():
        jump = float(delta.get(r['key'], 0))
        if jump > 0 and abs(jump - r['qty']) <= max(r['qty'] * tol_frac, 5):
            out.append(dict(po_id=r.get('po_id', ''), key=r['key'], product=r['product'],
                            color=r['color'], size=r['size'], po_qty=r['qty'],
                            jump=jump, jump_date=str(pd.to_datetime(d_now).date())))
    return pd.DataFrame(out)

def detect_new_products(sku: pd.DataFrame, costs: dict) -> list[str]:
    return sorted(set(sku['Product']) - set(costs.keys()))

# ---------------------------------------------------------------- data health & value
def has_inventory(inv: pd.DataFrame) -> bool:
    return inv is not None and not inv.empty

def value_summary(sku: pd.DataFrame, costs: dict, default_cost: float) -> dict:
    """Total inventory value and dead-stock value (on-hand with no 90-day sales)."""
    if sku is None or sku.empty or 'OnHand' not in sku.columns:
        return dict(total=0.0, dead=0.0, dead_units=0, dead_skus=0, has_oh=False)
    df = sku.copy()
    df['oh'] = df['OnHand'].clip(lower=0)
    df['c'] = df['Product'].map(lambda p: costs.get(p) if costs.get(p) else default_cost)
    total = float((df['oh'] * df['c']).sum())
    dead = df[(df['oh'] > 0) & (df['U90'] <= 0)]
    return dict(total=total,
                dead=float((dead['oh'] * dead['c']).sum()),
                dead_units=int(dead['oh'].sum()),
                dead_skus=int(len(dead)),
                has_oh=bool(df['oh'].sum() > 0))

def data_health(sales: pd.DataFrame, inv: pd.DataFrame, asof,
                costs: dict | None = None, sku: pd.DataFrame | None = None) -> dict:
    """Date ranges, missing-day gaps (sales + inventory), and missing-cost count."""
    asof = pd.to_datetime(asof).normalize()
    out = {}
    sdays = pd.to_datetime(sales['Day']).dt.normalize()
    out['sales_min'], out['sales_max'] = sdays.min(), sdays.max()
    present_sales = set(sdays.unique())
    recent = pd.date_range(asof - pd.Timedelta(days=89), asof)
    out['sales_missing'] = [d for d in recent if d not in present_sales]

    if has_inventory(inv):
        idates = sorted(pd.to_datetime(pd.Series(inv['date'].unique())).dt.normalize().unique())
        out['inv_loaded'] = True
        out['inv_min'], out['inv_max'] = idates[0], idates[-1]
        out['inv_dates'] = idates
        gaps = [(idates[i] - idates[i - 1]).days for i in range(1, len(idates))]
        med = float(np.median(gaps)) if gaps else 1.0
        out['inv_cadence_days'] = med
        # Only flag "missing days" when snapshots are roughly DAILY. If they're
        # sparse (e.g. month-end), every non-snapshot day isn't a gap — it's the
        # cadence — so we report unusually large gaps instead of crying wolf.
        if gaps and med <= 2:
            present_inv = set(idates)
            out['inv_daily'] = True
            out['inv_missing'] = [d for d in pd.date_range(idates[0], idates[-1])
                                  if d not in present_inv]
            out['inv_big_gaps'] = []
        else:
            out['inv_daily'] = False
            out['inv_missing'] = []
            out['inv_big_gaps'] = [(idates[i - 1], idates[i]) for i in range(1, len(idates))
                                   if gaps and gaps[i - 1] > max(2 * med, med + 2)]
    else:
        out['inv_loaded'] = False
        out['inv_min'] = out['inv_max'] = None
        out['inv_missing'], out['inv_dates'], out['inv_big_gaps'] = [], [], []
        out['inv_daily'], out['inv_cadence_days'] = False, None

    if sku is not None and costs is not None and not sku.empty:
        out['missing_costs'] = sorted(set(sku['Product'].unique()) - set(costs.keys()))
    else:
        out['missing_costs'] = []
    return out
