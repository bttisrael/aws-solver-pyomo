from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pydeck as pdk
import streamlit as st

from or_aws_fleet.api import SolveRequest, VehicleTypeParameter, solve
from or_aws_fleet.dashboard_data import (
    available_programming_dates,
    daily_programming,
    forecast_load_plan,
    forecast_optimization_summary,
    forecast_vehicle_summary,
    latest_forecast_run,
    operational_load_plan,
    optimization_runs,
    route_network,
    vehicle_master_data,
    vehicle_summary,
)
from or_aws_fleet.dsql_forecast import run_daily_forecast
from or_aws_fleet.programming_model import VehicleType


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
    return optimization_runs(limit=5)


@st.cache_data(ttl=60, show_spinner=False)
def get_vehicle_master():
    return vehicle_master_data()


@st.cache_data(ttl=60, show_spinner=False)
def get_route_network():
    return route_network()


def date_selector(key: str):
    dates = get_dates()
    if not dates:
        st.error("No dates are available in daily_programming.")
        st.stop()
    return st.selectbox("Programming date", dates, format_func=lambda value: value.isoformat(), key=key)


def configuration_screen() -> None:
    st.title("⚙️ Solver Configuration")
    st.caption("Edit every vehicle type and optimize fleet count plus route freight cost.")
    programming_date = date_selector("configuration_date")
    vehicles = get_vehicle_master().copy()
    if vehicles.empty:
        st.error("No vehicle types are available in logistics.vehicle_master_data.")
        return
    vehicles.insert(0, "enabled", True)

    with st.form("solver_configuration"):
        st.markdown('<div class="section-title">Vehicle fleet parameters</div>', unsafe_allow_html=True)
        edited_vehicles = st.data_editor(
            vehicles,
            hide_index=True,
            use_container_width=True,
            disabled=["vehicle_type"],
            column_config={
                "enabled": st.column_config.CheckboxColumn("Enabled"),
                "vehicle_type": st.column_config.TextColumn("Vehicle type"),
                "vehicle_capacity_m3": st.column_config.NumberColumn(
                    "Capacity (mÂ³)", min_value=0.01, format="%.2f"
                ),
                "vehicle_capacity_kg": st.column_config.NumberColumn(
                    "Capacity (kg)", min_value=1.0, format="%.0f"
                ),
                "freight_cost_per_km": st.column_config.NumberColumn(
                    "Freight cost/km", min_value=0.0, format="%.2f"
                ),
                "vehicle_capacity_pallets": st.column_config.NumberColumn(
                    "Pallet capacity", min_value=1.0, format="%.0f"
                ),
            },
            key="vehicle_configuration_editor",
        )
        left, middle, right = st.columns(3)
        with left:
            vehicle_count_weight = st.number_input(
                "Vehicle-count weight", min_value=0.001, value=1.0, step=0.1,
                help="Penalty applied to each activated vehicle.",
            )
        with middle:
            freight_cost_weight = st.number_input(
                "Freight-cost weight", min_value=0.0, value=0.001, step=0.001,
                format="%.4f",
                help="Multiplier applied to distance Ã— freight cost/km.",
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
                ("Vehicle capacities", "mÂ³, kg, pallets", "Applied separately by vehicle type."),
                ("Vehicle-count weight", "objective", "Controls fleet-size importance."),
                ("Freight-cost weight", "objective", "Controls route-cost importance."),
                ("Solver time limit", "seconds/route", "Balances solve quality and response time."),
                ("Programming date", "date", "Selects the daily_programming input snapshot."),
            ],
            columns=["Parameter", "Unit", "Description"],
        ),
        hide_index=True,
        use_container_width=True,
    )

    if submitted:
        selected_vehicles = [
            VehicleTypeParameter(**record)
            for record in edited_vehicles.to_dict(orient="records")
            if bool(record["enabled"])
        ]
        if not selected_vehicles:
            st.error("Enable at least one vehicle type before running the optimizer.")
            return
        with st.spinner("Optimizing the selected day and the next 21 forecast days..."):
            try:
                result = solve(
                    SolveRequest(
                        programming_date=programming_date,
                        vehicle_types=selected_vehicles,
                        vehicle_count_weight=vehicle_count_weight,
                        freight_cost_weight=freight_cost_weight,
                        time_limit_seconds=int(time_limit),
                        persist=persist,
                    )
                )
                forecast_run_id = run_daily_forecast(
                    run_date=programming_date,
                    time_limit_seconds=int(time_limit),
                    vehicle_types=[
                        VehicleType(
                            vehicle_type=item.vehicle_type,
                            vehicle_capacity_m3=item.vehicle_capacity_m3,
                            vehicle_capacity_kg=item.vehicle_capacity_kg,
                            freight_cost_per_km=item.freight_cost_per_km,
                            vehicle_capacity_pallets=item.vehicle_capacity_pallets,
                        )
                        for item in selected_vehicles
                    ],
                    vehicle_count_weight=vehicle_count_weight,
                    freight_cost_weight=freight_cost_weight,
                )
            except Exception as exc:
                st.error(f"Optimization could not be completed: {exc}")
            else:
                get_runs.clear()
                get_latest_forecast.clear()
                get_forecast_summary.clear()
                st.session_state["selected_run_id"] = result.run_id
                st.success(
                    f"Simulation completed: {result.vehicles} vehicles across "
                    f"{result.routes} routes. The 21-day forecast optimization "
                    f"was also refreshed (run {forecast_run_id[:8]}). Status: {result.status}."
                )
                cols = st.columns(5)
                cols[0].metric("Vehicles", result.vehicles)
                cols[1].metric("Routes", result.routes)
                cols[2].metric("Weight", f"{result.total_weight_kg:,.0f} kg")
                cols[3].metric("Pallet demand", f"{result.total_pallets:,.1f}")
                cols[4].metric("Freight cost", f"{result.total_freight_cost:,.2f}")


