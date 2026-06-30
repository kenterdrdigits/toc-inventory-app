"""
TOC Inventory — Streamlit app.
Run:  streamlit run app.py

Simple flow with a persistent left-side menu:
  Start here · Sales · Inventory · Buy list · Audit a SKU · Open POs · Costs
Upload your Shopify exports (CSV), set five numbers, read the buy list. Data is
parsed + date-stamped once and stored as compact Parquet, so reloads are fast.
The smarts over a spreadsheet: pace is units sold ÷ days actually in stock.
"""
import os, time, hmac, hashlib
from datetime import datetime, timedelta, date as _date
import pandas as pd
import streamlit as st
import engine as E
import storage as S

st.set_page_config(page_title="TOC Inventory", layout="wide", page_icon="📦")

# ---------------- Apple-style DARK polish (CSS only — no app logic) ----------------
st.markdown("""<style>
html, body, [class*="css"], [data-testid] {
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", "Inter", system-ui, sans-serif;
  -webkit-font-smoothing: antialiased; letter-spacing: -0.01em;
}
.block-container { padding-top: 2.6rem; padding-bottom: 4rem; max-width: 1320px; }
h1 { font-size: 2.4rem; font-weight: 700; letter-spacing: -0.03em; margin-bottom: .35rem; color: #F5F5F7; }
h2 { font-size: 1.4rem; font-weight: 600; letter-spacing: -0.02em; margin-top: 1.4rem; color: #F5F5F7; }
h3 { font-size: 1.1rem; font-weight: 600; color: #C7C7CF; }
[data-testid="stCaptionContainer"], .stCaption { color: #8A8A93; font-size: .82rem; }
[data-testid="stMetric"] {
  background: #16161C; border: 1px solid #23232B; border-radius: 16px;
  padding: 18px 20px; box-shadow: 0 1px 3px rgba(0,0,0,.25);
}
[data-testid="stMetricLabel"] p {
  opacity: .6; font-size: .75rem; font-weight: 500;
  text-transform: uppercase; letter-spacing: .05em;
}
[data-testid="stMetricValue"] { font-size: 1.7rem; font-weight: 600; letter-spacing: -0.02em; }
section[data-testid="stSidebar"] { background: #121217; border-right: 1px solid #23232B; }
.stButton button, .stDownloadButton button {
  border-radius: 10px; font-weight: 500; border: 1px solid #2A2A33;
}
[data-baseweb="input"], [data-baseweb="select"] { border-radius: 10px; }
[data-testid="stDataFrame"] { border-radius: 12px; border: 1px solid #23232B; overflow: hidden; }
[data-testid="stExpander"] { border: 1px solid #23232B; border-radius: 14px; background: #15151B; }
[data-testid="stExpander"] summary { padding: 4px 2px; }
hr { margin: 1.2rem 0; opacity: .18; }
</style>""", unsafe_allow_html=True)

# ---------------- speed: cache the heavy math; reuse across reruns ----------------
@st.cache_data(show_spinner=False)
def _compute(sales, inv, asof, A, pos, costs):
    sku = E.build_sku_table(sales, inv, asof)
    rec = E.recommend(sku, A, pos, costs, asof)
    cw = E.colorway_rollup(rec, A["otb"])
    vs = E.value_summary(sku, costs, A["default_cost"])
    health = E.data_health(sales, inv, asof, costs, sku)
    return sku, rec, cw, vs, health

# ---------------- login: cookie-backed (survives refresh + shared across tabs, 10-min idle) ----------------
IDLE_SECONDS = 10 * 60

def _sign(exp, secret):
    return hmac.new(str(secret).encode(), str(exp).encode(), hashlib.sha256).hexdigest()[:32]

def _make_token(secret):
    exp = int(time.time()) + IDLE_SECONDS
    return f"{exp}.{_sign(exp, secret)}"

def _token_exp(tok, secret):
    try:
        e, sig = tok.split(".", 1); e = int(e)
        return e if _sign(e, secret) == sig else None
    except Exception:
        return None

