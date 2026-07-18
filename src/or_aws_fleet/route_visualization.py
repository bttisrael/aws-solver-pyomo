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
