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
    """
    Returns a dataframe with:
    - establishment_WTE_per_person (before on-call loss)
    - ward_effective_WTE_per_person (after on-call loss)
    - totals, and on-call investment metrics
    """
    if dev_days_map is None:
        dev_days_map = {}

    out = d.copy()
    out["grade"] = out["staff_group"].map(grade_of)

    # Development days
    out["dev_days"] = out["staff_group"].map(dev_days_map).fillna(0)
    out["dev_factor"] = out["dev_days"] / 260.0  # approx working days/year

    # Availability after sickness + dev days
    out["availability_factor"] = (1 - sickness_rate) * (1 - out["dev_factor"])

    # Establishment WTE (before on-call loss)
    out["establishment_WTE_per_person"] = (
        out["base_WTE"]
        * out["pattern_factor"]
        * out["days_factor"]
        * out["leave_factor"]
        * out["availability_factor"]
    )

    # Ward-facing WTE (after on-call loss)
    out["ward_effective_WTE_per_person"] = (
        out["establishment_WTE_per_person"] * (1 - out["oncall_loss"])
    )

    # Totals
    out["establishment_WTE_total"] = out["headcount"] * out["establishment_WTE_per_person"]
    out["ward_WTE_total"] = out["headcount"] * out["ward_effective_WTE_per_person"]

    # On-call investment metrics (only meaningful where oncall_loss > 0)
    out["oncall_WTE_investment"] = out["establishment_WTE_total"].where(out["oncall_loss"] > 0, 0)
    out["oncall_WTE_lost"] = (out["establishment_WTE_total"] * out["oncall_loss"]).where(out["oncall_loss"] > 0, 0)
    out["oncall_ward_WTE_remaining"] = out["ward_WTE_total"].where(out["oncall_loss"] > 0, 0)

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
        value=int(round(row["headcount"])),
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

st.sidebar.header("Cover requirement")
required_staff_per_day = st.sidebar.number_input(
    "Required staff per weekday day (all wards combined)",
    min_value=1, max_value=50, value=9, step=1
)
working_days_per_year = st.sidebar.number_input(
    "Weekday day shifts per year (for locum estimate)",
    min_value=200, max_value=365, value=260, step=5
)

# -----------------------------
# APPLY CONFIG
# -----------------------------
df2 = df.copy()
df2["headcount"] = df2["staff_group"].map(new_counts).fillna(0)

baseline = recalc(df, sickness_rate=0.0, dev_days_map={})
scenario = recalc(df2, sickness_rate=sickness_rate, dev_days_map=dev_map)

# -----------------------------
# SUMMARIES
# -----------------------------
total_headcount = float(scenario["headcount"].sum())

# Headcount by grade
hc_by_grade = (
    scenario.groupby("grade", dropna=False)["headcount"]
    .sum()
    .reindex(["Foundation", "Core", "GPST", "ACP", "Other"])
    .fillna(0)
)

# On-call by grade: WTE investment and WTE lost
oncall_invest_by_grade = (
    scenario.groupby("grade", dropna=False)["oncall_WTE_investment"]
    .sum()
    .reindex(["Foundation", "Core", "GPST", "ACP", "Other"])
    .fillna(0)
)

oncall_lost_by_grade = (
    scenario.groupby("grade", dropna=False)["oncall_WTE_lost"]
    .sum()
    .reindex(["Foundation", "Core", "GPST", "ACP", "Other"])
    .fillna(0)
)

# Overall WTE
total_ward_wte = float(scenario["ward_WTE_total"].sum())
baseline_ward_wte = float(baseline["ward_WTE_total"].sum())

overall_oncall_invest_wte = float(scenario["oncall_WTE_investment"].sum())
overall_oncall_lost_wte = float(scenario["oncall_WTE_lost"].sum())
overall_oncall_ward_remaining = float(scenario["oncall_ward_WTE_remaining"].sum())

# -----------------------------
# COVER + LOCUM ESTIMATE
# -----------------------------
# Cover % is relative to the required staff per weekday day.
cover_pct = 100.0 * (total_ward_wte / required_staff_per_day) if required_staff_per_day > 0 else 0.0
cover_pct_display = min(cover_pct, 100.0)

# Locum shifts/year to fill the *average* shortfall (weekday day shifts only)
gap_staff_per_day = max(0.0, required_staff_per_day - total_ward_wte)
locum_shifts_per_year = gap_staff_per_day * float(working_days_per_year)

# -----------------------------
# OUTPUT
# -----------------------------
st.title("Geriatrics Staffing Capacity Model")

k1, k2, k3, k4, k5, k6 = st.columns(6)

k1.metric(
    "Total ward-facing WTE",
    f"{total_ward_wte:.2f}",
    f"{total_ward_wte - baseline_ward_wte:+.2f}"
)
k2.metric("% cover (weekday days)", f"{cover_pct_display:.0f}%")
k3.metric("Locum day-shifts/year", f"{locum_shifts_per_year:.0f}")
k4.metric("Total headcount", f"{total_headcount:.0f}")
k5.metric("On-call WTE investment", f"{overall_oncall_invest_wte:.2f}")
k6.metric("WTE lost to on-call", f"{overall_oncall_lost_wte:.2f}")

st.caption(
    f"Assumptions for cover: requirement={required_staff_per_day} staff per weekday day; "
    f"locum estimate uses {working_days_per_year} weekday day shifts/year. "
    f"On-call groups' ward-facing WTE remaining (after on-call loss): {overall_oncall_ward_remaining:.2f}."
)

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
    st.subheader("On-call commitment by grade (WTE)")
    table = pd.DataFrame({
        "grade": oncall_invest_by_grade.index,
        "oncall_WTE_investment": oncall_invest_by_grade.values,
        "WTE_lost_to_oncall": oncall_lost_by_grade.values
    })
    st.dataframe(table, use_container_width=True)
    st.caption("Chart: on-call WTE investment")
    st.bar_chart(oncall_invest_by_grade)

st.divider()

st.subheader("Ward-facing WTE by staff group")
plot_df = scenario[["staff_group", "ward_WTE_total"]].copy()
plot_df["staff_group"] = plot_df["staff_group"].astype(str)
st.bar_chart(plot_df.set_index("staff_group")["ward_WTE_total"])

st.subheader("Detailed Workforce Table")
detail = scenario[[
    "grade", "staff_group", "headcount",
    "leave_factor", "oncall_loss", "dev_days",
    "availability_factor",
    "establishment_WTE_total", "oncall_WTE_investment", "oncall_WTE_lost",
    "ward_WTE_total"
]].sort_values(["grade", "staff_group"])

st.dataframe(detail, use_container_width=True)
