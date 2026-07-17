from __future__ import annotations

from dataclasses import dataclass
import re

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
    total_volume_m3: float = 0
    google_driving_distance_km: float = 0
    units: int = 0
    qty_by_box: int = 0


@dataclass(frozen=True)
class VehicleType:
    vehicle_type: str
    vehicle_capacity_m3: float
    vehicle_capacity_kg: float
    freight_cost_per_km: float
    vehicle_capacity_pallets: float


@dataclass(frozen=True)
class VehicleLoad:
    vehicle_id: str
    vehicle_type: str
    origin: str
    destiny: str
    load_weight_kg: float
    load_pallets: float
    load_volume_m3: float
    load_boxes: float
    weight_utilization: float
    pallet_utilization: float
    volume_utilization: float
    route_distance_km: float
    freight_cost: float
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


def _legacy_vehicle(max_weight_kg: float, max_pallets: float) -> VehicleType:
    return VehicleType("Configured vehicle", 1_000_000_000, max_weight_kg, 0, max_pallets)


def _fits(line: ProgrammingLine, vehicle: VehicleType) -> bool:
    return (
        line.total_weight_kg <= vehicle.vehicle_capacity_kg + 1e-7
        and line.total_pallets <= vehicle.vehicle_capacity_pallets + 1e-7
        and line.total_volume_m3 <= vehicle.vehicle_capacity_m3 + 1e-7
    )


def _greedy_multi_vehicle(
    lines: list[ProgrammingLine],
    vehicle_types: list[VehicleType],
    distance_km: float,
    vehicle_count_weight: float,
    freight_cost_weight: float,
) -> list[tuple[VehicleType, list[int]]]:
    bins: list[tuple[VehicleType, list[int]]] = []
    weights: list[float] = []
    pallets: list[float] = []
    volumes: list[float] = []
    largest_weight = max(vehicle.vehicle_capacity_kg for vehicle in vehicle_types)
    largest_pallets = max(vehicle.vehicle_capacity_pallets for vehicle in vehicle_types)
    largest_volume = max(vehicle.vehicle_capacity_m3 for vehicle in vehicle_types)
    order = sorted(
        range(len(lines)),
        key=lambda idx: max(
            lines[idx].total_weight_kg / largest_weight,
            lines[idx].total_pallets / largest_pallets,
            lines[idx].total_volume_m3 / largest_volume,
        ),
        reverse=True,
    )
    for idx in order:
        line = lines[idx]
        feasible_bins = [
            number
            for number, (vehicle, _) in enumerate(bins)
            if weights[number] + line.total_weight_kg <= vehicle.vehicle_capacity_kg + 1e-7
            and pallets[number] + line.total_pallets <= vehicle.vehicle_capacity_pallets + 1e-7
            and volumes[number] + line.total_volume_m3 <= vehicle.vehicle_capacity_m3 + 1e-7
        ]
        if feasible_bins:
            number = min(
                feasible_bins,
                key=lambda item: max(
                    (weights[item] + line.total_weight_kg) / bins[item][0].vehicle_capacity_kg,
                    (pallets[item] + line.total_pallets)
                    / bins[item][0].vehicle_capacity_pallets,
                    (volumes[item] + line.total_volume_m3)
                    / bins[item][0].vehicle_capacity_m3,
                ),
            )
            bins[number][1].append(idx)
            weights[number] += line.total_weight_kg
            pallets[number] += line.total_pallets
            volumes[number] += line.total_volume_m3
            continue
        feasible_types = [vehicle for vehicle in vehicle_types if _fits(line, vehicle)]
        if not feasible_types:
            raise ValueError(f"Demand line exceeds every configured vehicle capacity: {line.demand_id}")
        vehicle = min(
            feasible_types,
            key=lambda item: vehicle_count_weight
            + freight_cost_weight * item.freight_cost_per_km * distance_km,
        )
        bins.append((vehicle, [idx]))
        weights.append(line.total_weight_kg)
        pallets.append(line.total_pallets)
        volumes.append(line.total_volume_m3)
    return bins


def _vehicle_loads(
    lines: list[ProgrammingLine],
    bins: list[tuple[VehicleType, list[int]]],
    distance_km: float,
) -> tuple[VehicleLoad, ...]:
    loads = []
    for number, (vehicle, indexes) in enumerate(bins, start=1):
        weight = sum(lines[idx].total_weight_kg for idx in indexes)
        pallets = sum(lines[idx].total_pallets for idx in indexes)
        volume = sum(lines[idx].total_volume_m3 for idx in indexes)
        boxes = sum(lines[idx].total_boxes for idx in indexes)
        type_code = re.sub(r"[^A-Z0-9]+", "_", vehicle.vehicle_type.upper()).strip("_")
        loads.append(
            VehicleLoad(
                vehicle_id=(
                    f"{lines[indexes[0]].origin}-{lines[indexes[0]].destiny}-"
                    f"{type_code}-V{number:03d}"
                ),
                vehicle_type=vehicle.vehicle_type,
                origin=lines[indexes[0]].origin,
                destiny=lines[indexes[0]].destiny,
                load_weight_kg=round(weight, 3),
                load_pallets=round(pallets, 6),
                load_volume_m3=round(volume, 6),
                load_boxes=round(boxes, 6),
                weight_utilization=round(weight / vehicle.vehicle_capacity_kg, 6),
                pallet_utilization=round(pallets / vehicle.vehicle_capacity_pallets, 6),
                volume_utilization=round(volume / vehicle.vehicle_capacity_m3, 6),
                route_distance_km=round(distance_km, 2),
                freight_cost=round(vehicle.freight_cost_per_km * distance_km, 2),
                demand_ids=tuple(lines[idx].demand_id for idx in indexes),
            )
        )
    return tuple(loads)


