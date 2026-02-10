import numpy as np
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
# GRADE GROUPING (updated)
# -----------------------------
# Core + GPST => "SHO-grade"
# Foundation => "Foundation-grade"
GRADE_MAP = {
    "FY1": "Foundation-grade",
    "FY2": "Foundation-grade",
    "IMT": "SHO-grade",
    "LIMT": "SHO-grade",
    "CEF": "SHO-grade",
    "CFn": "SHO-grade",
    "CFoc": "SHO-grade",
    "GPST": "SHO-grade",
    "ACP": "ACP",
}
def grade_of(staff_group: str) -> str:
    return GRADE_MAP.get(staff_group, "Other")

# -----------------------------
# MODEL CALCULATION
# -----------------------------
def recalc(d, sickness_rate=0.05, dev_days_map=None):
    """
    scheduled_ward_WTE_per_person:
      planned ward-facing contribution per person (after leave + oncall_loss),
      but BEFORE sickness/dev (availability).

    availability_factor:
      probability-like multiplier from sickness + dev days.

    ward_effective_WTE_per_person (mean):
      scheduled_ward_WTE_per_person * availability_factor

    oncall_WTE_lost (scheduled):
      how much establishment WTE is diverted into on-call (your "giving to on-call"),
      defined as scheduled_establishment_WTE_total * oncall_loss
      (i.e., BEFORE sickness/dev).
    """
    if dev_days_map is None:
        dev_days_map = {}

    out = d.copy()
    out["grade"] = out["staff_group"].map(grade_of)

    # Dev days -> availability
    out["dev_days"] = out["staff_group"].map(dev_days_map).fillna(0).astype(float)
    out["dev_factor"] = out["dev_days"] / 260.0  # approx working days/year

    out["availability_factor"] = (1 - sickness_rate) * (1 - out["dev_factor"])

    # Planned ward-facing contribution per person (no sickness/dev)
    out["scheduled_ward_WTE_per_person"] = (
        out["base_WTE"]
        * out["pattern_factor"]
        * out["days_factor"]
        * out["leave_factor"]
        * (1 - out["oncall_loss"])
    )

    # Mean ward-facing after availability
    out["ward_effective_WTE_per_person"] = (
        out["scheduled_ward_WTE_per_person"] * out["availability_factor"]
    )

    out["scheduled_ward_WTE_total"] = out["headcount"] * out["scheduled_ward_WTE_per_person"]
    out["ward_WTE_total_mean"] = out["headcount"] * out["ward_effective_WTE_per_person"]

    # Establishment WTE (scheduled, before on-call loss, before sickness/dev)
    out["scheduled_establishment_WTE_per_person"] = (
        out["base_WTE"]
        * out["pattern_factor"]
        * out["days_factor"]
        * out["leave_factor"]
    )
    out["scheduled_establishment_WTE_total"] = out["headcount"] * out["scheduled_establishment_WTE_per_person"]

    # "Giving to on-call": scheduled WTE diverted by on-call loss assumption
    out["oncall_WTE_lost"] = (out["scheduled_establishment_WTE_total"] * out["oncall_loss"]).where(out["oncall_loss"] > 0, 0)

    return out

# -----------------------------
# MONTE CARLO COVER SIMULATION
# -----------------------------
def simulate_cover(scenario_df: pd.DataFrame, required_wte: float, sim_days: int, seed: int = 1):
    """
    Builds an individual-level pool:
      each person contributes scheduled_ward_WTE_per_person if present that day,
      and is present with probability availability_factor.

    Returns:
      p_meet, expected_shortfall_per_day, expected_locum_wte_days_per_year
    """
    rng = np.random.default_rng(seed)

    # Expand to individuals
    weights = []
    probs = []
    for _, row in scenario_df.iterrows():
        n = int(round(row["headcount"]))
        if n <= 0:
            continue
        w = float(row["scheduled_ward_WTE_per_person"])
        p = float(row["availability_factor"])
        p = min(max(p, 0.0), 1.0)
        weights.extend([w] * n)
        probs.extend([p] * n)

    if len(weights) == 0:
        return 0.0, float(required_wte), float(required_wte) * 260.0

    w = np.array(weights, dtype=float)          # (N,)
    p = np.array(probs, dtype=float)            # (N,)

    # Simulate presence matrix: (sim_days, N)
    present = rng.random((sim_days, len(w))) < p
    available_wte = present @ w  # (sim_days,)

    shortfall = np.maximum(0.0, required_wte - available_wte)

    p_meet = float(np.mean(available_wte >= required_wte))
    exp_shortfall_per_day = float(np.mean(shortfall))
    return p_meet, exp_shortfall_per_day

