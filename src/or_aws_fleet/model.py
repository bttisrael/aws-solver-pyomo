from __future__ import annotations

import math
from dataclasses import dataclass

import pyomo.environ as pyo

from or_aws_fleet.demand import DemandPoint


@dataclass(frozen=True)
class VehicleAssignment:
    vehicle_id: str
    load_units: int
    capacity_units: int
    utilization: float
    customers: list[str]
    route_distance_proxy_km: float


@dataclass(frozen=True)
class FleetSolution:
    status: str
    solver_name: str
    vehicle_count: int
    total_demand_units: int
    total_capacity_units: int
    average_utilization: float
    assignments: list[VehicleAssignment]


def _solver_factory():
    for name in ("appsi_highs", "highs", "cbc", "glpk"):
        solver = pyo.SolverFactory(name)
        try:
            if solver.available(exception_flag=False):
                return name, solver
        except Exception:
            continue
    return None, None


def _build_solution_from_bins(
    bins: list[list[DemandPoint]],
    vehicle_capacity: int,
    status: str,
    solver_name: str,
) -> FleetSolution:
    assignments: list[VehicleAssignment] = []
    for vehicle_idx, customers in enumerate(bins, start=1):
        load_units = sum(customer.demand_units for customer in customers)
        route_distance_proxy_km = sum(customer.distance_km * 2 for customer in customers)
        assignments.append(
            VehicleAssignment(
                vehicle_id=f"V{vehicle_idx:03d}",
                load_units=load_units,
                capacity_units=vehicle_capacity,
                utilization=round(load_units / vehicle_capacity, 4),
                customers=[customer.customer_id for customer in customers],
                route_distance_proxy_km=round(route_distance_proxy_km, 2),
            )
        )

    total_demand = sum(assignment.load_units for assignment in assignments)
    total_capacity = len(assignments) * vehicle_capacity
    avg_utilization = total_demand / total_capacity if total_capacity else 0.0
    return FleetSolution(
        status=status,
        solver_name=solver_name,
        vehicle_count=len(assignments),
        total_demand_units=total_demand,
        total_capacity_units=total_capacity,
        average_utilization=round(avg_utilization, 4),
        assignments=assignments,
    )


def greedy_first_fit_decreasing(
    demand_points: list[DemandPoint],
    vehicle_capacity: int,
) -> FleetSolution:
    bins: list[list[DemandPoint]] = []
    loads: list[int] = []

    for point in sorted(demand_points, key=lambda row: row.demand_units, reverse=True):
        placed = False
        for idx, load in enumerate(loads):
            if load + point.demand_units <= vehicle_capacity:
                bins[idx].append(point)
                loads[idx] += point.demand_units
                placed = True
                break
        if not placed:
            bins.append([point])
            loads.append(point.demand_units)

    return _build_solution_from_bins(
        bins=bins,
        vehicle_capacity=vehicle_capacity,
        status="HEURISTIC",
        solver_name="first_fit_decreasing",
    )


def solve_fleet_sizing(
    demand_points: list[DemandPoint],
    vehicle_capacity: int,
    max_vehicles: int,
    distance_weight: float = 0.0001,
) -> FleetSolution:
    if not demand_points:
        return FleetSolution(
            status="EMPTY",
            solver_name="none",
            vehicle_count=0,
            total_demand_units=0,
            total_capacity_units=0,
            average_utilization=0.0,
            assignments=[],
        )

    total_demand = sum(point.demand_units for point in demand_points)
    min_required = math.ceil(total_demand / vehicle_capacity)
    max_vehicles = max(min_required, max_vehicles)

    solver_name, solver = _solver_factory()
    if solver is None:
        return greedy_first_fit_decreasing(demand_points, vehicle_capacity)

    customers = list(range(len(demand_points)))
    vehicles = list(range(max_vehicles))
    demand = {idx: demand_points[idx].demand_units for idx in customers}
    distance = {idx: demand_points[idx].distance_km for idx in customers}

    model = pyo.ConcreteModel("daily_fleet_sizing")
    model.C = pyo.Set(initialize=customers)
    model.V = pyo.Set(initialize=vehicles)
    model.x = pyo.Var(model.C, model.V, domain=pyo.Binary)
    model.y = pyo.Var(model.V, domain=pyo.Binary)

    def assign_once_rule(m, customer):
        return sum(m.x[customer, vehicle] for vehicle in m.V) == 1

    def capacity_rule(m, vehicle):
        return sum(demand[customer] * m.x[customer, vehicle] for customer in m.C) <= (
            vehicle_capacity * m.y[vehicle]
        )

    def activation_rule(m, customer, vehicle):
        return m.x[customer, vehicle] <= m.y[vehicle]

    def symmetry_rule(m, vehicle):
        if vehicle == max_vehicles - 1:
            return pyo.Constraint.Skip
        return m.y[vehicle] >= m.y[vehicle + 1]

    model.assign_once = pyo.Constraint(model.C, rule=assign_once_rule)
    model.capacity = pyo.Constraint(model.V, rule=capacity_rule)
    model.activation = pyo.Constraint(model.C, model.V, rule=activation_rule)
    model.symmetry = pyo.Constraint(model.V, rule=symmetry_rule)

    # A large coefficient keeps vehicle count as the primary objective. The tiny
    # distance term breaks ties toward shorter aggregate route proxies.
    model.objective = pyo.Objective(
        expr=(100_000 * sum(model.y[vehicle] for vehicle in model.V))
        + distance_weight
        * sum(distance[customer] * model.x[customer, vehicle] for customer in model.C for vehicle in model.V),
        sense=pyo.minimize,
    )

    result = solver.solve(model)
    termination = str(result.solver.termination_condition).lower()
    if "optimal" not in termination and "feasible" not in termination:
        return greedy_first_fit_decreasing(demand_points, vehicle_capacity)

    bins: list[list[DemandPoint]] = []
    for vehicle in vehicles:
        if pyo.value(model.y[vehicle]) < 0.5:
            continue
        assigned = [
            demand_points[customer]
            for customer in customers
            if pyo.value(model.x[customer, vehicle]) >= 0.5
        ]
        if assigned:
            bins.append(assigned)

    return _build_solution_from_bins(
        bins=bins,
        vehicle_capacity=vehicle_capacity,
        status="OPTIMAL" if "optimal" in termination else "FEASIBLE",
        solver_name=solver_name,
    )
