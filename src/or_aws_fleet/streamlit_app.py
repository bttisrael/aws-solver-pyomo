from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from or_aws_fleet.api import SolveRequest, solve
from or_aws_fleet.dashboard_data import (
    available_programming_dates,
    daily_programming,
    operational_load_plan,
    optimization_runs,
    vehicle_summary,
)


st.set_page_config(page_title="Beverage Load Optimizer", page_icon="🚚", layout="wide")
st.markdown(
    """
    <style>
    .stApp {background: #f5f8fc;}
    [data-testid="stSidebar"] {background: #173b6c;}
    [data-testid="stSidebar"] * {color: white;}
    h1, h2, h3 {color: #173b6c;}
    div[data-testid="stMetric"] {
        background: white; border: 1px solid #d7e2ef; border-radius: 8px;
        padding: 12px; box-shadow: 0 2px 5px rgba(20, 55, 95, .08);
    }
    .section-title {
        background: #173b6c; color: white; padding: 10px 14px;
        border-radius: 6px; font-size: 1.2rem; font-weight: 700; margin: 12px 0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=60, show_spinner=False)
def get_dates():
    return available_programming_dates()


@st.cache_data(ttl=30, show_spinner=False)
def get_runs():
    return optimization_runs()


def date_selector(key: str):
    dates = get_dates()
    if not dates:
        st.error("No dates are available in daily_programming.")
        st.stop()
    return st.selectbox("Programming date", dates, format_func=lambda value: value.isoformat(), key=key)


def configuration_screen() -> None:
    st.title("⚙️ Solver Configuration")
    st.caption("Edit the operating limits and run a new scenario without changing code.")
    programming_date = date_selector("configuration_date")

    with st.form("solver_configuration"):
        left, middle, right = st.columns(3)
        with left:
            max_weight = st.number_input(
                "Maximum vehicle weight (kg)", min_value=1_000.0, max_value=80_000.0,
                value=25_000.0, step=500.0,
                help="Maximum total product weight assigned to one vehicle.",
            )
        with middle:
            max_pallets = st.number_input(
                "Maximum pallet positions", min_value=1.0, max_value=120.0,
                value=60.0, step=1.0,
                help="Maximum pallet-equivalent capacity assigned to one vehicle.",
            )
        with right:
            time_limit = st.number_input(
                "Solver time limit (seconds)", min_value=5, max_value=300,
                value=60, step=5,
                help="Maximum optimization time for each origin-destination route.",
            )
        persist = st.checkbox("Save this simulation to the results database", value=True)
        submitted = st.form_submit_button("🚀 Run optimization", type="primary", use_container_width=True)

    st.markdown('<div class="section-title">Parameter reference</div>', unsafe_allow_html=True)
    st.dataframe(
        pd.DataFrame(
            [
                ("Maximum vehicle weight", "kg", "Controls the vehicle weight constraint."),
                ("Maximum pallet positions", "positions", "Controls vehicle floor/capacity usage."),
                ("Solver time limit", "seconds/route", "Balances solve quality and response time."),
                ("Programming date", "date", "Selects the daily_programming input snapshot."),
            ],
            columns=["Parameter", "Unit", "Description"],
        ),
        hide_index=True,
        use_container_width=True,
    )

    if submitted:
        with st.spinner("Reading daily programming and optimizing all routes..."):
            try:
                result = solve(
                    SolveRequest(
                        programming_date=programming_date,
                        max_weight_kg=max_weight,
                        max_pallets=max_pallets,
                        time_limit_seconds=int(time_limit),
                        persist=persist,
                    )
                )
            except Exception as exc:
                st.error(f"Optimization could not be completed: {exc}")
            else:
                get_runs.clear()
                st.session_state["selected_run_id"] = result.run_id
                st.success(
                    f"Simulation completed: {result.vehicles} vehicles across "
                    f"{result.routes} routes. Status: {result.status}."
                )
                cols = st.columns(4)
                cols[0].metric("Vehicles", result.vehicles)
                cols[1].metric("Routes", result.routes)
                cols[2].metric("Weight", f"{result.total_weight_kg:,.0f} kg")
                cols[3].metric("Pallet demand", f"{result.total_pallets:,.1f}")


def results_screen() -> None:
    st.title("📊 Optimization Results")
    runs = get_runs()
    if runs.empty:
        st.info("Run an optimization to create the first result.")
        return

    labels = {
        row.run_id: f"{row.programming_date} · {str(row.run_id)[:8]} · {row.status} · {row.vehicle_count} vehicles"
        for row in runs.itertuples()
    }
    preferred = st.session_state.get("selected_run_id")
    run_ids = runs["run_id"].astype(str).tolist()
    index = run_ids.index(preferred) if preferred in run_ids else 0
    run_id = st.selectbox("Optimization run", run_ids, index=index, format_func=labels.get)
    selected = runs.loc[runs["run_id"].astype(str) == run_id].iloc[0]
    vehicles = vehicle_summary(run_id)
    loads = operational_load_plan(run_id)

    st.markdown('<div class="section-title">Macro results</div>', unsafe_allow_html=True)
    occupancy = vehicles[["weight_utilization", "pallet_utilization"]].max(axis=1).mean() if not vehicles.empty else 0
    metrics = st.columns(6)
    metrics[0].metric("Vehicles", int(selected["vehicle_count"]))
    metrics[1].metric("Routes", int(selected["route_count"]))
    metrics[2].metric("Load items", len(loads))
    metrics[3].metric("Boxes", f"{vehicles['load_boxes'].sum():,.0f}" if not vehicles.empty else "0")
    metrics[4].metric("Weight", f"{float(selected['total_weight_kg']):,.0f} kg")
    metrics[5].metric("Avg. occupancy", f"{occupancy:.1%}")
    st.caption(
        f"Programming date: {selected['programming_date']} · Solver: {selected['solver_name']} · "
        f"Status: {selected['status']} · Created: {selected['created_at']}"
    )

    st.markdown('<div class="section-title">Route and vehicle summary</div>', unsafe_allow_html=True)
    route_view = vehicles.rename(
        columns={
            "origin": "Origin", "destiny": "Destination", "vehicle_id": "Vehicle",
            "load_pallets": "Pallet demand", "load_boxes": "Boxes",
            "load_weight_kg": "Weight (kg)", "weight_utilization": "Weight occupancy",
            "pallet_utilization": "Pallet occupancy",
        }
    )
    route_view["Weight occupancy"] = route_view["Weight occupancy"] * 100
    route_view["Pallet occupancy"] = route_view["Pallet occupancy"] * 100
    st.dataframe(
        route_view,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Weight occupancy": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100),
            "Pallet occupancy": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100),
        },
    )

    st.markdown('<div class="section-title">Detailed operational load plan</div>', unsafe_allow_html=True)
    if loads.empty:
        st.warning("This run does not have a persisted operational load plan.")
        return
    origin_filter = st.multiselect("Origin", sorted(loads["origin"].unique()))
    destination_filter = st.multiselect("Destination", sorted(loads["destination"].unique()))
    filtered = loads
    if origin_filter:
        filtered = filtered[filtered["origin"].isin(origin_filter)]
    if destination_filter:
        filtered = filtered[filtered["destination"].isin(destination_filter)]
    st.dataframe(
        filtered.rename(
            columns={
                "origin": "Origin", "destination": "Destination", "vehicle_id": "Vehicle",
                "position_number": "Position", "load_level": "Level", "load_item_label": "Load item",
                "material_code": "Material", "boxes": "Boxes", "units_by_material": "Units/material",
                "total_units": "Total units", "weight_kg": "Weight (kg)",
                "pallet_volume": "Pallet volume", "demand_id": "Demand ID",
            }
        ),
        hide_index=True,
        use_container_width=True,
        height=560,
    )
    st.download_button(
        "Download operational plan as CSV",
        filtered.to_csv(index=False).encode("utf-8"),
        file_name=f"operational_load_plan_{run_id}.csv",
        mime="text/csv",
    )


def programming_screen() -> None:
    st.title("📅 Daily Programming")
    programming_date = date_selector("programming_date")
    with st.spinner("Loading daily programming..."):
        frame = daily_programming(programming_date)
    if frame.empty:
        st.warning("No programming rows were found for this date.")
        return

    metrics = st.columns(5)
    metrics[0].metric("Demand lines", f"{len(frame):,}")
    metrics[1].metric("Origins", frame["origin"].nunique())
    metrics[2].metric("Destinations", frame["destiny"].nunique())
    metrics[3].metric("Units", f"{frame['units'].sum():,.0f}")
    metrics[4].metric("Weight", f"{frame['total_weight_kg'].sum():,.0f} kg")

    origin = st.multiselect("Filter origins", sorted(frame["origin"].unique()))
    destiny = st.multiselect("Filter destinations", sorted(frame["destiny"].unique()))
    filtered = frame
    if origin:
        filtered = filtered[filtered["origin"].isin(origin)]
    if destiny:
        filtered = filtered[filtered["destiny"].isin(destiny)]
    st.dataframe(filtered, hide_index=True, use_container_width=True, height=620)
    st.download_button(
        "Download daily programming as CSV",
        filtered.to_csv(index=False).encode("utf-8"),
        file_name=f"daily_programming_{programming_date}.csv",
        mime="text/csv",
    )


st.sidebar.title("🚚 Load Optimizer")
st.sidebar.caption(f"Updated {datetime.now(ZoneInfo('America/Sao_Paulo')):%Y-%m-%d %H:%M}")
screen = st.sidebar.radio(
    "Navigation",
    ["Solver Configuration", "Optimization Results", "Daily Programming"],
)

try:
    if screen == "Solver Configuration":
        configuration_screen()
    elif screen == "Optimization Results":
        results_screen()
    else:
        programming_screen()
except Exception as exc:
    st.error("The dashboard could not access its AWS data source.")
    st.exception(exc)
