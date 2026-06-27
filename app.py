"""
TOC Inventory — Streamlit prototype.
Run:  streamlit run app.py
Single user, CSV-driven. Point it at your Shopify exports folder (or upload files),
set the rules, manage POs, confirm receipts, review new products, and read the order.
"""
import os, time, hmac, hashlib
from datetime import datetime, timedelta
import pandas as pd
import streamlit as st
import engine as E
import storage as S

st.set_page_config(page_title="TOC Inventory", layout="wide", page_icon="📦")

# ---------------- Apple-ish dark polish ----------------
st.markdown("""<style>
html, body, [class*="css"] { font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Inter", system-ui, sans-serif; }
.block-container { padding-top: 2.2rem; max-width: 1320px; }
[data-testid="stMetric"] { background:#16161C; border:1px solid #23232B; border-radius:14px; padding:14px 16px; }
[data-testid="stMetricLabel"] p { opacity:.65; font-size:.8rem; }
div[data-baseweb="tab-list"] { gap: 2px; }
.stButton button, .stDownloadButton button { border-radius:10px; }
hr { margin:.8rem 0; opacity:.25; }
</style>""", unsafe_allow_html=True)

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
    # 1) cookie login — remembered across refresh; shared across browser tabs
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

    # 2) in-session fallback (and the whole story if the cookie component is unavailable)
    if st.session_state.get("auth_ok"):
        return True

    # 3) password form
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

st.title("📦 TOC Inventory — order what actually sells")

# ---------------- sidebar: data + rules ----------------
A = S.load_assumptions()
with st.sidebar:
    st.header("1 · Data")
    mode = st.radio("Source", ["Folder path", "Upload CSVs"], horizontal=True)
    sales = inv = asof = None
    if mode == "Folder path":
        default = "/Users/kylekent/Documents/Claude/Projects/Justina McKee/exports"
        folder = st.text_input("Exports folder", value=default)
        if st.button("Load from folder", type="primary"):
            try:
                st.session_state["data"] = E.load_from_folder(folder)
                st.success("Loaded.")
            except Exception as ex:
                st.error(str(ex))
    else:
        ups = st.file_uploader("Drop sales + inventory CSVs", accept_multiple_files=True, type="csv")
        if ups and st.button("Load + save uploads", type="primary"):
            try:
                S.save_uploaded_files(ups)            # persist raw files (no Shopify API needed)
                stored = S.load_stored_bytes()        # reload the FULL saved set (history + new)
                st.session_state["data"] = (E.load_from_named_bytes(stored) if stored
                                            else E.load_from_uploads(ups))
                st.success("Loaded and saved for next time.")
            except Exception as ex:
                st.error(str(ex))

    st.header("2 · Rules")
    A["review"] = st.number_input("Order cadence (days)", 1, 30, int(A["review"]),
                                  help="7 = weekly. Reorder point = pace × (lead + this).")
    A["lead_swim"] = st.number_input("Lead time — swim (days)", 1, 120, int(A["lead_swim"]))
    A["lead_apparel"] = st.number_input("Lead time — apparel (days)", 1, 120, int(A["lead_apparel"]))
    A["max_cover"] = st.number_input("Max cover (days)", 1, 120, int(A["max_cover"]))
    A["otb"] = st.number_input("Open-to-Buy ($)", 0, 10_000_000, int(A["otb"]), step=5000,
                               help="Fund colorways by profit-velocity until this is spent.")
    A["default_cost"] = st.number_input("Default unit cost ($)", 0.0, 1000.0, float(A["default_cost"]))
    A["pack"] = st.number_input("Pack / MOQ", 1, 1000, int(A["pack"]))
    # only save when something actually changed (avoids a DB write on every click)
    if A != st.session_state.get("_last_assumptions"):
        S.save_assumptions(A)
        st.session_state["_last_assumptions"] = dict(A)

    st.header("3 · Saved data")
    _stored = S.list_stored_files()
    if _stored:
        st.caption(f"💾 {len(_stored)} file(s) saved — auto-loads when you reopen the app.")
        if st.button("🗑️ Clear saved data"):
            S.clear_stored_files(); st.session_state.pop("data", None)
            st.success("Cleared."); st.rerun()
    else:
        st.caption("No saved data yet. Upload files and they'll be remembered next time.")

    st.divider()
    if st.button("Log out"):
        st.session_state["auth_ok"] = False
        _cm = _cookie_mgr()
        if _cm is not None:
            try: _cm.delete("toc_auth", key="auth_logout")
            except Exception: pass
        st.rerun()