def _cookie_mgr():
    try:
        import extra_streamlit_components as stx
        if "cookie_mgr" not in st.session_state:
            st.session_state["cookie_mgr"] = stx.CookieManager(key="toc_cookies")
        return st.session_state["cookie_mgr"]
    except Exception:
        return None

def check_password() -> bool:
    try:
        correct = st.secrets.get("APP_PASSWORD", None)
    except Exception:
        correct = None
    if not correct:
        st.error("No app password is set yet. Add APP_PASSWORD in Secrets "
                 "(Streamlit Cloud → Manage app → Settings → Secrets, "
                 "or locally in a file at .streamlit/secrets.toml).")
        return False

    cm = _cookie_mgr()
    if cm is not None:
        try:
            tok = cm.get("toc_auth")
        except Exception:
            tok = None
        if tok:
            exp = _token_exp(tok, correct)
            if exp and time.time() < exp:
                st.session_state["auth_ok"] = True
                if time.time() > exp - (IDLE_SECONDS - 60):
                    try:
                        cm.set("toc_auth", _make_token(correct), key="auth_refresh",
                               expires_at=datetime.now() + timedelta(hours=12))
                    except Exception:
                        pass
                return True
        elif not st.session_state.get("auth_ok") and not st.session_state.get("_cookie_settled"):
            st.session_state["_cookie_settled"] = True
            st.info("Loading…"); st.stop()

    if st.session_state.get("auth_ok"):
        return True

    st.markdown("### 🔒 TOC Inventory — sign in")
    pw = st.text_input("Password", type="password")
    if pw:
        if pw == correct:
            st.session_state["auth_ok"] = True
            if cm is not None:
                try:
                    cm.set("toc_auth", _make_token(correct), key="auth_login",
                           expires_at=datetime.now() + timedelta(hours=12))
                except Exception:
                    pass
            st.rerun()
        else:
            st.error("Incorrect password.")
    return False

if not check_password():
    st.stop()

# ---------------- settings + persisted small tables ----------------
A = S.load_assumptions()
costs = S.load_costs()
pos = S.load_pos()

# ---------------- load canonical datasets into the session (cold start only) ----------------
def _assemble_into_session(sales_df, inv_df):
    inv_df = inv_df if (inv_df is not None and len(inv_df)) else pd.DataFrame(columns=E.INV_COLS)
    st.session_state["data"] = E.assemble(sales_df, inv_df)

if "data" not in st.session_state:
    s = S.load_dataset("sales")
    if s is not None and len(s):
        _assemble_into_session(s, S.load_dataset("inventory"))
    else:
        # one-time migration: old raw-CSV blobs → canonical Parquet
        try:
            stored = S.load_stored_bytes()
            if stored:
                ls, li, _ = E.load_from_named_bytes(stored)
                ls = ls[[c for c in E.SALES_COLS if c in ls.columns]]
                li = li[[c for c in E.INV_COLS if c in li.columns]] if li is not None and len(li) else None
                S.save_dataset("sales", ls)
                if li is not None and len(li): S.save_dataset("inventory", li)
                _assemble_into_session(ls, li)
        except Exception:
            pass

HAS_DATA = "data" in st.session_state
if HAS_DATA:
    sales, inv, asof = st.session_state["data"]
    sku, rec, cw, vs, health = _compute(sales, inv, asof, A, pos, costs)
    asof_d = pd.to_datetime(asof).date()
    has_inv = E.has_inventory(inv)

def _short(d):
    if d is None or pd.isna(d): return "—"
    t = pd.to_datetime(d); return f"{t.month}/{t.day}/{t.strftime('%y')}"

def _need_data():
    st.info("No data yet. Add your Shopify CSVs on the **Sales** and **Inventory** views (left).")

