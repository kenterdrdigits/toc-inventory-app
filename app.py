"""
TOC Inventory — Streamlit app.
Run:  streamlit run app.py

Simple, Excel-style flow that mirrors the Open-to-Buy tool:
  Start here → Sales → Inventory → Buy list → Audit a SKU → Open POs → Costs
Paste your Shopify exports, set four numbers, read the buy list. The one bit of
extra smarts over a spreadsheet: pace is units sold ÷ days actually in stock, so
stockouts don't make a fast seller look slow.
"""
import os, time, hmac, hashlib
from datetime import datetime, timedelta
import pandas as pd
import streamlit as st
import engine as E
import storage as S

st.set_page_config(page_title="TOC Inventory", layout="wide", page_icon="📦")

# ---------------- Apple-style light polish (CSS only — no app logic) ----------------
st.markdown("""<style>
html, body, [class*="css"], [data-testid] {
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", "Inter", system-ui, sans-serif;
  -webkit-font-smoothing: antialiased; letter-spacing: -0.01em;
}
.block-container { padding-top: 3rem; padding-bottom: 4rem; max-width: 1320px; }
h1 { font-size: 2.4rem; font-weight: 700; letter-spacing: -0.03em; margin-bottom: .35rem; color: #1D1D1F; }
h2 { font-size: 1.4rem; font-weight: 600; letter-spacing: -0.02em; margin-top: 1.6rem; color: #1D1D1F; }
h3 { font-size: 1.1rem; font-weight: 600; color: #6E6E73; }
[data-testid="stCaptionContainer"], .stCaption { color: #6E6E73; font-size: .82rem; }
[data-testid="stMetric"] {
  background: #FFFFFF; border: 1px solid #E5E5EA; border-radius: 16px;
  padding: 18px 20px; box-shadow: 0 1px 3px rgba(0,0,0,.06);
}
[data-testid="stMetricLabel"] p {
  opacity: .55; font-size: .75rem; font-weight: 500;
  text-transform: uppercase; letter-spacing: .05em;
}
[data-testid="stMetricValue"] { font-size: 1.7rem; font-weight: 600; letter-spacing: -0.02em; }
div[data-baseweb="tab-list"] {
  gap: 4px; background: #F5F5F7; padding: 5px;
  border-radius: 12px; border: 1px solid #E5E5EA;
}
button[data-baseweb="tab"] { border-radius: 9px; padding: 6px 14px; color: #6E6E73; }
button[data-baseweb="tab"][aria-selected="true"] {
  background: #FFFFFF; color: #1D1D1F; box-shadow: 0 1px 2px rgba(0,0,0,.08);
}
div[data-baseweb="tab-highlight"], div[data-baseweb="tab-border"] { background: transparent; }
.stButton button, .stDownloadButton button {
  border-radius: 10px; font-weight: 500; border: 1px solid #D2D2D7;
}
[data-baseweb="input"], [data-baseweb="select"] { border-radius: 10px; }
[data-testid="stDataFrame"], [data-testid="stTable"] {
  border-radius: 12px; border: 1px solid #E5E5EA; overflow: hidden;
}
[data-testid="stExpander"] { border: 1px solid #E5E5EA; border-radius: 14px; background: #FFFFFF; }
hr { margin: 1.4rem 0; opacity: .12; }
</style>""", unsafe_allow_html=True)

# ---------------- speed: cache the heavy work so reruns/refreshes are instant ----------------
@st.cache_data(show_spinner="Loading your saved data…")
def _load_named_cached(stored):
    return E.load_from_named_bytes(stored)

@st.cache_data(show_spinner="Crunching the numbers…")
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
    """An extra-streamlit-components CookieManager, or None if the component is unavailable."""
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
                if time.time() > exp - (IDLE_SECONDS - 60):   # slide idle window, throttled ~1/min
                    try:
                        cm.set("toc_auth", _make_token(correct), key="auth_refresh",
                               expires_at=datetime.now() + timedelta(hours=12))
                    except Exception:
                        pass
                return True
        elif not st.session_state.get("auth_ok") and not st.session_state.get("_cookie_settled"):
            st.session_state["_cookie_settled"] = True   # let the cookie component mount once
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

