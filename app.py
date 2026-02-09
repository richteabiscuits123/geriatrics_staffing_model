import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt

st.set_page_config(page_title="Geriatrics Staffing Model", layout="wide")

df = pd.read_excel("staffing_model.xlsx", sheet_name="Staff")
df.columns = [str(c).strip() for c in df.columns]
if "staff_group" not in df.columns:
    df = df.rename(columns={df.columns[0]: "staff_group"})

def recalc(d):
    d = d.copy()
    d["effective_WTE"] = (
        d["base_WTE"] * d["pattern_factor"] * d["days_factor"] *
        d["leave_factor"] * (1 - d["oncall_loss"])
    )
    d["total_WTE"] = d["headcount"] * d["effective_WTE"]
    return d

baseline = recalc(df)

st.sidebar.header("Scenario Controls")
cfn_to_limt = st.sidebar.slider("Convert CFn → LIMT", 0, int(baseline.loc[baseline.staff_group=="CFn","headcount"]), 0)
cfoc_to_limt = st.sidebar.slider("Convert CFoc → LIMT", 0, int(baseline.loc[baseline.staff_group=="CFoc","headcount"]), 0)
limt_oncall = st.sidebar.slider("LIMT oncall loss", 0.2, 0.6, 0.4, 0.05)

df2 = baseline.copy()
df2.loc[df2.staff_group=="CFn","headcount"] -= cfn_to_limt
df2.loc[df2.staff_group=="CFoc","headcount"] -= cfoc_to_limt
df2.loc[df2.staff_group=="LIMT","headcount"] += (cfn_to_limt + cfoc_to_limt)
df2.loc[df2.staff_group=="LIMT","oncall_loss"] = limt_oncall
df2 = recalc(df2)

st.title("Geriatrics Staffing Dashboard")

col1, col2 = st.columns(2)
col1.metric("Total Ward WTE", f"{df2.total_WTE.sum():.2f}")
col2.metric("On-call Pool", f"{df2.loc[df2.oncall_loss>0,'headcount'].sum():.0f}")

st.subheader("Ward WTE by Staff Group")

plot_df = df2.copy()
plot_df["staff_group"] = plot_df["staff_group"].astype(str)
plot_df["total_WTE"] = pd.to_numeric(plot_df["total_WTE"], errors="coerce")

fig = plt.figure()
plt.barh(plot_df["staff_group"], plot_df["total_WTE"])
plt.xlabel("Total ward-facing WTE")
plt.ylabel("Staff group")
st.pyplot(fig)

st.subheader("Detail Table")
st.dataframe(df2)