# ---------------- shared upload widget (parses, date-stamps, merges, persists) ----------------
def _ingest(prepared):
    """prepared: list of {kind, df(full), date(None|str)}. Merge into canonical + persist."""
    s_all = S.load_dataset("sales")
    i_all = S.load_dataset("inventory")
    n_sales = n_inv = 0
    for p in prepared:
        if p["kind"] == "sales":
            nf = E.process_sales(p["df"], fallback_date=p["date"])
            s_all = E.merge_sales(s_all, nf); n_sales += len(nf)
        elif p["kind"] == "inventory":
            nf = E.process_inventory(p["df"], p["date"])
            i_all = E.merge_inventory(i_all, nf); n_inv += len(nf)
    ok = True
    if n_sales and s_all is not None: ok = S.save_dataset("sales", s_all) and ok
    if n_inv and i_all is not None and len(i_all): ok = S.save_dataset("inventory", i_all) and ok
    if s_all is not None and len(s_all):
        _assemble_into_session(s_all, i_all)
    _compute.clear()
    return ok, n_sales, n_inv

def _upload_widget(key):
    ups = st.file_uploader("Add CSV(s) — sales and/or inventory", accept_multiple_files=True,
                           type="csv", key=key)
    if not ups:
        return
    prepared_meta = []
    for f in ups:
        try:
            head = pd.read_csv(f, nrows=5); f.seek(0)
        except Exception as ex:
            st.warning(f"Couldn't read {f.name}: {ex}"); continue
        kind = E.classify_columns(head.columns)
        if kind is None:
            st.warning(f"**{f.name}** — unrecognized columns (need a Shopify sales or inventory export).")
            continue
        status = E.file_date_status(head, f.name, kind)
        if status == "per-row":
            date = None  # sales already carries per-row dates
            st.caption(f"📄 **{f.name}** → sales (dates read from the file)")
        elif status is None:
            picked = st.date_input(f"Snapshot date for **{f.name}**", value=_date.today(), key=f"date_{key}_{f.name}")
            date = str(picked)
            st.caption(f"📄 **{f.name}** → {kind} (you set the date)")
        else:
            date = status
            st.caption(f"📄 **{f.name}** → {kind} (date {status})")
        prepared_meta.append({"file": f, "kind": kind, "date": date})

    st.write("")  # spacing so the dropzone never overlaps the button
    if prepared_meta and st.button("⬇️ Load + save", type="primary", key=f"btn_{key}"):
        try:
            with st.spinner("Reading, date-stamping and saving…"):
                prepared = []
                for m in prepared_meta:
                    df = pd.read_csv(m["file"]); m["file"].seek(0)
                    prepared.append({"kind": m["kind"], "df": df, "date": m["date"]})
                ok, ns, ni = _ingest(prepared)
            if ok:
                st.success(f"Loaded and saved {ns:,} sales + {ni:,} inventory rows.")
            else:
                st.warning(f"Loaded {ns:,} sales + {ni:,} inventory rows for this session, but "
                           "saving to storage failed (connection or size). Try again or check Supabase.")
            st.rerun()
        except Exception as ex:
            st.error(f"Import failed: {ex}")

def _csv_download(label, df, filename, key):
    st.download_button(label, df.to_csv(index=False).encode(), filename, "text/csv", key=key)

# ---------------- sidebar: navigation (persists across reruns) ----------------
VIEWS = ["🏠 Start here", "🧾 Sales", "📦 Inventory", "🛒 Buy list",
         "🔎 Audit a SKU", "🚚 Open POs", "💲 Costs"]
with st.sidebar:
    st.markdown("### 📦 TOC Inventory")
    nav = st.radio("Navigate", VIEWS, key="nav", label_visibility="collapsed")
    st.divider()
    if HAS_DATA:
        st.caption(f"Window: {_short(health['sales_min'])} → {_short(health['sales_max'])}")
    if st.button("Log out", use_container_width=True):
        st.session_state["auth_ok"] = False
        _cm = _cookie_mgr()
        if _cm is not None:
            try: _cm.delete("toc_auth", key="auth_logout")
            except Exception: pass
        st.rerun()