# auto-load saved uploads so a refresh / cold start remembers your data
if "data" not in st.session_state:
    _stored = S.load_stored_bytes()
    if _stored:
        try:
            st.session_state["data"] = E.load_from_named_bytes(_stored)
        except Exception:
            pass

if "data" not in st.session_state:
    st.info("⬅️ Load your Shopify exports to begin (or upload once — they'll be remembered).")
    st.stop()

sales, inv, asof = st.session_state["data"]
costs = S.load_costs()
pos = S.load_pos()
setup = S.load_setup()
sku = E.build_sku_table(sales, inv, asof)
rec = E.recommend(sku, A, pos, costs, asof, setup=setup)
cw = E.colorway_rollup(rec, A["otb"])
asof_d = pd.to_datetime(asof).date()
has_inv = E.has_inventory(inv)
vs = E.value_summary(sku, costs, A["default_cost"])
health = E.data_health(sales, inv, asof, costs, sku)

def _short(d):
    if d is None: return "—"
    t = pd.to_datetime(d); return f"{t.month}/{t.day}/{t.strftime('%y')}"

if not has_inv:
    st.warning(
        "⚠️ **No inventory loaded.** The order needs your current on-hand to be accurate — "
        "without it every SKU reads as empty, so the quantities below are rough guesses. "
        "Upload a *Month-end inventory snapshot* (or load from the folder) and they'll snap into place.")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Sales window", f"{_short(health['sales_min'])} → {_short(health['sales_max'])}",
          help="Earliest sales date in the data → most recent (as-of).")
c2.metric("Active SKUs", f"{len(sku):,}")
c3.metric("Inventory value", f"${vs['total']:,.0f}" if has_inv else "—",
          help="On-hand units × unit cost across all SKUs.")
c4.metric("Dead stock", f"${vs['dead']:,.0f}" if has_inv else "—",
          help=(f"{vs['dead_skus']} SKUs · {vs['dead_units']:,} units on-hand with no sales in 90 days"
                if has_inv else "Load inventory to compute."))
c5.metric("This week's spend",
          f"${cw.loc[cw['InBudget']=='BUY','OrderCost'].sum():,.0f} / ${A['otb']:,.0f}",
          help=f"{int((cw['InBudget']=='BUY').sum())} colorways funded by Open-to-Buy.")

with st.expander("🩺 Data health — is my data clean?"):
    hc1, hc2 = st.columns(2)
    with hc1:
        st.markdown("**Sales**")
        st.write(f"Window: {_short(health['sales_min'])} → {_short(health['sales_max'])}")
        sm = health["sales_missing"]
        if sm:
            st.info(f"{len(sm)} day(s) with no sales in the last 90 (may be true zero-sales days): "
                    + ", ".join(_short(d) for d in sm[:12]) + (" …" if len(sm) > 12 else ""))
        else:
            st.success("No gaps in the last 90 days of sales.")
    with hc2:
        st.markdown("**Inventory**")
        if health["inv_loaded"]:
            st.write(f"Snapshots: {_short(health['inv_min'])} → {_short(health['inv_max'])} "
                     f"({len(health['inv_dates'])} day(s), ~{health['inv_cadence_days']:.0f}-day cadence)")
            if health["inv_daily"] and health["inv_missing"]:
                im = health["inv_missing"]
                st.warning(f"⚠️ Missing {len(im)} snapshot day(s) — this hurts the in-stock-days math: "
                           + ", ".join(_short(d) for d in im[:12]) + (" …" if len(im) > 12 else ""))
            elif health["inv_big_gaps"]:
                bg = health["inv_big_gaps"]
                st.info("Snapshots are sparse; larger-than-usual gaps: "
                        + ", ".join(f"{_short(a)}→{_short(b)}" for a, b in bg[:6]))
            else:
                st.success("No unusual gaps in your inventory snapshots.")
        else:
            st.warning("No inventory loaded — upload a Month-end inventory snapshot.")
    mc = health["missing_costs"]
    if mc:
        st.caption(f"💲 {len(mc)} product(s) using the ${A['default_cost']:.0f} default cost — "
                   "set real costs in the **Unit costs** tab for accurate value & profit ranking.")

