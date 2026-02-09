import math
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
# BINOMIAL HELPERS (no SciPy needed)
# -----------------------------
def binom_pmf(n: int, k: int, p: float) -> float:
    if k < 0 or k > n:
        return 0.0
    return math.comb(n, k) * (p ** k) * ((1 - p) ** (n - k))

def prob_at_least(n: int, r: int, p: float) -> float:
    # P(X >= r)
    if r <= 0:
        return 1.0
    if r > n:
        return 0.0
    return sum(binom_pmf(n, k, p) for k in range(r, n + 1))

def expected_shortfall(n: int, r: int, p: float) -> float:
    # E[max(0, r - X)]
    if r <= 0:
        return 0.0
    if n <= 0:
        return float(r)
    return sum((r - k) * binom_pmf(n, k, p) for k in range(0, min(r, n + 1)))

def required_n_for_target(r: int, p: float, target: float, n_max: int = 200) -> int:
    # Smallest n such that P(X >= r) >= target
    if r <= 0:
        return 0
    for n in range(r, n_max + 1):
        if prob_at_least(n, r, p) >= target:
            return n
    return n_max

# -----------------------------
# MODEL CALCULATION
# -----------------------------
def recalc(d, sickness_rate=0.05, dev_days_map=None):
    """
    We separate:
    A) Scheduled ward-facing WTE (after leave + rota oncall_loss), but BEFORE sickness/dev
    B) Availability factor from sickness/dev, treated as probabilistic driver of cover reliability

    Outputs include:
    - scheduled_ward_WTE_total: deterministic planned capacity (no sickness/dev)
    - ward_WTE_total: expected ward-facing WTE after applying sickness/dev deterministically (mean)
    - on-call investment/loss metrics in WTE
    """
    if dev_days_map is None:
        dev_days_map = {}

    out = d.copy()
    out["grade"] = out["staff_group"].map(grade_of)

    # Development days (probabilistic availability driver)
    out["dev_days"] = out["staff_group"].map(dev_days_map).fillna(0)
    out["dev_factor"] = out["dev_days"] / 260.0  # approx working days/year

    # Availability factor (used as the effective p-driver)
    out["availability_factor"] = (1 - sickness_rate) * (1 - out["dev_factor"])

    # Scheduled ward-facing WTE per person (no sickness/dev)
    out["scheduled_ward_WTE_per_person"] = (
        out["base_WTE"]
        * out["pattern_factor"]
        * out["days_factor"]
        * out["leave_factor"]
        * (1 - out["oncall_loss"])
    )

    # Expected ward-facing WTE per person (mean, after sickness/dev)
    out["ward_effective_WTE_per_person"] = (
        out["scheduled_ward_WTE_per_person"] * out["availability_factor"]
    )

    # Totals
    out["scheduled_ward_WTE_total"] = out["headcount"] * out["scheduled_ward_WTE_per_person"]
    out["ward_WTE_total"] = out["headcount"] * out["ward_effective_WTE_per_person"]

    # On-call investment metrics: define "investment" as scheduled WTE in on-call groups
    # and "lost" as the portion removed by oncall_loss.
    out["scheduled_establishment_WTE_per_person"] = (
        out["base_WTE"]
        * out["pattern_factor"]
        * out["days_factor"]
        * out["leave_factor"]
    )
    out["scheduled_establishment_WTE_total"] = out["headcount"] * out["scheduled_establishment_WTE_per_person"]

    out["oncall_WTE_investment"] = out["scheduled_establishment_WTE_total"].where(out["oncall_loss"] > 0, 0)
    out["oncall_WTE_lost"] = (out["scheduled_establishment_WTE_total"] * out["oncall_loss"]).where(out["oncall_loss"] > 0, 0)
    out["oncall_ward_WTE_remaining"] = out["scheduled_ward_WTE_total"].where(out["oncall_loss"] > 0, 0)

    return out

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