# ================= Start here =================
if nav == VIEWS[0]:
    st.title("📦 TOC Inventory")
    st.write("Your weekly buy list — what to reorder, in priority order, funded to your budget.")

    st.subheader("How to use it")
    st.markdown(
        "1. **Set your numbers** below.\n"
        "2. **Add your data** on the **Sales** and **Inventory** views "
        "(Shopify *Total sales by product variant* + *Month-end inventory snapshot*).\n"
        "3. Open the **Buy list** — sorted by priority, funded top-down to your budget. "
        "Export anything to Excel from its view.")

    st.subheader("Your settings")
    s1, s2, s3 = st.columns(3)
    A["lead"] = s1.number_input("Lead time (days)", 1, 180, int(A.get("lead", 21)),
                                help="Days from placing an order to arrival.")
    A["review"] = s2.number_input("Order every (days)", 1, 60, int(A["review"]),
                                  help="How often you reorder. 7 = weekly.")
    A["otb"] = s3.number_input("Weekly order budget ($)", 0, 10_000_000, int(A["otb"]), step=5000,
                               help="Cash cap for this cycle. The buy list funds top-down until it's spent.")
    s4, s5, _ = st.columns(3)
    A["default_cost"] = s4.number_input("Default unit cost ($)", 0.0, 1000.0, float(A["default_cost"]),
                                        help="Used for any product without a real cost set (see Costs).")
    A["pack"] = s5.number_input("Pack / MOQ", 1, 1000, int(A["pack"]),
                                help="Order quantities round up to a multiple of this.")
    _keys = ("lead", "review", "otb", "default_cost", "pack")
    if {k: A.get(k) for k in _keys} != st.session_state.get("_last_assumptions"):
        S.save_assumptions(A)
        st.session_state["_last_assumptions"] = {k: A.get(k) for k in _keys}

    st.divider()
    st.subheader("Status")
    if not HAS_DATA:
        _need_data()
    else:
        st.caption(f"Sales window: **{_short(health['sales_min'])} → {_short(health['sales_max'])}**"
                   + ("" if has_inv else "  ·  ⚠️ no inventory loaded yet"))
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Active SKUs", f"{len(sku):,}")
        m2.metric("Inventory value", f"${vs['total']:,.0f}" if has_inv else "—")
        m3.metric("Dead stock", f"${vs['dead']:,.0f}" if has_inv else "—",
                  help=(f"{vs['dead_skus']} SKUs · {vs['dead_units']:,} units with no sales in 90 days"
                        if has_inv else "Load inventory to compute."))
        m4.metric("This week's spend",
                  f"${cw.loc[cw['InBudget']=='BUY','OrderCost'].sum():,.0f} / ${A['otb']:,.0f}",
                  help=f"{int((cw['InBudget']=='BUY').sum())} colorways funded by your budget.")
        if st.button("🗑️ Clear saved data"):
            S.clear_datasets(); st.session_state.pop("data", None)
            _compute.clear(); st.success("Cleared."); st.rerun()

# ================= Sales =================
elif nav == VIEWS[1]:
    st.subheader("Sales")
    st.caption("Upload your Shopify *Total sales by product variant* export. Each row keeps its "
               "date; re-uploading merges (same day + SKU overwrites).")
    _upload_widget("up_sales")
    st.divider()
    if not HAS_DATA:
        _need_data()
    else:
        view = sales.copy()
        view["Day"] = pd.to_datetime(view["Day"]).dt.date
        view = view[["Day", "Product", "Color", "Size", "Product variant SKU", "units", "ns"]].rename(
            columns={"Product variant SKU": "SKU", "units": "Units sold", "ns": "Net sales"})
        pick = st.selectbox("Filter to a product", ["(all products)"] + sorted(sales["Product"].unique()), key="sales_pick")
        if pick != "(all products)":
            view = view[view["Product"] == pick]
        st.caption(f"{len(view):,} rows · {_short(health['sales_min'])} → {_short(health['sales_max'])}")
        _csv_download("⬇️ Export sales (CSV)", view, "sales_export.csv", "dl_sales")
        st.dataframe(view.sort_values("Day", ascending=False), use_container_width=True, height=460, hide_index=True)

