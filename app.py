import pandas as pd
import streamlit as st

st.set_page_config(page_title="Geriatrics Workforce Model", layout="wide")

# ---------- Load data ----------
df = pd.read_excel("staffing_model.xlsx", sheet_name="Staff")
df.columns = [str(c).strip() for c in df.columns]
if "staff_group" not in df.columns:
    df = df.rename(columns={df.columns[0]: "staff_group"})

def recalc(d):
    d = d.copy()
    d["effective_WTE"] = (
        d["base_WTE"] *
        d["pattern_factor"] *
        d["days_factor"] *
        d["leave_factor"] *
        (1 - d["oncall_loss"])
    )
    d["total_WTE"] = d["headcount"] * d["effective_WTE"]
    return d

baseline = recalc(df)

# ---------- Sidebar controls ----------
st.sidebar.header("Workforce Configuration")

cfn_to_limt  = st.sidebar.slider("Convert CFn → LIMT", 0, int(df.loc[df.staff_group=="CFn","headcount"]), 0)
cfoc_to_limt = st.sidebar.slider("Convert CFoc → LIMT", 0, int(df.loc[df.staff_group=="CFoc","headcount"]), 0)

st.sidebar.subheader("Assumptions")
limt_oncall = st.sidebar.slider("LIMT on-call loss factor", 0.20, 0.60, 0.40, 0.05)
global_leave = st.sidebar.slider("Leave factor (all groups)", 0.60, 0.95, float(df.leave_factor.iloc[0]), 0.01)

# ---------- Apply scenario ----------
df2 = df.copy()
df2["leave_factor"] = global_leave

df2.loc[df2.staff_group=="CFn","headcount"] -= cfn_to_limt
df2.loc[df2.staff_group=="CFoc","headcount"] -= cfoc_to_limt
df2.loc[df2.staff_group=="LIMT","headcount"] += (cfn_to_limt + cfoc_to_limt)
df2.loc[df2.staff_group=="LIMT","oncall_loss"] = limt_oncall

df2 = recalc(df2)

# ---------- KPIs ----------
st.title("Geriatrics Staffing Capacity Model")

col1, col2, col3 = st.columns(3)
col1.metric("Total Ward-Facing WTE", f"{df2.total_WTE.sum():.2f}", f"{df2.total_WTE.sum() - baseline.total_WTE.sum():+.2f}")
col2.metric("On-Call Pool (Headcount)", f"{df2.loc[df2.oncall_loss>0,'headcount'].sum():.0f}")
col3.metric("Non-On-Call Headcount", f"{df2.loc[df2.oncall_loss==0,'headcount'].sum():.0f}")

st.divider()

# ---------- Visuals ----------
st.subheader("Ward-Facing WTE by Staff Group")
chart_df = df2.copy()
chart_df["staff_group"] = chart_df["staff_group"].astype(str)
st.bar_chart(chart_df.set_index("staff_group")["total_WTE"])

st.subheader("Detailed Workforce Table")
st.dataframe(df2)