# ---------------- thin sidebar: log out only ----------------
with st.sidebar:
    st.markdown("### 📦 TOC Inventory")
    st.caption("Order what actually sells.")
    st.divider()
    if st.button("Log out", use_container_width=True):
        st.session_state["auth_ok"] = False
        _cm = _cookie_mgr()
        if _cm is not None:
            try: _cm.delete("toc_auth", key="auth_logout")
            except Exception: pass
        st.rerun()

# ---------------- load saved settings + persisted data ----------------
A = S.load_assumptions()
costs = S.load_costs()
pos = S.load_pos()

def _handle_upload(ups):
    """Save uploaded CSVs and (re)load the full set into session. Loader classifies
    sales vs inventory by their columns, so it doesn't matter which tab you drop them on."""
    S.save_uploaded_files(ups)
    stored = S.load_stored_bytes()
    st.session_state["data"] = (_load_named_cached(stored) if stored
                                else E.load_from_uploads(ups))

# auto-load saved uploads so a refresh / cold start remembers your data
if "data" not in st.session_state:
    _stored = S.load_stored_bytes()
    if _stored:
        try:
            st.session_state["data"] = _load_named_cached(_stored)
        except Exception:
            pass

HAS_DATA = "data" in st.session_state
if HAS_DATA:
    sales, inv, asof = st.session_state["data"]
    sku, rec, cw, vs, health = _compute(sales, inv, asof, A, pos, costs)
    asof_d = pd.to_datetime(asof).date()
    has_inv = E.has_inventory(inv)

def _short(d):
    if d is None: return "—"
    t = pd.to_datetime(d); return f"{t.month}/{t.day}/{t.strftime('%y')}"

def _need_data():
    st.info("⬅️ No data loaded yet. Add your Shopify CSVs on the **Sales** and **Inventory** tabs.")

# ---------------- tabs (Excel-style flow) ----------------
T = st.tabs(["🏠 Start here", "🧾 Sales", "📦 Inventory", "🛒 Buy list",
             "🔎 Audit a SKU", "🚚 Open POs", "💲 Costs"])

# ================= Start here =================
with T[0]:
    st.title("📦 TOC Inventory")
    st.write("Your weekly buy list — what to reorder, in priority order, funded to your budget.")

    st.subheader("How to use it")
    st.markdown(
        "1. **Set your numbers** below.\n"
        "2. **Add your data** on the **Sales** and **Inventory** tabs "
        "(Shopify *Total sales by product variant* + *Month-end inventory snapshot*).\n"
        "3. Open the **Buy list** — it's sorted by priority and funded top-down to your budget.")

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
                                        help="Used for any product without a real cost set (see Costs tab).")
    A["pack"] = s5.number_input("Pack / MOQ", 1, 1000, int(A["pack"]),
                                help="Order quantities round up to a multiple of this.")
    if {k: A.get(k) for k in ("lead", "review", "otb", "default_cost", "pack")} != \
       st.session_state.get("_last_assumptions"):
        S.save_assumptions(A)
        st.session_state["_last_assumptions"] = {k: A.get(k) for k in ("lead", "review", "otb", "default_cost", "pack")}

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
        _stored = S.list_stored_files()
        if _stored:
            st.caption(f"💾 {len(_stored)} file(s) saved — auto-loads when you reopen the app.")
            if st.button("🗑️ Clear saved data"):
                S.clear_stored_files(); st.session_state.pop("data", None)
                st.success("Cleared."); st.rerun()