# ================= Inventory =================
elif nav == VIEWS[2]:
    st.subheader("Inventory")
    st.caption("Upload your *Month-end inventory snapshot*. If the file has no date, you'll be "
               "asked to set the snapshot date — it's stored with the data and shows in exports.")
    _upload_widget("up_inv")
    st.divider()
    if not HAS_DATA:
        _need_data()
    elif not has_inv:
        st.warning("No inventory loaded yet. Upload a *Month-end inventory snapshot* above.")
    else:
        iview = inv.copy()
        iview["date"] = pd.to_datetime(iview["date"]).dt.date
        iview = iview[["date", "Product", "Color", "Size", "oh", "cost"]].rename(
            columns={"date": "Snapshot date", "oh": "On hand", "cost": "Unit cost"})
        ipick = st.selectbox("Filter to a product", ["(all products)"] + sorted(inv["Product"].unique()), key="inv_pick")
        if ipick != "(all products)":
            iview = iview[iview["Product"] == ipick]
        st.caption(f"{len(iview):,} rows · snapshots {_short(health['inv_min'])} → {_short(health['inv_max'])}")
        _csv_download("⬇️ Export inventory (CSV)", iview, "inventory_export.csv", "dl_inv")
        st.dataframe(iview.sort_values(["Snapshot date", "Product"], ascending=[False, True]),
                     use_container_width=True, height=460, hide_index=True)

# ================= Buy list =================
elif nav == VIEWS[3]:
    st.subheader("Buy list")
    if not HAS_DATA:
        _need_data()
    else:
        if not has_inv:
            st.warning("Quantities assume **0 on-hand** until you load inventory — treat this as a preview.")
        st.caption("Sorted by priority and funded top-down to your weekly budget. "
                   "**Return/day** = pace × profit per unit. **BUY** = funded; **HOLD** = waits.")
        gc1, gc2 = st.columns([2, 3])
        grain = gc1.radio("View by", ["Colorway", "Size", "Product"], index=0, horizontal=True, key="grain")
        pick = gc2.selectbox("Filter to a product", ["(all products)"] + sorted(rec["Product"].unique()), key="buy_pick")
        rf = rec if pick == "(all products)" else rec[rec["Product"] == pick]

        if grain == "Product":
            out = E.product_rollup(rf, cw)
            cols = ["Product", "Colorways", "Pace", "OnHand", "OnOrder", "ReorderPt", "Order",
                    "XS", "S", "M", "L", "XL", "Oth", "ProfitVelocity", "OrderCost"]
            if "BuyColorways" in out.columns: cols.insert(2, "BuyColorways")
            out = out[cols].round({"Pace": 2, "ProfitVelocity": 1, "OrderCost": 0}).rename(columns={"ProfitVelocity": "Return/day"})
            st.caption("Totals across every colorway of each product. BUY/HOLD is decided at the colorway level.")
        elif grain == "Colorway":
            cwf = cw if pick == "(all products)" else cw[cw["Product"] == pick]
            out = cwf[["Product", "Color", "Pace", "OnHand", "OnOrder", "ReorderPt", "Order",
                       "XS", "S", "M", "L", "XL", "Oth", "ProfitVelocity", "OrderCost", "CumCost", "InBudget"]]\
                .round({"Pace": 2, "ProfitVelocity": 1, "OrderCost": 0, "CumCost": 0})\
                .rename(columns={"ProfitVelocity": "Return/day", "InBudget": "Order?"})
        else:  # Size
            sz = rf.merge(cw[["Product", "Color", "InBudget"]], on=["Product", "Color"], how="left")
            out = sz[["SKU", "Product", "Color", "Size", "Pace", "DIS30", "OnHand", "OnOrder", "ReorderPt",
                      "Order", "Zone", "InBudget", "UnitCost", "Price", "ProfitVelocity"]]\
                .round({"Pace": 2, "ProfitVelocity": 1})\
                .rename(columns={"ProfitVelocity": "Return/day", "InBudget": "Order?"})\
                .sort_values("Order", ascending=False)

        _csv_download("⬇️ Export this buy list (CSV)", out, f"buy_list_{grain.lower()}.csv", "dl_buy")
        st.dataframe(out, use_container_width=True, height=460, hide_index=True)

        st.divider()
        st.markdown("**Factory PO** — this week's funded BUY rows, in the shipping-list format")
        buy_sizes = rec.merge(cw[cw["InBudget"] == "BUY"][["Product", "Color"]], on=["Product", "Color"], how="inner")
        buy_sizes = buy_sizes[buy_sizes["Order"] > 0]
        d1, d2 = st.columns(2)
        po_sku = buy_sizes.assign(Date=str(asof_d))[["Date", "SKU", "Product", "Color", "Size", "Order"]]\
            .rename(columns={"Order": "Qty"}).sort_values(["Product", "Color", "Size"])
        with d1:
            _csv_download("⬇️ Factory PO — by SKU (codes)", po_sku, "factory_po_by_sku.csv", "dl_po_sku")
        buy = cw[cw["InBudget"] == "BUY"]
        factory = buy.assign(Date=str(asof_d))[
            ["Date", "Product", "Color", "XS", "S", "M", "L", "XL", "Oth", "Order"]].rename(columns={"Order": "Total"})
        with d2:
            _csv_download("⬇️ Factory PO — by colorway (size grid)", factory, "factory_po_by_colorway.csv", "dl_po_cw")