tabs = st.tabs(["🛒 Order", "🔎 Audit a SKU", "🗂️ Inactive SKUs", "🚚 Open POs",
                "✅ Receipts to confirm", "🆕 New products", "💲 Unit costs", "⚙️ Setup"])

# ---------------- Order (grain toggle: Product / Colorway / Size) ----------------
with tabs[0]:
    st.subheader("Recommended order")
    if not has_inv:
        st.warning("Quantities assume **0 on-hand** until you load inventory — treat this as a preview.")
    gc1, gc2 = st.columns([2, 3])
    grain = gc1.radio("View by", ["Product", "Colorway", "Size"], index=1, horizontal=True)
    pick = gc2.selectbox("Filter to a product", ["(all products)"] + sorted(rec["Product"].unique()))
    rf = rec if pick == "(all products)" else rec[rec["Product"] == pick]

    if grain == "Product":
        pr = E.product_rollup(rf, cw)
        cols = ["Product","Colorways","Pace","OnHand","OnOrder","ReorderPt","Order",
                "XS","S","M","L","XL","Oth","ProfitVelocity","OrderCost"]
        if "BuyColorways" in pr.columns: cols.insert(2, "BuyColorways")
        st.dataframe(pr[cols].round({"Pace":2,"ProfitVelocity":1,"OrderCost":0}),
                     use_container_width=True, height=460)
        st.caption("Totals across every colorway of each product. BUY/DEFER is decided at the colorway level.")
    elif grain == "Colorway":
        cwf = cw if pick == "(all products)" else cw[cw["Product"] == pick]
        show = cwf[["Product","Color","Pace","OnHand","OnOrder","ReorderPt","Order",
                    "XS","S","M","L","XL","Oth","ProfitVelocity","OrderCost","CumCost","InBudget"]].copy()
        st.dataframe(show.round({"Pace":2,"ProfitVelocity":1,"OrderCost":0,"CumCost":0}),
                     use_container_width=True, height=460)
    else:  # Size — each SKU its own reorder point, with its real code + buffer zone
        sz = rf.merge(cw[["Product","Color","InBudget"]], on=["Product","Color"], how="left")
        cols = ["SKU","Product","Color","Size","Pace","DIS30","OnHand","OnOrder","ReorderPt",
                "Order","Zone","InBudget","UnitCost","Price","ProfitVelocity"]
        st.dataframe(sz[cols].round({"Pace":2,"ProfitVelocity":1}).sort_values("Order", ascending=False),
                     use_container_width=True, height=460)

    st.divider()
    # ---- Factory PO downloads (this week's funded BUY rows) ----
    buy_sizes = rec.merge(cw[cw["InBudget"] == "BUY"][["Product","Color"]],
                          on=["Product","Color"], how="inner")
    buy_sizes = buy_sizes[buy_sizes["Order"] > 0]
    d1, d2 = st.columns(2)
    po_sku = buy_sizes.assign(Date=str(asof_d))[["Date","SKU","Product","Color","Size","Order"]]\
        .rename(columns={"Order":"Qty"}).sort_values(["Product","Color","Size"])
    d1.download_button("⬇️ Factory PO — by SKU (with codes)",
                       po_sku.to_csv(index=False).encode(), "factory_po_by_sku.csv", "text/csv",
                       help="One row per SKU with its real Shopify code — the order you hand the factory.")
    buy = cw[cw["InBudget"] == "BUY"]
    factory = buy.assign(Date=str(asof_d))[
        ["Date","Product","Color","XS","S","M","L","XL","Oth","Order"]].rename(columns={"Order":"Total"})
    d2.download_button("⬇️ Factory PO — by colorway (size grid)",
                       factory.to_csv(index=False).encode(), "factory_po_by_colorway.csv", "text/csv")

