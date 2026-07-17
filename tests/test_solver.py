from or_aws_fleet.demand import DemandPoint
from or_aws_fleet.model import solve_fleet_sizing


def point(customer_id: str, demand_units: int) -> DemandPoint:
    return DemandPoint(
        customer_id=customer_id,
        city="Sao Paulo",
        state="SP",
        latitude=-23.55,
        longitude=-46.63,
        demand_units=demand_units,
        service_minutes=10,
        distance_km=5.0,
    )


def test_solver_minimizes_vehicle_count_for_simple_case():
    demand = [
        point("A", 60),
        point("B", 40),
        point("C", 50),
        point("D", 50),
    ]

    solution = solve_fleet_sizing(demand, vehicle_capacity=100, max_vehicles=4)

    assert solution.vehicle_count == 2
    assert solution.total_demand_units == 200
    assert all(assignment.load_units <= assignment.capacity_units for assignment in solution.assignments)