# ================= Sales =================
with T[1]:
    st.subheader("Sales")
    st.caption("Drop your Shopify *Total sales by product variant* export here. "
               "Re-uploading a file with the same name just overwrites it.")
    ups = st.file_uploader("Add sales CSV(s)", accept_multiple_files=True, type="csv", key="up_sales")
    if ups and st.button("Load + save", type="primary", key="btn_sales"):
        try:
            _handle_upload(ups); st.success("Loaded and saved."); st.rerun()
        except Exception as ex:
            st.error(str(ex))
    if not HAS_DATA:
        _need_data()
    else:
        view = sales.copy()
        view["Day"] = pd.to_datetime(view["Day"]).dt.date
        code_col = "Product variant SKU"
        cols = ["Day", "Product", "Color", "Size"] + ([code_col] if code_col in view.columns else []) + ["units", "ns"]
        view = view[cols].rename(columns={code_col: "SKU", "units": "Units sold", "ns": "Net sales"})
        pick = st.selectbox("Filter to a product", ["(all products)"] + sorted(sales["Product"].unique()), key="sales_pick")
        if pick != "(all products)":
            view = view[view["Product"] == pick]
        st.caption(f"{len(view):,} rows · {_short(health['sales_min'])} → {_short(health['sales_max'])}")
        st.dataframe(view.sort_values("Day", ascending=False), use_container_width=True, height=460, hide_index=True)

# ================= Inventory =================
with T[2]:
    st.subheader("Inventory")
    st.caption("Drop your *Month-end inventory snapshot* export here. The on-hand drives "
               "accurate order quantities and the in-stock-days pace.")
    upi = st.file_uploader("Add inventory CSV(s)", accept_multiple_files=True, type="csv", key="up_inv")
    if upi and st.button("Load + save", type="primary", key="btn_inv"):
        try:
            _handle_upload(upi); st.success("Loaded and saved."); st.rerun()
        except Exception as ex:
            st.error(str(ex))
    if not HAS_DATA:
        _need_data()
    elif not has_inv:
        st.warning("No inventory loaded yet. Upload a *Month-end inventory snapshot* above.")
    else:
        iview = inv.copy()
        iview["date"] = pd.to_datetime(iview["date"]).dt.date
        iview = iview[["date", "Product", "Color", "Size", "oh", "cost"]].rename(
            columns={"date": "Snapshot", "oh": "On hand", "cost": "Unit cost"})
        ipick = st.selectbox("Filter to a product", ["(all products)"] + sorted(inv["Product"].unique()), key="inv_pick")
        if ipick != "(all products)":
            iview = iview[iview["Product"] == ipick]
        st.caption(f"{len(iview):,} rows · snapshots {_short(health['inv_min'])} → {_short(health['inv_max'])}")
        st.dataframe(iview.sort_values(["Snapshot", "Product"], ascending=[False, True]),
                     use_container_width=True, height=460, hide_index=True)

