from datetime import date

from or_aws_fleet.demand import generate_daily_demand


def test_daily_demand_is_deterministic_for_date():
    run_date = date(2026, 7, 4)

    first = generate_daily_demand(run_date, demand_points=10)
    second = generate_daily_demand(run_date, demand_points=10)

    assert first == second
    assert len(first) == 10
    assert all(point.demand_units > 0 for point in first)
