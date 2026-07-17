from datetime import date
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def load_handler():
    path = Path(__file__).resolve().parents[1] / "lambda" / "dsql_daily_demand" / "handler.py"
    spec = spec_from_file_location("dsql_daily_demand_handler", path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_demand_applies_units_multiplier() -> None:
    handler = load_handler()
    materials = [("MAT-001", 12), ("MAT-002", 24)]
    normal, _ = handler.generate_rows(date(2026, 7, 18), materials, 100, 271828, 1)
    scaled, _ = handler.generate_rows(date(2026, 7, 18), materials, 100, 271828, 4)
    assert len(normal) == len(scaled)
    assert [row[6] * 4 for row in normal] == [row[6] for row in scaled]


def test_programming_rows_include_google_route_distance() -> None:
    handler = load_handler()
    assert "google_driving_distance_km" in handler.CREATE_PROGRAMMING_SQL
    assert "route.google_driving_distance_km" in handler.INSERT_PROGRAMMING_SQL
    assert "JOIN logistics.route AS route" in handler.INSERT_PROGRAMMING_SQL
    assert "total_volume_m3" in handler.CREATE_PROGRAMMING_SQL
    assert "master.box_volume" in handler.INSERT_PROGRAMMING_SQL
