from decimal import Decimal

import pandas as pd

from or_aws_fleet.route_visualization import (
    calculate_cost_efficiency_summary,
    prepare_route_map_data,
)


def test_prepare_route_map_data_converts_database_decimals_for_map_and_ranking():
    routes = pd.DataFrame(
        [
            {
                "origin": "FAC_01",
                "destiny": "DC_01",
                "origin_latitude": Decimal("-23.5505"),
                "origin_longitude": Decimal("-46.6333"),
                "destiny_latitude": Decimal("-8.0476"),
                "destiny_longitude": Decimal("-34.8770"),
                "distance_km": Decimal("2100.0"),
                "google_driving_distance_km": Decimal("2734.3"),
                "vehicle_count": Decimal("3"),
                "load_weight_kg": Decimal("2386"),
                "load_pallets": Decimal("9.2"),
                "load_boxes": Decimal("100"),
                "freight_cost": Decimal("24608.88"),
                "average_occupancy": Decimal("0.446"),
            },
            {
                "origin": "FAC_02",
                "destiny": "DC_02",
                "origin_latitude": Decimal("-19.9167"),
                "origin_longitude": Decimal("-43.9345"),
                "destiny_latitude": Decimal("-22.9068"),
                "destiny_longitude": Decimal("-43.1729"),
                "distance_km": Decimal("450.5"),
                "google_driving_distance_km": None,
                "vehicle_count": Decimal("1"),
                "load_weight_kg": Decimal("900"),
                "load_pallets": Decimal("2"),
                "load_boxes": Decimal("40"),
                "freight_cost": Decimal("1200"),
                "average_occupancy": Decimal("0.75"),
            },
        ]
    )

    prepared = prepare_route_map_data(routes)

    assert prepared["display_distance_km"].dtype.kind == "f"
    assert prepared.nlargest(1, "display_distance_km").iloc[0]["route"] == "FAC_01 → DC_01"
    assert prepared.iloc[1]["display_distance_km"] == 450.5
    assert round(prepared.iloc[0]["glow_width"], 1) == 7.2


def test_cost_efficiency_summary_projects_forecast_from_current_vehicle_cost():
    routes = pd.DataFrame(
        [
            {"freight_cost": 600, "average_occupancy": 0.5, "vehicle_count": 2},
            {"freight_cost": 400, "average_occupancy": 0.75, "vehicle_count": 2},
        ]
    )
    forecast = pd.DataFrame(
        [
            {"scenario": "P50", "vehicle_count": 10, "average_occupancy": 0.8},
            {"scenario": "P90", "vehicle_count": 12, "average_occupancy": 0.7},
        ]
    )

    summary = calculate_cost_efficiency_summary(routes, forecast)

    assert summary["current_cost"] == 1_000
    assert summary["current_avoidable"] == 400
    assert summary["forecast_cost"] == 2_500
    assert round(summary["forecast_avoidable"], 2) == 500