# ================= Buy list =================
with T[3]:
    st.subheader("Buy list")
    if not HAS_DATA:
        _need_data()
    else:
        if not has_inv:
            st.warning("Quantities assume **0 on-hand** until you load inventory — treat this as a preview.")
        st.caption("Sorted by priority and funded top-down to your weekly budget. "
                   "**Return/day** = pace × profit per unit. **BUY** = funded this cycle; **HOLD** = waits.")
        gc1, gc2 = st.columns([2, 3])
        grain = gc1.radio("View by", ["Colorway", "Size", "Product"], index=0, horizontal=True)
        pick = gc2.selectbox("Filter to a product", ["(all products)"] + sorted(rec["Product"].unique()))
        rf = rec if pick == "(all products)" else rec[rec["Product"] == pick]

        if grain == "Product":
            pr = E.product_rollup(rf, cw)
            cols = ["Product", "Colorways", "Pace", "OnHand", "OnOrder", "ReorderPt", "Order",
                    "XS", "S", "M", "L", "XL", "Oth", "ProfitVelocity", "OrderCost"]
            if "BuyColorways" in pr.columns: cols.insert(2, "BuyColorways")
            st.dataframe(pr[cols].round({"Pace": 2, "ProfitVelocity": 1, "OrderCost": 0})
                         .rename(columns={"ProfitVelocity": "Return/day"}),
                         use_container_width=True, height=460, hide_index=True)
            st.caption("Totals across every colorway of each product. BUY/HOLD is decided at the colorway level.")
        elif grain == "Colorway":
            cwf = cw if pick == "(all products)" else cw[cw["Product"] == pick]
            show = cwf[["Product", "Color", "Pace", "OnHand", "OnOrder", "ReorderPt", "Order",
                        "XS", "S", "M", "L", "XL", "Oth", "ProfitVelocity", "OrderCost", "CumCost", "InBudget"]].copy()
            st.dataframe(show.round({"Pace": 2, "ProfitVelocity": 1, "OrderCost": 0, "CumCost": 0})
                         .rename(columns={"ProfitVelocity": "Return/day", "InBudget": "Order?"}),
                         use_container_width=True, height=460, hide_index=True)
        else:  # Size
            sz = rf.merge(cw[["Product", "Color", "InBudget"]], on=["Product", "Color"], how="left")
            cols = ["SKU", "Product", "Color", "Size", "Pace", "DIS30", "OnHand", "OnOrder", "ReorderPt",
                    "Order", "Zone", "InBudget", "UnitCost", "Price", "ProfitVelocity"]
            st.dataframe(sz[cols].round({"Pace": 2, "ProfitVelocity": 1})
                         .rename(columns={"ProfitVelocity": "Return/day", "InBudget": "Order?"})
                         .sort_values("Order", ascending=False),
                         use_container_width=True, height=460, hide_index=True)

        st.divider()
        # ---- Factory PO downloads (this week's funded BUY rows) ----
        buy_sizes = rec.merge(cw[cw["InBudget"] == "BUY"][["Product", "Color"]],
                              on=["Product", "Color"], how="inner")
        buy_sizes = buy_sizes[buy_sizes["Order"] > 0]
        d1, d2 = st.columns(2)
        po_sku = buy_sizes.assign(Date=str(asof_d))[["Date", "SKU", "Product", "Color", "Size", "Order"]]\
            .rename(columns={"Order": "Qty"}).sort_values(["Product", "Color", "Size"])
        d1.download_button("⬇️ Factory PO — by SKU (with codes)",
                           po_sku.to_csv(index=False).encode(), "factory_po_by_sku.csv", "text/csv",
                           help="One row per SKU with its real Shopify code — the order you hand the factory.")
        buy = cw[cw["InBudget"] == "BUY"]
        factory = buy.assign(Date=str(asof_d))[
            ["Date", "Product", "Color", "XS", "S", "M", "L", "XL", "Oth", "Order"]].rename(columns={"Order": "Total"})
        d2.download_button("⬇️ Factory PO — by colorway (size grid)",
                           factory.to_csv(index=False).encode(), "factory_po_by_colorway.csv", "text/csv",
                           help="Size grid per colorway — matches the shipping-list format you send the manufacturer.")

# ================= Audit a SKU =================
with T[4]:
    st.subheader("Audit a SKU")
    if not HAS_DATA:
        _need_data()
    else:
        st.caption("Pick a product, then a colorway, then a size — and see whether the pace ignores stockout days.")
        a1, a2, a3 = st.columns(3)
        prod = a1.selectbox("Product", sorted(rec["Product"].unique()))
        colors = sorted(rec[rec["Product"] == prod]["Color"].unique())
        color = a2.selectbox("Colorway", colors)
        sizes = sorted(rec[(rec["Product"] == prod) & (rec["Color"] == color)]["Size"].unique())
        size = a3.selectbox("Size", sizes)
        row = rec[(rec["Product"] == prod) & (rec["Color"] == color) & (rec["Size"] == size)]
        if row.empty:
            st.info("No data for that combination.")
        else:
            r = row.iloc[0]
            st.caption(f"SKU code: **{r['SKU'] or '—'}**")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Days in stock (30)", int(r["DIS30"]))
            m2.metric("Units sold (30d)", int(r["U30"]))
            m3.metric("Daily pace", f"{r['Pace']:.2f}")
            m4.metric("Order now", int(r["Order"]))
            k = r["key"]
            start = asof - pd.Timedelta(days=41)
            ds = sales[(sales["key"] == k) & (sales["Day"] >= start)].groupby("Day")["units"].sum()
            di = inv[inv["key"] == k].groupby("date")["oh"].sum() if not inv.empty else pd.Series(dtype=float)
            idx = pd.date_range(start, asof)
            chart = pd.DataFrame({"Units sold": ds.reindex(idx).fillna(0),
                                  "On hand": di.reindex(idx).ffill().fillna(0)}, index=idx)
            st.line_chart(chart)