# ---------------- Audit ----------------
with tabs[1]:
    st.subheader("Audit any SKU — does the pace ignore stockout days?")
    keys = sorted(rec["key"].unique())
    k = st.selectbox("Pick a SKU (Product | Color | Size)", keys)
    r = rec[rec["key"] == k].iloc[0]
    st.caption(f"SKU code: **{r['SKU'] or '—'}**")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Days in stock (30)", int(r["DIS30"]))
    m2.metric("Units sold (30d)", int(r["U30"]))
    m3.metric("Daily pace", f"{r['Pace']:.2f}")
    m4.metric("Order now", int(r["Order"]))
    start = asof - pd.Timedelta(days=41)
    ds = sales[(sales["key"] == k) & (sales["Day"] >= start)].groupby("Day")["units"].sum()
    di = inv[inv["key"] == k].groupby("date")["oh"].sum() if not inv.empty else pd.Series(dtype=float)
    idx = pd.date_range(start, asof)
    chart = pd.DataFrame({"Units sold": ds.reindex(idx).fillna(0),
                          "On hand": di.reindex(idx).ffill().fillna(0)}, index=idx)
    st.line_chart(chart)

# ---------------- Inactive SKUs ----------------
with tabs[2]:
    st.subheader("Inactive SKUs — sold before, quiet now (no sales in 90 days, no stock)")
    # cache per data-load (scans full history) so it doesn't recompute on every click
    _ck = ("inactive", str(asof), len(sales))
    if st.session_state.get("_inactive_key") != _ck:
        st.session_state["_inactive_df"] = E.inactive_skus(sales, inv, asof)
        st.session_state["_inactive_key"] = _ck
    inactive = st.session_state["_inactive_df"]
    if inactive.empty:
        st.success("No inactive SKUs — everything with history is either selling or in stock.")
    else:
        st.caption(f"{len(inactive):,} dormant SKUs, most-recently-sold first. Pick one below to see its history.")
        st.dataframe(inactive[["SKU","Product","Color","Size","LastSold","DaysSince","LifetimeUnits","OnHand"]],
                     use_container_width=True, height=320)
        ikey = st.selectbox("Drill into a SKU", inactive["key"].tolist())
        ir = inactive[inactive["key"] == ikey].iloc[0]
        dd1, dd2, dd3 = st.columns(3)
        dd1.metric("Last sold", str(ir["LastSold"]))
        dd2.metric("Days since", int(ir["DaysSince"]))
        dd3.metric("Lifetime units", f"{ir['LifetimeUnits']:,.0f}")
        st.caption(f"SKU code: **{ir['SKU'] or '—'}**")
        hist = sales[sales["key"] == ikey].groupby("Day")["units"].sum()
        if len(hist):
            st.line_chart(hist.rename("Units sold"))

# ---------------- Open POs ----------------
with tabs[3]:
    st.subheader("Open POs (what's on the way)")
    st.caption("Add inbound orders here. The engine only counts a PO if its ETA lands within lead + cadence.")
    edit = pos[pos["status"].fillna("in_transit") == "in_transit"] if len(pos) else pos
    edited = st.data_editor(edit if len(edit) else pd.DataFrame(columns=S.PO_COLS),
                            num_rows="dynamic", use_container_width=True,
                            column_config={"po_id": st.column_config.TextColumn("po_id", disabled=True)})
    if st.button("💾 Save POs"):
        received = pos[pos["status"] == "received"]
        S.save_pos(pd.concat([edited, received], ignore_index=True))
        st.success("Saved."); st.rerun()

