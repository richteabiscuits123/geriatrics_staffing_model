import pandas as pd
import streamlit as st

st.set_page_config(page_title="Geriatrics Workforce Model", layout="wide")

EXCEL_PATH = "staffing_model.xlsx"
STAFF_SHEET = "Staff"

# ---------- Load ----------
df = pd.read_excel(EXCEL_PATH, sheet_name=STAFF_SHEET)
df.columns = [str(c).strip() for c in df.columns]
if "staff_group" not in df.columns:
    df = df.rename(columns={df.columns[0]: "staff_group"})

required = ["staff_group","headcount","base_WTE","pattern_factor","days_factor","leave_factor","oncall_loss"]
missing = [c for c in required if c not in df.columns]
if missing:
    st.error(f"Missing columns in Staff sheet: {missing}")
    st.stop()

# ---------- Model ----------
def recalc(d, sickness_rate=0.05, dev_days_map=None, default_dev_days=0):
    """
    sickness_rate: proportion lost (e.g. 0.05 = 5%)
    dev_days_map: dict staff_group -> dev days/year (e.g. {"IMT":10, "LIMT":10, "CFn":10, "CFoc":10})
    """
    if dev_days_map is None:
        dev_days_map = {}

    out = d.copy()

    # development days factor: convert days/year to proportion of the year.
    # Approx: 260 working days/year baseline -> dev_factor = dev_days / 260
    out["dev_days"] = out["staff_group"].map(dev_days_map).fillna(default_dev_days).astype(float)
    out["dev_factor"] = out["dev_days"] / 260.0

    # Apply: capacity multiplier = (1 - sickness) * (1 - dev_factor)
    out["availability_factor"] = (1 - sickness_rate) * (1 - out["dev_factor"])

    out["effective_WTE"] = (
        out["base_WTE"]
        * out["pattern_factor"]
        * out["days_factor"]
        * out["leave_factor"]
        * (1 - out["oncall_loss"])
        * out["availability_factor"]
    )
    out["total_WTE"] = out["headcount"] * out["effective_WTE"]
    return out

# ---------- UI ----------
st.title("Geriatrics Staffing Capacity Model (configuration-based)")
st.caption("Set headcount by staff group; the model calculates ward-facing daytime WTE after leave, on-call loss, sickness and development days.")

st.sidebar.header("Configuration: headcount by staff group")
new_counts = {}
for _, row in df.sort_values("staff_group").iterrows():
    g = str(row["staff_group"])
    default = int(round(float(row["headcount"])))
    new_counts[g] = st.sidebar.number_input(g, min_value=0, max_value=200, value=default, step=1)

st.sidebar.header("Assumptions")
sickness_rate = st.sidebar.slider("Sickness allowance", 0.00, 0.15, 0.05, 0.01)

st.sidebar.subheader("Development days (per person per year)")
dev_days_default = st.sidebar.number_input("Default dev days (all groups)", min_value=0, max_value=40, value=0, step=1)

# Toggle: apply 10 development days to selected groups
apply_10_to_key = st.sidebar.checkbox("Apply 10 dev days to IMT, LIMT, Clinical Fellows", value=True)
dev_map = {}
if apply_10_to_key:
    for key in ["IMT","LIMT","CFn","CFoc"]:
        dev_map[key] = 10

# ---------- Apply config ----------
df2 = df.copy()
df2["headcount"] = df2["staff_group"].map(new_counts).fillna(df2["headcount"]).astype(float)

baseline = recalc(df, sickness_rate=0.0, dev_days_map={}, default_dev_days=0)  # clean baseline without extra allowances
scenario = recalc(df2, sickness_rate=sickness_rate, dev_days_map=dev_map, default_dev_days=dev_days_default)

# ---------- KPIs ----------
total = float(scenario["total_WTE"].sum())
baseline_total = float(baseline["total_WTE"].sum())
delta = total - baseline_total

oncall_pool = float(scenario.loc[scenario["oncall_loss"] > 0, "headcount"].sum())

c1, c2, c3 = st.columns(3)
c1.metric("Total ward-facing WTE", f"{total:.2f}", f"{delta:+.2f} vs baseline (no sickness/dev)")
c2.metric("On-call pool headcount", f"{oncall_pool:.0f}")
c3.metric("Sickness + dev applied to", "IMT/LIMT/CF" if apply_10_to_key else "custom/default")

st.divider()

st.subheader("Ward-facing WTE by staff group")
plot = scenario[["staff_group","total_WTE"]].copy()
plot["staff_group"] = plot["staff_group"].astype(str)
st.bar_chart(plot.set_index("staff_group")["total_WTE"])

st.subheader("Detailed table")
show = scenario[[
    "staff_group","headcount","leave_factor","oncall_loss","dev_days","availability_factor","effective_WTE","total_WTE"
]].sort_values("staff_group")
st.dataframe(show, use_container_width=True)