st.sidebar.header("Cover / Reliability")
required_staff_per_day = st.sidebar.number_input(
    "Required staff available per weekday day (pooled)",
    min_value=1, max_value=60, value=9, step=1
)
reliability_target = st.sidebar.slider("Reliability target", 0.50, 0.99, 0.95, 0.01)
working_days_per_year = st.sidebar.number_input(
    "Weekday day shifts per year (locum estimate)",
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
# KEY TOTALS
# -----------------------------
total_headcount = float(scenario["headcount"].sum())

total_scheduled_ward_wte = float(scenario["scheduled_ward_WTE_total"].sum())  # before sickness/dev
total_expected_ward_wte = float(scenario["ward_WTE_total"].sum())            # mean after sickness/dev

overall_oncall_invest_wte = float(scenario["oncall_WTE_investment"].sum())
overall_oncall_lost_wte = float(scenario["oncall_WTE_lost"].sum())

# -----------------------------
# PROBABILISTIC COVER MODEL (pooled)
# -----------------------------
# Interpret "scheduled ward WTE" as the number of pooled WTE-units, N.
# Then the effective availability probability p is the ratio:
# mean available / scheduled capacity.
if total_scheduled_ward_wte > 0:
    p_eff = max(0.0, min(1.0, total_expected_ward_wte / total_scheduled_ward_wte))
else:
    p_eff = 0.0

# Convert scheduled WTE into an integer N for the binomial model.
# Rounding is a pragmatic approximation; you can also use floor/ceil as preferred.
N_current = int(round(total_scheduled_ward_wte))

prob_meet = prob_at_least(N_current, int(required_staff_per_day), p_eff)
shortfall_per_day = expected_shortfall(N_current, int(required_staff_per_day), p_eff)
locum_shifts_per_year = shortfall_per_day * float(working_days_per_year)

# Required N (WTE) to hit target reliability
N_required = required_n_for_target(int(required_staff_per_day), p_eff, float(reliability_target), n_max=200)
wte_gap_to_target = max(0, N_required - N_current)

# -----------------------------
# HEADCOUNT BY GRADE (as before)
# -----------------------------
hc_by_grade = (
    scenario.groupby("grade", dropna=False)["headcount"]
    .sum()
    .reindex(["Foundation", "Core", "GPST", "ACP", "Other"])
    .fillna(0)
)

# On-call commitment by grade (WTE investment and WTE lost)
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

# -----------------------------
# OUTPUT
# -----------------------------
st.title("Geriatrics Staffing Capacity Model")

k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Expected ward-facing WTE (mean)", f"{total_expected_ward_wte:.2f}")
k2.metric("Scheduled ward WTE (before sickness/dev)", f"{total_scheduled_ward_wte:.2f}")
k3.metric("P(meet cover) with flexing", f"{100*prob_meet:.1f}%")
k4.metric("Locum day-shifts/year (expected)", f"{locum_shifts_per_year:.0f}")
k5.metric("WTE needed for target reliability", f"{N_required} (gap {wte_gap_to_target:+})")
k6.metric("On-call WTE lost", f"{overall_oncall_lost_wte:.2f}")

st.caption(
    f"Cover requirement: ≥{required_staff_per_day} staff available per weekday day (pooled across wards). "
    f"Effective availability p ≈ {p_eff:.3f} derived from sickness/dev assumptions. "
    f"Binomial model uses N ≈ round(scheduled ward WTE) = {N_current}."
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

st.subheader("Ward-facing WTE by staff group (expected mean)")
plot_df = scenario[["staff_group", "ward_WTE_total"]].copy()
plot_df["staff_group"] = plot_df["staff_group"].astype(str)
st.bar_chart(plot_df.set_index("staff_group")["ward_WTE_total"])

st.subheader("Detailed Workforce Table")
detail = scenario[[
    "grade", "staff_group", "headcount",
    "leave_factor", "oncall_loss", "dev_days",
    "availability_factor",
    "scheduled_ward_WTE_total", "ward_WTE_total",
    "oncall_WTE_investment", "oncall_WTE_lost"
]].sort_values(["grade", "staff_group"])

st.dataframe(detail, use_container_width=True)