# -----------------------------
# SIDEBAR CONFIG
# -----------------------------
st.sidebar.header("Headcount Configuration")
new_counts = {}
for _, row in df.sort_values("staff_group").iterrows():
    g = row["staff_group"]
    new_counts[g] = st.sidebar.number_input(
        g, min_value=0, max_value=200,
        value=int(round(row["headcount"])), step=1
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

st.sidebar.header("Cover / Locum")
required_staff_per_day = st.sidebar.number_input(
    "Required pooled cover (WTE-equivalent per weekday day)",
    min_value=1.0, max_value=60.0, value=9.0, step=1.0
)
working_days_per_year = st.sidebar.number_input(
    "Weekday day shifts per year",
    min_value=200, max_value=365, value=260, step=5
)

st.sidebar.header("Simulation")
sim_days = st.sidebar.slider("Simulation days (more = smoother)", 2000, 50000, 20000, 2000)
seed = st.sidebar.number_input("Random seed", min_value=1, max_value=9999, value=1, step=1)

# -----------------------------
# APPLY CONFIG
# -----------------------------
df2 = df.copy()
df2["headcount"] = df2["staff_group"].map(new_counts).fillna(0)

scenario = recalc(df2, sickness_rate=sickness_rate, dev_days_map=dev_map)

# -----------------------------
# SUMMARIES
# -----------------------------
total_headcount = float(scenario["headcount"].sum())
scheduled_ward_wte = float(scenario["scheduled_ward_WTE_total"].sum())
mean_ward_wte = float(scenario["ward_WTE_total_mean"].sum())

# On-call WTE lost (overall + by grade)  ✅ user requested
oncall_lost_by_grade = (
    scenario.groupby("grade", dropna=False)["oncall_WTE_lost"]
    .sum()
    .reindex(["Foundation-grade", "SHO-grade", "ACP", "Other"])
    .fillna(0)
)

oncall_lost_foundation = float(oncall_lost_by_grade.get("Foundation-grade", 0.0))
oncall_lost_sho = float(oncall_lost_by_grade.get("SHO-grade", 0.0))
oncall_lost_total = float(scenario["oncall_WTE_lost"].sum())

# Cover simulation
p_meet, exp_shortfall_per_day = simulate_cover(
    scenario_df=scenario,
    required_wte=float(required_staff_per_day),
    sim_days=int(sim_days),
    seed=int(seed)
)
locum_wte_days_per_year = exp_shortfall_per_day * float(working_days_per_year)

# Headcount by grade (still useful)
hc_by_grade = (
    scenario.groupby("grade", dropna=False)["headcount"]
    .sum()
    .reindex(["Foundation-grade", "SHO-grade", "ACP", "Other"])
    .fillna(0)
)

# -----------------------------
# OUTPUT
# -----------------------------
st.title("Geriatrics Staffing Capacity Model")

# Top metrics row (now includes WTE LOST to on-call by grade)
k1, k2, k3, k4, k5, k6, k7, k8 = st.columns(8)
k1.metric("Mean ward-facing WTE", f"{mean_ward_wte:.2f}")
k2.metric("Scheduled ward WTE", f"{scheduled_ward_wte:.2f}")
k3.metric("P(meet cover) with flexing", f"{100*p_meet:.1f}%")
k4.metric("Locum WTE-day shifts/year", f"{locum_wte_days_per_year:.0f}")
k5.metric("Total headcount", f"{total_headcount:.0f}")
k6.metric("WTE lost to on-call (total)", f"{oncall_lost_total:.2f}")
k7.metric("WTE lost to on-call (Foundation)", f"{oncall_lost_foundation:.2f}")
k8.metric("WTE lost to on-call (SHO)", f"{oncall_lost_sho:.2f}")

st.caption(
    f"Cover requirement is {required_staff_per_day:.0f} WTE-equivalent per weekday day (pooled across wards). "
    f"Simulation uses {sim_days} days (seed={seed}); increase sim-days if you want smoother locum estimates. "
    f"Locum estimate is expected shortfall per day × {working_days_per_year} weekday shifts/year."
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
    st.subheader("On-call WTE lost by grade (\"given\" to on-call)")
    lost_tbl = oncall_lost_by_grade.reset_index()
    lost_tbl.columns = ["grade", "WTE_lost_to_oncall"]
    st.dataframe(lost_tbl, use_container_width=True)
    st.bar_chart(oncall_lost_by_grade)

st.divider()

st.subheader("Mean ward-facing WTE by staff group")
plot_df = scenario[["staff_group", "ward_WTE_total_mean"]].copy()
plot_df["staff_group"] = plot_df["staff_group"].astype(str)
st.bar_chart(plot_df.set_index("staff_group")["ward_WTE_total_mean"])

st.subheader("Detailed Workforce Table")
detail = scenario[[
    "grade", "staff_group", "headcount",
    "leave_factor", "oncall_loss", "dev_days",
    "availability_factor",
    "scheduled_ward_WTE_total", "ward_WTE_total_mean",
    "oncall_WTE_lost"
]].sort_values(["grade", "staff_group"])

st.dataframe(detail, use_container_width=True)