def results_screen() -> None:
    st.title("📊 Actual Optimization")
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
    metrics = st.columns(7)
    metrics[0].metric("Vehicles", int(selected["vehicle_count"]))
    metrics[1].metric("Routes", int(selected["route_count"]))
    metrics[2].metric("Load items", len(loads))
    metrics[3].metric("Boxes", f"{vehicles['load_boxes'].sum():,.0f}" if not vehicles.empty else "0")
    metrics[4].metric("Weight", f"{float(selected['total_weight_kg']):,.0f} kg")
    metrics[5].metric("Avg. occupancy", f"{occupancy:.1%}")
    metrics[6].metric("Freight cost", f"{float(selected['total_freight_cost']):,.2f}")
    st.caption(
        f"Programming date: {selected['programming_date']} · Solver: {selected['solver_name']} · "
        f"Status: {selected['status']} · Created: {selected['created_at']}"
    )

    st.markdown('<div class="section-title">Route and vehicle summary</div>', unsafe_allow_html=True)
    route_view = vehicles.rename(
        columns={
            "origin": "Origin", "destiny": "Destination", "vehicle_id": "Vehicle",
            "vehicle_type": "Vehicle type",
            "load_pallets": "Pallet demand", "load_boxes": "Boxes",
            "load_weight_kg": "Weight (kg)", "load_volume_m3": "Volume (mÂ³)",
            "weight_utilization": "Weight occupancy",
            "pallet_utilization": "Pallet occupancy",
            "volume_utilization": "Volume occupancy",
            "route_distance_km": "Distance (km)", "freight_cost": "Freight cost",
        }
    )
    route_view["Weight occupancy"] = route_view["Weight occupancy"] * 100
    route_view["Pallet occupancy"] = route_view["Pallet occupancy"] * 100
    route_view["Volume occupancy"] = route_view["Volume occupancy"] * 100
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