# ---------------- Receipts to confirm ----------------
with tabs[4]:
    st.subheader("Receipts to confirm — arrivals detected from the inventory data")
    st.caption("When on-hand jumps up by ~a PO's quantity, it's probably arrived. Confirm to drop it off 'in transit'.")
    cand = E.detect_receipts(inv, pos)
    if cand.empty:
        st.info("No likely receipts right now. (Needs at least two inventory snapshots + open POs.)")
    else:
        for _, c in cand.iterrows():
            cc1, cc2 = st.columns([5, 1])
            cc1.write(f"**{c['product']} · {c['color']} · {c['size']}** — on-hand jumped **+{int(c['jump'])}** "
                      f"on {c['jump_date']}, matches open PO of **{int(c['po_qty'])}**.")
            if cc2.button("Confirm received", key="rcv_"+str(c['po_id'])):
                S.mark_received(c['po_id']); st.success("Marked received."); st.rerun()

# ---------------- New products ----------------
with tabs[5]:
    st.subheader("New products to set up")
    new = E.detect_new_products(sku, costs)
    if not new:
        st.success("No new products — every product has a cost set.")
    else:
        st.caption(f"{len(new)} products are using the ${A['default_cost']:.0f} default cost. Set real costs:")
        nd = pd.DataFrame({"product": new, "unit_cost": [A["default_cost"]] * len(new)})
        e = st.data_editor(nd, use_container_width=True, hide_index=True)
        if st.button("💾 Save these costs"):
            costs.update({r["product"]: float(r["unit_cost"]) for _, r in e.iterrows() if r["unit_cost"]})
            S.save_costs(costs); st.success("Saved."); st.rerun()

# ---------------- Unit costs ----------------
with tabs[6]:
    st.subheader("Unit costs (drives the profit ranking)")
    allp = sorted(sku["Product"].unique())
    cd = pd.DataFrame({"product": allp, "unit_cost": [costs.get(p, A["default_cost"]) for p in allp]})
    e = st.data_editor(cd, use_container_width=True, hide_index=True, height=460)
    if st.button("💾 Save costs"):
        S.save_costs({r["product"]: float(r["unit_cost"]) for _, r in e.iterrows() if r["unit_cost"]})
        st.success("Saved."); st.rerun()

# ---------------- Setup (category + lead time per product) ----------------
with tabs[7]:
    st.subheader("Setup — category & lead time per product")
    st.caption("Override the swim/apparel guess and set a lead time per product. "
               "Blank lead = use the sidebar default for that category. New products appear here automatically.")
    prods = sorted(sku["Product"].unique())
    base = []
    for p in prods:
        s = setup.get(p, {})
        inferred = sku.loc[sku["Product"] == p, "Category"].iloc[0]
        base.append({"product": p,
                     "category": s.get("category") or inferred,
                     "lead_days": s.get("lead") if s.get("lead") else None})
    se = st.data_editor(
        pd.DataFrame(base), use_container_width=True, hide_index=True, height=460,
        column_config={
            "product": st.column_config.TextColumn("product", disabled=True),
            "category": st.column_config.SelectboxColumn("category", options=["swim", "apparel"]),
            "lead_days": st.column_config.NumberColumn("lead_days (blank = use sidebar default)",
                                                       min_value=1, max_value=180, step=1)})
    if st.button("💾 Save setup"):
        newmap = {}
        for _, r in se.iterrows():
            entry = {}
            if r["category"] in ("swim", "apparel"):
                entry["category"] = r["category"]
            if pd.notna(r["lead_days"]) and str(r["lead_days"]).strip() != "":
                try: entry["lead"] = int(float(r["lead_days"]))
                except Exception: pass
            if entry:
                newmap[r["product"]] = entry
        S.save_setup(newmap)
        st.success("Saved. The order uses these on the next rerun."); st.rerun()