# ================= Audit a SKU =================
elif nav == VIEWS[4]:
    st.subheader("Audit a SKU")
    if not HAS_DATA:
        _need_data()
    else:
        st.caption("Pick a product, then a colorway, then a size. Pace divides units by days the SKU "
                   "was actually in stock, so stockouts don't make a fast seller look slow.")
        a1, a2, a3 = st.columns(3)
        prod = a1.selectbox("Product", sorted(rec["Product"].unique()), key="aud_prod")
        colors = sorted(rec[rec["Product"] == prod]["Color"].unique())
        color = a2.selectbox("Colorway", colors, key="aud_color")
        sizes = sorted(rec[(rec["Product"] == prod) & (rec["Color"] == color)]["Size"].unique())
        size = a3.selectbox("Size", sizes, key="aud_size")
        row = rec[(rec["Product"] == prod) & (rec["Color"] == color) & (rec["Size"] == size)]
        if row.empty:
            st.info("No data for that combination.")
        else:
            r = row.iloc[0]
            st.caption(f"SKU code: **{r['SKU'] or '—'}**")
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("On hand (units)", int(r["OnHand"]))
            m2.metric("Days in stock (last 30d)", int(r["DIS30"]))
            m3.metric("Units sold (30d)", int(r["U30"]))
            m4.metric("Daily pace", f"{r['Pace']:.2f}")
            m5.metric("Order now", int(r["Order"]))
            k = r["key"]
            start = asof - pd.Timedelta(days=41)
            ds = sales[(sales["key"] == k) & (sales["Day"] >= start)].groupby("Day")["units"].sum()
            di = inv[inv["key"] == k].groupby("date")["oh"].sum() if not inv.empty else pd.Series(dtype=float)
            idx = pd.date_range(start, asof)
            chart = pd.DataFrame({"Units sold": ds.reindex(idx).fillna(0),
                                  "On hand": di.reindex(idx).ffill().fillna(0)}, index=idx)
            st.line_chart(chart)

