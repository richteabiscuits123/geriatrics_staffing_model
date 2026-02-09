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

st.sidebar.header("Assu


