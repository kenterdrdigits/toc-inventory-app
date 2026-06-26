"""
TOC Inventory — Streamlit prototype.
Run:  streamlit run app.py
Single user, CSV-driven. Point it at your Shopify exports folder (or upload files),
set the rules, manage POs, confirm receipts, review new products, and read the order.
"""
import os
import pandas as pd
import streamlit as st
import engine as E
import storage as S

st.set_page_config(page_title="TOC Inventory", layout="wide", page_icon="📦")

# ---------------- simple password gate ----------------
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
    if st.session_state.get("auth_ok"):
        return True
    st.markdown("### 🔒 TOC Inventory — sign in")
    pw = st.text_input("Password", type="password")
    if pw:
        if pw == correct:
            st.session_state["auth_ok"] = True
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
        if ups and st.button("Load uploads", type="primary"):
            try:
                st.session_state["data"] = E.load_from_uploads(ups)
                st.success("Loaded.")
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
    S.save_assumptions(A)

if "data" not in st.session_state:
    st.info("⬅️ Load your Shopify exports to begin (folder path is easiest).")
    st.stop()

sales, inv, asof = st.session_state["data"]
costs = S.load_costs()
pos = S.load_pos()
sku = E.build_sku_table(sales, inv, asof)
rec = E.recommend(sku, A, pos, costs, asof)
cw = E.colorway_rollup(rec, A["otb"])
asof_d = pd.to_datetime(asof).date()

c1, c2, c3, c4 = st.columns(4)
c1.metric("As-of (sales)", str(asof_d))
c2.metric("Active SKUs", f"{len(sku):,}")
c3.metric("Colorways to buy", int((cw["InBudget"] == "BUY").sum()))
c4.metric("This week's spend", f"${cw.loc[cw['InBudget']=='BUY','OrderCost'].sum():,.0f} / ${A['otb']:,.0f}")

tabs = st.tabs(["🛒 Order", "🔬 By size", "🔎 Audit a SKU", "🚚 Open POs",
                "✅ Receipts to confirm", "🆕 New products", "💲 Unit costs"])

# ---------------- Order (colorway) ----------------
with tabs[0]:
    st.subheader("Recommended order — by colorway (funded by Open-to-Buy)")
    show = cw[["Product","Color","Pace","OnHand","OnOrder","ReorderPt","Order",
               "XS","S","M","L","XL","Oth","ProfitVelocity","OrderCost","CumCost","InBudget"]].copy()
    show = show.round({"Pace":2,"ProfitVelocity":1,"OrderCost":0,"CumCost":0})
    st.dataframe(show, use_container_width=True, height=460)
    buy = cw[cw["InBudget"] == "BUY"]
    factory = buy.assign(Date=str(asof_d),
                         **{"Style No.": ["YM-%04d" % i for i in range(1000, 1000+len(buy))]})
    factory = factory[["Date","Style No.","Product","Color","XS","S","M","L","XL","Oth","Order"]]\
        .rename(columns={"Order":"Total"})
    st.download_button("⬇️ Download Factory PO (this week's BUY rows)",
                       factory.to_csv(index=False).encode(), "factory_po.csv", "text/csv")

# ---------------- By size ----------------
with tabs[1]:
    st.subheader("Size-level engine — each SKU its own reorder point")
    prod = st.selectbox("Filter to a product (optional)", ["(all)"] + sorted(rec["Product"].unique()))
    d = rec if prod == "(all)" else rec[rec["Product"] == prod]
    st.dataframe(d[["Product","Color","Size","Pace","DIS30","OnHand","OnOrder","ReorderPt",
                    "Order","Zone","UnitCost","Price","ProfitVelocity"]]
                 .round({"Pace":2,"ProfitVelocity":1}).sort_values("Order", ascending=False),
                 use_container_width=True, height=460)

# ---------------- Audit ----------------
with tabs[2]:
    st.subheader("Audit any SKU — does the pace ignore stockout days?")
    keys = sorted(rec["key"].unique())
    k = st.selectbox("Pick a SKU (Product | Color | Size)", keys)
    r = rec[rec["key"] == k].iloc[0]
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