# ================= Open POs =================
elif nav == VIEWS[5]:
    st.subheader("Open POs (what's on the way)")
    st.caption("Track inbound factory orders. The buy list nets a PO only if its ETA lands within "
               "lead + order cadence. Size must match the SKU's size so it nets correctly.")
    edit = pos[pos["status"].fillna("in_transit") == "in_transit"] if len(pos) else pos
    cfg = {"po_id": st.column_config.TextColumn("po_id", disabled=True)}
    if HAS_DATA:
        prod_opts = sorted(set(sku["Product"].unique()) | set(edit.get("product", pd.Series(dtype=str)).dropna().astype(str)))
        size_opts = sorted(set(sku["Size"].unique()) | set(edit.get("size", pd.Series(dtype=str)).dropna().astype(str)))
        cfg["product"] = st.column_config.SelectboxColumn("product", options=prod_opts)
        cfg["size"] = st.column_config.SelectboxColumn("size", options=size_opts)
    edited = st.data_editor(edit if len(edit) else pd.DataFrame(columns=S.PO_COLS),
                            num_rows="dynamic", use_container_width=True, hide_index=True, column_config=cfg)
    if st.button("💾 Save POs"):
        received = pos[pos["status"] == "received"] if len(pos) else pos
        S.save_pos(pd.concat([edited, received], ignore_index=True))
        st.success("Saved."); st.rerun()

    st.divider()
    st.markdown("**Receipts to confirm** — arrivals detected from your inventory snapshots")
    if not HAS_DATA:
        _need_data()
    else:
        cand = E.detect_receipts(inv, pos)
        if cand.empty:
            st.info("No likely receipts right now. (Needs at least two inventory snapshots + open POs.)")
        else:
            for _, c in cand.iterrows():
                cc1, cc2 = st.columns([5, 1])
                cc1.write(f"**{c['product']} · {c['color']} · {c['size']}** — on-hand jumped **+{int(c['jump'])}** "
                          f"on {c['jump_date']}, matches open PO of **{int(c['po_qty'])}**.")
                if cc2.button("Confirm received", key="rcv_" + str(c['po_id'])):
                    S.mark_received(c['po_id']); st.success("Marked received."); st.rerun()

# ================= Costs =================
elif nav == VIEWS[6]:
    st.subheader("Costs")
    st.caption("Unit cost per product drives the profit ranking and inventory value. "
               "Products with no cost set use the default until you fill them in.")
    if not HAS_DATA:
        _need_data()
    else:
        new = E.detect_new_products(sku, costs)
        if new:
            st.warning(f"⚠️ {len(new)} product(s) have **no cost set** and use the "
                       f"${A['default_cost']:.0f} default: " + ", ".join(new[:8]) + (" …" if len(new) > 8 else ""))
        allp = sorted(sku["Product"].unique())
        cd = pd.DataFrame({"product": allp,
                           "unit_cost": [costs.get(p, A["default_cost"]) for p in allp],
                           "cost set?": ["—" if p in new else "✓" for p in allp]})
        _csv_download("⬇️ Export costs (CSV)", cd, "unit_costs.csv", "dl_costs")
        e = st.data_editor(cd, use_container_width=True, hide_index=True, height=420,
                           column_config={"product": st.column_config.TextColumn("product", disabled=True),
                                          "cost set?": st.column_config.TextColumn("cost set?", disabled=True)})
        if st.button("💾 Save costs"):
            S.save_costs({r["product"]: float(r["unit_cost"]) for _, r in e.iterrows() if r["unit_cost"]})
            st.success("Saved."); st.rerun()

        st.divider()
        with st.expander("Inactive / dead stock (no sales in 90 days, no stock)"):
            _ck = ("inactive", str(asof), len(sales))
            if st.session_state.get("_inactive_key") != _ck:
                st.session_state["_inactive_df"] = E.inactive_skus(sales, inv, asof)
                st.session_state["_inactive_key"] = _ck
            inactive = st.session_state["_inactive_df"]
            if inactive.empty:
                st.success("No inactive SKUs — everything with history is either selling or in stock.")
            else:
                st.caption(f"{len(inactive):,} dormant SKUs, most-recently-sold first.")
                _csv_download("⬇️ Export inactive (CSV)", inactive, "inactive_skus.csv", "dl_inactive")
                st.dataframe(inactive[["SKU", "Product", "Color", "Size", "LastSold", "DaysSince",
                                       "LifetimeUnits", "OnHand"]],
                             use_container_width=True, height=300, hide_index=True)
