import pandas as pd
import streamlit as st

st.set_page_config(page_title="Geriatrics Workforce Model", layout="wide")

EXCEL_PATH = "staffing_model.xlsx"
STAFF_SHEET = "Staff"

# -----------------------------
# LOAD & CLEAN DATA
# -----------------------------
df = pd.read_excel(EXCEL_PATH, sheet_name=STAFF_SHEET)
df.columns = [str(c).strip() for c in df.columns]

if "staff_group" not in df.columns:
    df = df.rename(columns={df.columns[0]: "staff_group"})

df["staff_group"] = df["staff_group"].astype(str).str.strip()
df = df[df["staff_group"].ne("")]
df = df[df["staff_group"].ne("nan")]

numeric_cols = [
    "headcount", "base_WTE", "pattern_factor",
    "days_factor", "leave_factor", "oncall_loss"
]
for col in numeric_cols:
    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

required = [
    "staff_group", "headcount", "base_WTE",
    "pattern_factor", "days_factor", "leave_factor", "oncall_loss"
]
missing = [c for c in required if c not in df.columns]
if missing:
    st.error(f"Missing columns in Staff sheet: {missing}")
    st.stop()

# -----------------------------
# GRADE GROUPING (your definitions)
# -----------------------------
GRADE_MAP = {
    "FY1": "Foundation",
    "FY2": "Foundation",
    "IMT": "Core",
    "LIMT": "Core",
    "CEF": "Core",
    "CFn": "Core",
    "CFoc": "Core",
    "GPST": "GPST",
    "ACP": "ACP",
}

def grade_of(staff_group: str) -> str:
    return GRADE_MAP.get(staff_group, "Other")

# -----------------------------
# MODEL CALCULATION
# -----------------------------
def recalc(d, sickness_rate=0.05, dev_days_map=None):
    if dev_days_map is None:
        dev_days_map = {}

    out = d.copy()

    out["grade"] = out["staff_group"].map(grade_of)

    out["dev_days"] = out["staff_group"].map(dev_days_map).fillna(0)
    out["dev_factor"] = out["dev_days"] / 260.0  # approx working days/year

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

# -----------------------------
# SIDEBAR CONFIG
# -----------------------------
st.sidebar.header("Headcount Configuration")

new_counts = {}
for _, row in df.sort_values("staff_group").iterrows():
    g = row["staff_group"]
    new_counts[g] = st.sidebar.number_input(
        g,
        min_value=0,
        max_value=200,
        value=int(row["headcount"]),
        step=1
    )

st.sidebar.header("Assumptions")
sickness_rate = st.sidebar.slider("Sickness allowance", 0.00, 0.15, 0.05, 0.01)

apply_dev_days = st.sidebar.checkbox(
    "Apply 10 development days to IMT, LIMT, Clinical Fellows", value=True
)

dev_map = {}
if apply_dev_days:
    for g in ["IMT", "LIMT", "CFn", "CFoc"]:
        dev_map[g] = 10

# -----------------------------
# APPLY CONFIG
# -----------------------------
df2 = df.copy()
df2["headcount"] = df2["staff_group"].map(new_counts).fillna(0)

baseline = recalc(df, sickness_rate=0.0, dev_days_map={})
scenario = recalc(df2, sickness_rate=sickness_rate, dev_days_map=dev_map)

# -----------------------------
# HEADCOUNT SUMMARIES
# -----------------------------
total_headcount = float(scenario["headcount"].sum())

hc_by_grade = (
    scenario.groupby("grade", dropna=False)["headcount"]
    .sum()
    .reindex(["Foundation", "Core", "GPST", "ACP", "Other"])
    .fillna(0)
)

oncall_by_grade = (
    scenario.loc[scenario["oncall_loss"] > 0]
    .groupby("grade", dropna=False)["headcount"]
    .sum()
    .reindex(["Foundation", "Core", "GPST", "ACP", "Other"])
    .fillna(0)
)

# -----------------------------
# OUTPUT
# -----------------------------
st.title("Geriatrics Staffing Capacity Model")

total_wte = float(scenario["total_WTE"].sum())
baseline_wte = float(baseline["total_WTE"].sum())

k1, k2, k3, k4 = st.columns(4)
k1.metric("Total Ward-Facing WTE", f"{total_wte:.2f}", f"{total_wte - baseline_wte:+.2f}")
k2.metric("Total Headcount", f"{total_headcount:.0f}")
k3.metric("On-Call Pool (Headcount)", f"{scenario.loc[scenario.oncall_loss > 0, 'headcount'].sum():.0f}")
k4.metric("Sickness + Dev Days Applied", "Yes" if apply_dev_days else "No")

st.divider()

left, right = st.columns(2)

with left:
    st.subheader("Headcount by grade")
    st.dataframe(
        hc_by_grade.reset_index().rename(columns={"index": "grade", "headcount": "headcount"}),
        use_container_width=True
    )
    st.bar_chart(hc_by_grade)

with right:
    st.subheader("On-call headcount by grade")
    st.dataframe(
        oncall_by_grade.reset_index().rename(columns={"index": "grade", "headcount": "oncall_headcount"}),
        use_container_width=True
    )
    st.bar_chart(oncall_by_grade)

st.divider()

st.subheader("Ward-Facing WTE by staff group")
plot_df = scenario[["staff_group", "total_WTE"]].copy()
plot_df["staff_group"] = plot_df["staff_group"].astype(str)
st.bar_chart(plot_df.set_index("staff_group")["total_WTE"])

st.subheader("Detailed Workforce Table")
st.dataframe(
    scenario[[
        "grade", "staff_group", "headcount",
        "leave_factor", "oncall_loss", "dev_days",
        "availability_factor", "effective_WTE", "total_WTE"
    ]].sort_values(["grade", "staff_group"]),
    use_container_width=True
)
