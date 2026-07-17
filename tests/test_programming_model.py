from or_aws_fleet.programming_model import ProgrammingLine, solve_route


def line(number: int, weight: float, pallets: float) -> ProgrammingLine:
    return ProgrammingLine(
        demand_id=f"D{number}",
        origin="FACTORY_A",
        destiny="DC_A",
        cod_material=f"SKU{number}",
        total_weight_kg=weight,
        total_pallets=pallets,
        total_boxes=pallets * 50,
    )


def test_solver_minimizes_vehicles_with_weight_and_pallet_limits() -> None:
    solution = solve_route(
        [line(1, 15_000, 20), line(2, 10_000, 25), line(3, 9_000, 35)],
        max_weight_kg=25_000,
        max_pallets=60,
        time_limit_seconds=10,
    )
    assert len(solution.vehicles) == 2
    assert sorted(demand_id for vehicle in solution.vehicles for demand_id in vehicle.demand_ids) == ["D1", "D2", "D3"]
    assert all(vehicle.load_weight_kg <= 25_000 for vehicle in solution.vehicles)
    assert all(vehicle.load_pallets <= 60 for vehicle in solution.vehicles)


def test_solver_rejects_line_larger_than_vehicle() -> None:
    try:
        solve_route([line(1, 26_000, 10)], max_weight_kg=25_000, max_pallets=60)
    except ValueError as exc:
        assert "exceed" in str(exc)
    else:
        raise AssertionError("Expected oversized demand to be rejected")