# ================= Open POs =================
with T[5]:
    st.subheader("Open POs (what's on the way)")
    st.caption("Track inbound factory orders. The buy list nets a PO only if its ETA lands within "
               "lead + order cadence. Size must match the SKU's size so it nets correctly.")
    edit = pos[pos["status"].fillna("in_transit") == "in_transit"] if len(pos) else pos
    # SKU pickers when we have data, so entries match the catalog and net correctly
    cfg = {"po_id": st.column_config.TextColumn("po_id", disabled=True)}
    if HAS_DATA:
        # union catalog values with any values already on existing POs, so the
        # dropdowns help without rejecting a row that was entered before.
        prod_opts = sorted(set(sku["Product"].unique()) | set(edit.get("product", pd.Series(dtype=str)).dropna().astype(str)))
        size_opts = sorted(set(sku["Size"].unique()) | set(edit.get("size", pd.Series(dtype=str)).dropna().astype(str)))
        cfg["product"] = st.column_config.SelectboxColumn("product", options=prod_opts)
        cfg["size"] = st.column_config.SelectboxColumn("size", options=size_opts)
    edited = st.data_editor(edit if len(edit) else pd.DataFrame(columns=S.PO_COLS),
                            num_rows="dynamic", use_container_width=True, hide_index=True,
                            column_config=cfg)
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
with T[6]:
    st.subheader("Costs")
    st.caption("Unit cost per product drives the profit ranking and inventory value. "
               "Products with no cost set use the default until you fill them in.")
    if not HAS_DATA:
        _need_data()
    else:
        new = E.detect_new_products(sku, costs)
        if new:
            st.warning(f"⚠️ {len(new)} product(s) have **no cost set** and are using the "
                       f"${A['default_cost']:.0f} default: " + ", ".join(new[:8]) + (" …" if len(new) > 8 else ""))
        allp = sorted(sku["Product"].unique())
        cd = pd.DataFrame({"product": allp,
                           "unit_cost": [costs.get(p, A["default_cost"]) for p in allp],
                           "cost set?": ["—" if p in new else "✓" for p in allp]})
        e = st.data_editor(cd, use_container_width=True, hide_index=True, height=440,
                           column_config={"product": st.column_config.TextColumn("product", disabled=True),
                                          "cost set?": st.column_config.TextColumn("cost set?", disabled=True)})
        if st.button("💾 Save costs"):
            S.save_costs({r["product"]: float(r["unit_cost"]) for _, r in e.iterrows() if r["unit_cost"]})
            st.success("Saved."); st.rerun()

        with st.expander("🗂️ Inactive / dead stock — sold before, quiet now (no sales 90d, no stock)"):
            _ck = ("inactive", str(asof), len(sales))
            if st.session_state.get("_inactive_key") != _ck:
                st.session_state["_inactive_df"] = E.inactive_skus(sales, inv, asof)
                st.session_state["_inactive_key"] = _ck
            inactive = st.session_state["_inactive_df"]
            if inactive.empty:
                st.success("No inactive SKUs — everything with history is either selling or in stock.")
            else:
                st.caption(f"{len(inactive):,} dormant SKUs, most-recently-sold first.")
                st.dataframe(inactive[["SKU", "Product", "Color", "Size", "LastSold", "DaysSince",
                                       "LifetimeUnits", "OnHand"]],
                             use_container_width=True, height=300, hide_index=True)