def route_network_screen() -> None:
    st.title("🗺️ Route Network")
    st.caption(
        "All factory-to-distribution-center lanes. Operational metrics use the latest "
        "Actual Optimization run."
    )
    routes = get_route_network().copy()
    if routes.empty:
        st.info("No routes are available in logistics.route.")
        return

    origins = st.multiselect("Origins", sorted(routes["origin"].unique()))
    destinations = st.multiselect("Destinations", sorted(routes["destiny"].unique()))
    filtered = routes
    if origins:
        filtered = filtered[filtered["origin"].isin(origins)]
    if destinations:
        filtered = filtered[filtered["destiny"].isin(destinations)]
    if filtered.empty:
        st.warning("No routes match the selected filters.")
        return

    filtered = filtered.copy()
    filtered["display_distance_km"] = filtered["google_driving_distance_km"].fillna(
        filtered["distance_km"]
    )
    filtered["occupancy_percent"] = filtered["average_occupancy"] * 100
    filtered["line_width"] = filtered["vehicle_count"].clip(lower=1, upper=12)
    filtered["route"] = filtered["origin"] + " → " + filtered["destiny"]

    metrics = st.columns(6)
    metrics[0].metric("Routes", f"{len(filtered):,}")
    metrics[1].metric("Factories", filtered["origin"].nunique())
    metrics[2].metric("Distribution centers", filtered["destiny"].nunique())
    metrics[3].metric("Route distance", f"{filtered['display_distance_km'].sum():,.0f} km")
    metrics[4].metric("Latest-run vehicles", f"{int(filtered['vehicle_count'].sum()):,}")
    metrics[5].metric("Latest freight cost", f"{filtered['freight_cost'].sum():,.2f}")

    origin_nodes = filtered[
        ["origin", "origin_latitude", "origin_longitude"]
    ].drop_duplicates().rename(
        columns={
            "origin": "location",
            "origin_latitude": "latitude",
            "origin_longitude": "longitude",
        }
    )
    origin_nodes["location_type"] = "Factory"
    origin_nodes["color"] = [[30, 136, 229, 220]] * len(origin_nodes)
    destination_nodes = filtered[
        ["destiny", "destiny_latitude", "destiny_longitude"]
    ].drop_duplicates().rename(
        columns={
            "destiny": "location",
            "destiny_latitude": "latitude",
            "destiny_longitude": "longitude",
        }
    )
    destination_nodes["location_type"] = "Distribution center"
    destination_nodes["color"] = [[255, 75, 75, 220]] * len(destination_nodes)
    nodes = pd.concat([origin_nodes, destination_nodes], ignore_index=True)

    midpoint_latitude = float(nodes["latitude"].mean())
    midpoint_longitude = float(nodes["longitude"].mean())
    deck = pdk.Deck(
        map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        initial_view_state=pdk.ViewState(
            latitude=midpoint_latitude,
            longitude=midpoint_longitude,
            zoom=3.2,
            pitch=25,
        ),
        layers=[
            pdk.Layer(
                "ArcLayer",
                data=filtered,
                get_source_position=["origin_longitude", "origin_latitude"],
                get_target_position=["destiny_longitude", "destiny_latitude"],
                get_source_color=[30, 136, 229, 150],
                get_target_color=[255, 75, 75, 170],
                get_width="line_width",
                width_min_pixels=1,
                width_max_pixels=8,
                pickable=True,
                auto_highlight=True,
            ),
            pdk.Layer(
                "ScatterplotLayer",
                data=nodes,
                get_position=["longitude", "latitude"],
                get_fill_color="color",
                get_radius=30_000,
                radius_min_pixels=5,
                radius_max_pixels=12,
                pickable=True,
            ),
        ],
        tooltip={
            "html": (
                "<b>{route}{location}</b><br/>"
                "Driving distance: {display_distance_km} km<br/>"
                "Vehicles: {vehicle_count}<br/>"
                "Weight: {load_weight_kg} kg<br/>"
                "Freight cost: {freight_cost}"
            ),
            "style": {"backgroundColor": "#173b6c", "color": "white"},
        },
    )
    st.pydeck_chart(deck, use_container_width=True, height=620)
    st.caption("Blue markers are factories; red markers are distribution centers.")

    st.markdown('<div class="section-title">Route metrics</div>', unsafe_allow_html=True)
    route_name = st.selectbox("Route details", filtered["route"].tolist())
    selected = filtered.loc[filtered["route"] == route_name].iloc[0]
    details = st.columns(6)
    details[0].metric("Driving distance", f"{float(selected['display_distance_km']):,.1f} km")
    details[1].metric("Vehicles", f"{int(selected['vehicle_count']):,}")
    details[2].metric("Weight", f"{float(selected['load_weight_kg']):,.0f} kg")
    details[3].metric("Pallet demand", f"{float(selected['load_pallets']):,.1f}")
    details[4].metric("Avg. occupancy", f"{float(selected['average_occupancy']):.1%}")
    details[5].metric("Freight cost", f"{float(selected['freight_cost']):,.2f}")

    route_table = filtered[
        [
            "origin", "destiny", "display_distance_km", "vehicle_count",
            "load_weight_kg", "load_pallets", "load_boxes",
            "occupancy_percent", "freight_cost",
        ]
    ].rename(
        columns={
            "origin": "Origin",
            "destiny": "Destination",
            "display_distance_km": "Driving distance (km)",
            "vehicle_count": "Vehicles",
            "load_weight_kg": "Weight (kg)",
            "load_pallets": "Pallet demand",
            "load_boxes": "Boxes",
            "occupancy_percent": "Avg. occupancy (%)",
            "freight_cost": "Freight cost",
        }
    )
    st.dataframe(
        route_table,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Avg. occupancy (%)": st.column_config.ProgressColumn(
                format="%.1f%%", min_value=0, max_value=100
            )
        },
    )


@st.cache_data(ttl=60, show_spinner=False)
def get_latest_forecast():
    return latest_forecast_run()