def solve_route(
    lines: list[ProgrammingLine],
    max_weight_kg: float = 25_000,
    max_pallets: float = 60,
    time_limit_seconds: int = 60,
    *,
    vehicle_types: list[VehicleType] | None = None,
    distance_km: float | None = None,
    vehicle_count_weight: float = 1.0,
    freight_cost_weight: float = 0.001,
) -> RouteSolution:
    if not lines:
        raise ValueError("A route must contain at least one programming line.")
    if len({line.origin for line in lines}) != 1 or len({line.destiny for line in lines}) != 1:
        raise ValueError("solve_route requires exactly one origin-destiny route.")
    types = vehicle_types or [_legacy_vehicle(max_weight_kg, max_pallets)]
    if not types:
        raise ValueError("At least one vehicle type must be enabled.")
    if any(
        value <= 0
        for vehicle in types
        for value in (
            vehicle.vehicle_capacity_m3,
            vehicle.vehicle_capacity_kg,
            vehicle.vehicle_capacity_pallets,
        )
    ):
        raise ValueError("Vehicle capacities must be positive.")
    route_distance = (
        float(distance_km)
        if distance_km is not None
        else max(line.google_driving_distance_km for line in lines)
    )
    greedy_bins = _greedy_multi_vehicle(
        lines, types, route_distance, vehicle_count_weight, freight_cost_weight
    )
    solver_name, solver = available_solver()
    if solver is None:
        return RouteSolution(
            "HEURISTIC",
            "multi_vehicle_first_fit",
            lines[0].origin,
            lines[0].destiny,
            _vehicle_loads(lines, greedy_bins, route_distance),
        )

    items = list(range(len(lines)))
    slots = [(vehicle_index, slot) for vehicle_index in range(len(types)) for slot in items]
    model = pyo.ConcreteModel("multi_vehicle_route_optimization")
    model.I = pyo.Set(initialize=items)
    model.S = pyo.Set(initialize=slots, dimen=2)
    model.x = pyo.Var(model.I, model.S, domain=pyo.Binary)
    model.y = pyo.Var(model.S, domain=pyo.Binary)
    model.assign_once = pyo.Constraint(
        model.I,
        rule=lambda m, item: sum(m.x[item, vehicle_index, slot] for vehicle_index, slot in m.S)
        == 1,
    )
    model.weight_capacity = pyo.Constraint(
        model.S,
        rule=lambda m, vehicle_index, slot: sum(
            lines[item].total_weight_kg * m.x[item, vehicle_index, slot] for item in m.I
        )
        <= types[vehicle_index].vehicle_capacity_kg * m.y[vehicle_index, slot],
    )
    model.pallet_capacity = pyo.Constraint(
        model.S,
        rule=lambda m, vehicle_index, slot: sum(
            lines[item].total_pallets * m.x[item, vehicle_index, slot] for item in m.I
        )
        <= types[vehicle_index].vehicle_capacity_pallets * m.y[vehicle_index, slot],
    )
    model.volume_capacity = pyo.Constraint(
        model.S,
        rule=lambda m, vehicle_index, slot: sum(
            lines[item].total_volume_m3 * m.x[item, vehicle_index, slot] for item in m.I
        )
        <= types[vehicle_index].vehicle_capacity_m3 * m.y[vehicle_index, slot],
    )
    model.activation = pyo.Constraint(
        model.I,
        model.S,
        rule=lambda m, item, vehicle_index, slot: m.x[item, vehicle_index, slot]
        <= m.y[vehicle_index, slot],
    )
    model.symmetry = pyo.Constraint(
        [(vehicle_index, slot) for vehicle_index in range(len(types)) for slot in items[:-1]],
        rule=lambda m, vehicle_index, slot: m.y[vehicle_index, slot]
        >= m.y[vehicle_index, slot + 1],
    )
    model.objective = pyo.Objective(
        expr=sum(
            (
                vehicle_count_weight
                + freight_cost_weight
                * types[vehicle_index].freight_cost_per_km
                * route_distance
            )
            * model.y[vehicle_index, slot]
            for vehicle_index, slot in slots
        ),
        sense=pyo.minimize,
    )

    for vehicle_index, slot in slots:
        model.y[vehicle_index, slot].value = 0
    type_slots_used = {index: 0 for index in range(len(types))}
    for vehicle, indexes in greedy_bins:
        vehicle_index = types.index(vehicle)
        slot = type_slots_used[vehicle_index]
        type_slots_used[vehicle_index] += 1
        model.y[vehicle_index, slot].value = 1
        for item in indexes:
            model.x[item, vehicle_index, slot].value = 1

    if solver_name == "appsi_highs":
        solver.config.time_limit = time_limit_seconds
    elif hasattr(solver, "options"):
        solver.options["time_limit"] = time_limit_seconds
    result = solver.solve(model, tee=False)
    termination = str(result.solver.termination_condition).lower()
    if "optimal" not in termination and "feasible" not in termination:
        bins = greedy_bins
        status = "HEURISTIC"
        used_solver = "multi_vehicle_first_fit"
    else:
        bins = []
        for vehicle_index, slot in slots:
            if pyo.value(model.y[vehicle_index, slot]) < 0.5:
                continue
            indexes = [
                item
                for item in items
                if pyo.value(model.x[item, vehicle_index, slot]) >= 0.5
            ]
            if indexes:
                bins.append((types[vehicle_index], indexes))
        status = "OPTIMAL" if "optimal" in termination else "FEASIBLE"
        used_solver = str(solver_name)
    return RouteSolution(
        status,
        used_solver,
        lines[0].origin,
        lines[0].destiny,
        _vehicle_loads(lines, bins, route_distance),
    )
