from or_aws_fleet.programming_model import ProgrammingLine, VehicleType, solve_route


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


def test_solver_selects_vehicle_type_using_count_capacity_and_route_cost() -> None:
    vehicles = [
        VehicleType("Light van", 3.2, 650, 2.8, 2),
        VehicleType("Cargo van", 8, 1_200, 4.2, 5),
        VehicleType("Box truck", 45, 10_000, 12, 30),
    ]
    lines = [line(1, 600, 2), line(2, 600, 2)]
    solution = solve_route(
        lines,
        vehicle_types=vehicles,
        distance_km=100,
        vehicle_count_weight=1,
        freight_cost_weight=0.001,
        time_limit_seconds=10,
    )
    assert len(solution.vehicles) == 1
    assert solution.vehicles[0].vehicle_type == "Cargo van"
    assert solution.vehicles[0].freight_cost == 420


def test_solver_enforces_cubic_volume_capacity() -> None:
    vehicles = [
        VehicleType("Cargo van", 8, 1_200, 4.2, 5),
        VehicleType("Box truck", 45, 10_000, 12, 30),
    ]
    oversized_volume = line(1, 700, 2)
    oversized_volume = ProgrammingLine(
        **{**oversized_volume.__dict__, "total_volume_m3": 9}
    )
    solution = solve_route(
        [oversized_volume], vehicle_types=vehicles, distance_km=50, time_limit_seconds=10
    )
    assert solution.vehicles[0].vehicle_type == "Box truck"