@st.cache_data(ttl=60, show_spinner=False)
def get_forecast_summary(run_id: str):
    return forecast_optimization_summary(run_id)


def forecast_optimized_screen() -> None:
    st.title("Forecast Optimization")
    st.caption("Rolling 21-day demand forecast with P50 expected and P90 capacity plans.")
    run = get_latest_forecast()
    if run.empty:
        st.info("The first scheduled forecast has not completed yet.")
        return
    selected_run = run.iloc[0]
    run_id = str(selected_run["run_id"])
    summary = get_forecast_summary(run_id)
    if summary.empty:
        st.warning("This forecast run does not contain optimization results.")
        return

    p50 = summary.loc[summary["scenario"] == "P50"].copy()
    p90 = summary.loc[summary["scenario"] == "P90"].copy()
    metrics = st.columns(6)
    metrics[0].metric("Horizon", f"{int(selected_run['horizon_days'])} days")
    metrics[1].metric("P50 vehicles", f"{int(p50['vehicle_count'].sum()):,}")
    metrics[2].metric("P90 vehicles", f"{int(p90['vehicle_count'].sum()):,}")
    metrics[3].metric("P50 units", f"{int(p50['total_units'].sum()):,}")
    metrics[4].metric("P50 weight", f"{float(p50['total_weight_kg'].sum()):,.0f} kg")
    metrics[5].metric("Avg. occupancy", f"{float(p50['average_occupancy'].mean()):.1%}")
    st.caption(
        f"Run date: {selected_run['forecast_run_date']} | Model: {selected_run['model_version']} | "
        f"Status: {selected_run['status']}"
    )

    st.markdown('<div class="section-title">21-day vehicle requirement</div>', unsafe_allow_html=True)
    chart = summary.pivot(index="forecast_date", columns="scenario", values="vehicle_count")
    st.line_chart(chart, color=["#1f77b4", "#ff4b4b"])
    st.caption("P50 is the expected operating plan. P90 is the conservative capacity plan.")

    st.markdown('<div class="section-title">Forecast accuracy and governance</div>', unsafe_allow_html=True)
    accuracy = st.columns(5)
    accuracy[0].metric("WAPE", "Pending" if pd.isna(selected_run["wape"]) else f"{float(selected_run['wape']):.1%}")
    accuracy[1].metric("MASE", "Pending" if pd.isna(selected_run["mase"]) else f"{float(selected_run['mase']):.2f}")
    accuracy[2].metric("Bias", "Pending" if pd.isna(selected_run["bias"]) else f"{float(selected_run['bias']):.1%}")
    accuracy[3].metric(
        "Interval coverage", "Pending" if pd.isna(selected_run["interval_coverage"])
        else f"{float(selected_run['interval_coverage']):.1%}",
    )
    accuracy[4].metric("Retraining", "Recommended" if selected_run["retraining_recommended"] else "Not required")

    st.markdown('<div class="section-title">Daily optimized plan</div>', unsafe_allow_html=True)
    dates = sorted(summary["forecast_date"].unique())
    left, right = st.columns(2)
    selected_date = left.selectbox("Forecast date", dates, format_func=lambda value: value.isoformat())
    scenario = right.radio("Planning scenario", ["P50", "P90"], horizontal=True)
    vehicles = forecast_vehicle_summary(run_id, selected_date, scenario)
    loads = forecast_load_plan(run_id, selected_date, scenario)
    st.dataframe(vehicles, hide_index=True, use_container_width=True)
    st.markdown('<div class="section-title">Detailed operational load</div>', unsafe_allow_html=True)
    st.dataframe(loads, hide_index=True, use_container_width=True, height=500)
    st.download_button(
        "Download forecast load plan as CSV", loads.to_csv(index=False).encode("utf-8"),
        file_name=f"forecast_load_plan_{selected_date}_{scenario}.csv", mime="text/csv",
    )


st.sidebar.title("🚚 Load Optimizer")
st.sidebar.caption(f"Updated {datetime.now(ZoneInfo('America/Sao_Paulo')):%Y-%m-%d %H:%M}")
screen = st.sidebar.radio(
    "Navigation",
    [
        "Solver Configuration",
        "Actual Optimization",
        "Forecast Optimization",
        "Route Network",
        "Daily Programming",
    ],
)

try:
    if screen == "Solver Configuration":
        configuration_screen()
    elif screen == "Actual Optimization":
        results_screen()
    elif screen == "Forecast Optimization":
        forecast_optimized_screen()
    elif screen == "Route Network":
        route_network_screen()
    else:
        programming_screen()
except Exception as exc:
    st.error("The dashboard could not access its AWS data source.")
    st.exception(exc)
