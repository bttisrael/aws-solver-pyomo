from __future__ import annotations

import math
from dataclasses import dataclass

import pyomo.environ as pyo


@dataclass(frozen=True)
class ProgrammingLine:
    demand_id: str
    origin: str
    destiny: str
    cod_material: str
    total_weight_kg: float
    total_pallets: float
    total_boxes: float
    units: int = 0
    qty_by_box: int = 0


@dataclass(frozen=True)
class VehicleLoad:
    vehicle_id: str
    origin: str
    destiny: str
    load_weight_kg: float
    load_pallets: float
    load_boxes: float
    weight_utilization: float
    pallet_utilization: float
    demand_ids: tuple[str, ...]


@dataclass(frozen=True)
class RouteSolution:
    status: str
    solver_name: str
    origin: str
    destiny: str
    vehicles: tuple[VehicleLoad, ...]


def available_solver():
    for name in ("appsi_highs", "highs", "cbc", "glpk"):
        solver = pyo.SolverFactory(name)
        try:
            if solver.available(exception_flag=False):
                return name, solver
        except Exception:
            continue
    return None, None


def first_fit_decreasing(
    lines: list[ProgrammingLine],
    max_weight_kg: float,
    max_pallets: float,
) -> list[list[int]]:
    bins: list[list[int]] = []
    weights: list[float] = []
    pallets: list[float] = []
    order = sorted(
        range(len(lines)),
        key=lambda idx: max(
            lines[idx].total_weight_kg / max_weight_kg,
            lines[idx].total_pallets / max_pallets,
        ),
        reverse=True,
    )
    for idx in order:
        line = lines[idx]
        for vehicle in range(len(bins)):
            if (
                weights[vehicle] + line.total_weight_kg <= max_weight_kg + 1e-7
                and pallets[vehicle] + line.total_pallets <= max_pallets + 1e-7
            ):
                bins[vehicle].append(idx)
                weights[vehicle] += line.total_weight_kg
                pallets[vehicle] += line.total_pallets
                break
        else:
            bins.append([idx])
            weights.append(line.total_weight_kg)
            pallets.append(line.total_pallets)
    return bins


def _vehicle_loads(
    lines: list[ProgrammingLine],
    bins: list[list[int]],
    max_weight_kg: float,
    max_pallets: float,
) -> tuple[VehicleLoad, ...]:
    loads: list[VehicleLoad] = []
    for number, indexes in enumerate(bins, start=1):
        weight = sum(lines[idx].total_weight_kg for idx in indexes)
        pallets = sum(lines[idx].total_pallets for idx in indexes)
        boxes = sum(lines[idx].total_boxes for idx in indexes)
        loads.append(
            VehicleLoad(
                vehicle_id=f"{lines[indexes[0]].origin}-{lines[indexes[0]].destiny}-V{number:03d}",
                origin=lines[indexes[0]].origin,
                destiny=lines[indexes[0]].destiny,
                load_weight_kg=round(weight, 3),
                load_pallets=round(pallets, 6),
                load_boxes=round(boxes, 6),
                weight_utilization=round(weight / max_weight_kg, 6),
                pallet_utilization=round(pallets / max_pallets, 6),
                demand_ids=tuple(lines[idx].demand_id for idx in indexes),
            )
        )
    return tuple(loads)


def solve_route(
    lines: list[ProgrammingLine],
    max_weight_kg: float = 25_000,
    max_pallets: float = 60,
    time_limit_seconds: int = 60,
) -> RouteSolution:
    if not lines:
        raise ValueError("A route must contain at least one programming line.")
    origins = {line.origin for line in lines}
    destinies = {line.destiny for line in lines}
    if len(origins) != 1 or len(destinies) != 1:
        raise ValueError("solve_route requires exactly one origin-destiny route.")
    oversized = [
        line.demand_id
        for line in lines
        if line.total_weight_kg > max_weight_kg + 1e-7 or line.total_pallets > max_pallets + 1e-7
    ]
    if oversized:
        raise ValueError(f"Demand lines exceed a single vehicle capacity: {oversized[:5]}")

    greedy_bins = first_fit_decreasing(lines, max_weight_kg, max_pallets)
    solver_name, solver = available_solver()
    if solver is None:
        return RouteSolution(
            status="HEURISTIC",
            solver_name="first_fit_decreasing",
            origin=lines[0].origin,
            destiny=lines[0].destiny,
            vehicles=_vehicle_loads(lines, greedy_bins, max_weight_kg, max_pallets),
        )

    items = list(range(len(lines)))
    vehicles = list(range(len(greedy_bins)))
    model = pyo.ConcreteModel("route_vehicle_minimization")
    model.I = pyo.Set(initialize=items)
    model.V = pyo.Set(initialize=vehicles)
    model.x = pyo.Var(model.I, model.V, domain=pyo.Binary)
    model.y = pyo.Var(model.V, domain=pyo.Binary)

    model.assign_once = pyo.Constraint(
        model.I,
        rule=lambda m, item: sum(m.x[item, vehicle] for vehicle in m.V) == 1,
    )
    model.weight_capacity = pyo.Constraint(
        model.V,
        rule=lambda m, vehicle: sum(
            lines[item].total_weight_kg * m.x[item, vehicle] for item in m.I
        ) <= max_weight_kg * m.y[vehicle],
    )
    model.pallet_capacity = pyo.Constraint(
        model.V,
        rule=lambda m, vehicle: sum(
            lines[item].total_pallets * m.x[item, vehicle] for item in m.I
        ) <= max_pallets * m.y[vehicle],
    )
    model.activation = pyo.Constraint(
        model.I,
        model.V,
        rule=lambda m, item, vehicle: m.x[item, vehicle] <= m.y[vehicle],
    )

    def symmetry_rule(m, vehicle):
        if vehicle == vehicles[-1]:
            return pyo.Constraint.Skip
        return m.y[vehicle] >= m.y[vehicle + 1]

    model.symmetry = pyo.Constraint(model.V, rule=symmetry_rule)
    model.objective = pyo.Objective(expr=sum(model.y[vehicle] for vehicle in model.V), sense=pyo.minimize)

    for vehicle, indexes in enumerate(greedy_bins):
        model.y[vehicle].value = 1
        for item in indexes:
            model.x[item, vehicle].value = 1

    if solver_name == "appsi_highs":
        solver.config.time_limit = time_limit_seconds
    elif hasattr(solver, "options"):
        solver.options["time_limit"] = time_limit_seconds

    result = solver.solve(model, tee=False)
    termination = str(result.solver.termination_condition).lower()
    if "optimal" not in termination and "feasible" not in termination:
        bins = greedy_bins
        status = "HEURISTIC"
        used_solver = "first_fit_decreasing"
    else:
        bins = [
            [item for item in items if pyo.value(model.x[item, vehicle]) >= 0.5]
            for vehicle in vehicles
            if pyo.value(model.y[vehicle]) >= 0.5
        ]
        bins = [indexes for indexes in bins if indexes]
        status = "OPTIMAL" if "optimal" in termination else "FEASIBLE"
        used_solver = str(solver_name)

    lower_bound = max(
        math.ceil(sum(line.total_weight_kg for line in lines) / max_weight_kg),
        math.ceil(sum(line.total_pallets for line in lines) / max_pallets),
    )
    if len(bins) == lower_bound:
        status = "OPTIMAL"
    return RouteSolution(
        status=status,
        solver_name=used_solver,
        origin=lines[0].origin,
        destiny=lines[0].destiny,
        vehicles=_vehicle_loads(lines, bins, max_weight_kg, max_pallets),
    )
