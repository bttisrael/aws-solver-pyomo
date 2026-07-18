from __future__ import annotations

import pandas as pd


ROUTE_NUMERIC_COLUMNS = (
    "origin_latitude",
    "origin_longitude",
    "destiny_latitude",
    "destiny_longitude",
    "distance_km",
    "google_driving_distance_km",
    "vehicle_count",
    "load_weight_kg",
    "load_pallets",
    "load_boxes",
    "freight_cost",
    "average_occupancy",
)

ROUTE_COORDINATE_COLUMNS = (
    "origin_latitude",
    "origin_longitude",
    "destiny_latitude",
    "destiny_longitude",
)


def prepare_route_map_data(routes: pd.DataFrame) -> pd.DataFrame:
    """Return route data with browser-safe numeric values for PyDeck."""
    prepared = routes.copy()
    for column in ROUTE_NUMERIC_COLUMNS:
        if column in prepared:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")

    prepared["display_distance_km"] = prepared[
        "google_driving_distance_km"
    ].fillna(prepared["distance_km"]).astype(float)
    prepared["occupancy_percent"] = prepared["average_occupancy"].fillna(0).astype(float) * 100
    prepared["line_width"] = (
        prepared["vehicle_count"].fillna(0).clip(lower=1, upper=12).astype(float)
    )
    prepared["glow_width"] = prepared["line_width"] * 2.4
    prepared["route"] = prepared["origin"].astype(str) + " → " + prepared["destiny"].astype(str)
    return prepared.dropna(subset=list(ROUTE_COORDINATE_COLUMNS)).reset_index(drop=True)


def calculate_cost_efficiency_summary(
    routes: pd.DataFrame,
    forecast_summary: pd.DataFrame,
) -> dict[str, float]:
    """Estimate current and forecast cost opportunity from unused vehicle capacity."""
    current = routes.copy()
    for column in ("freight_cost", "average_occupancy", "vehicle_count"):
        current[column] = pd.to_numeric(current[column], errors="coerce").fillna(0.0)
    current["average_occupancy"] = current["average_occupancy"].clip(0.0, 1.0)
    current_cost = float(current["freight_cost"].sum())
    current_avoidable = float(
        (current["freight_cost"] * (1.0 - current["average_occupancy"])).sum()
    )
    current_vehicles = float(current["vehicle_count"].sum())
    cost_per_vehicle = current_cost / current_vehicles if current_vehicles > 0 else 0.0

    forecast = forecast_summary.loc[forecast_summary["scenario"] == "P50"].copy()
    if forecast.empty:
        forecast_cost = 0.0
        forecast_avoidable = 0.0
    else:
        forecast["vehicle_count"] = pd.to_numeric(
            forecast["vehicle_count"], errors="coerce"
        ).fillna(0.0)
        forecast["average_occupancy"] = pd.to_numeric(
            forecast["average_occupancy"], errors="coerce"
        ).fillna(0.0).clip(0.0, 1.0)
        forecast["projected_cost"] = forecast["vehicle_count"] * cost_per_vehicle
        forecast_cost = float(forecast["projected_cost"].sum())
        forecast_avoidable = float(
            (forecast["projected_cost"] * (1.0 - forecast["average_occupancy"])).sum()
        )

    return {
        "current_cost": current_cost,
        "current_avoidable": current_avoidable,
        "forecast_cost": forecast_cost,
        "forecast_avoidable": forecast_avoidable,
    }
