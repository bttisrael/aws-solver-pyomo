from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from or_aws_fleet.dsql_optimizer import (
    DatabaseSettings,
    load_programming,
    load_vehicle_types,
    persist_solution,
)
from or_aws_fleet.programming_model import ProgrammingLine, VehicleType, solve_route


app = FastAPI(title="Beverage Fleet Optimizer", version="1.0.0")


class VehicleTypeParameter(BaseModel):
    vehicle_type: str = Field(min_length=1)
    vehicle_capacity_m3: float = Field(gt=0)
    vehicle_capacity_kg: float = Field(gt=0)
    freight_cost_per_km: float = Field(ge=0)
    vehicle_capacity_pallets: float = Field(gt=0)
    enabled: bool = True


class SolveRequest(BaseModel):
    programming_date: date | None = None
    max_weight_kg: float = Field(default=25_000, gt=0)
    max_pallets: float = Field(default=60, gt=0)
    time_limit_seconds: int = Field(default=60, ge=1, le=300)
    vehicle_types: list[VehicleTypeParameter] | None = None
    vehicle_count_weight: float = Field(default=1.0, gt=0)
    freight_cost_weight: float = Field(default=0.001, ge=0)
    persist: bool = True


class SolveResponse(BaseModel):
    run_id: str | None
    programming_date: date
    status: str
    solver_names: list[str]
    routes: int
    vehicles: int
    demand_lines: int
    total_weight_kg: float
    total_pallets: float
    average_weight_utilization: float
    average_pallet_utilization: float
    average_volume_utilization: float
    total_freight_cost: float
    vehicle_mix: dict[str, int]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.post("/solve", response_model=SolveResponse)
def solve(request: SolveRequest) -> SolveResponse:
    programming_date = request.programming_date or datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    settings = DatabaseSettings.from_env()
    conn = settings.connect()
    try:
        lines = load_programming(conn, programming_date)
        if not lines:
            raise HTTPException(status_code=404, detail=f"No daily_programming rows for {programming_date}")

        configured = request.vehicle_types
        vehicle_types = (
            [
                VehicleType(
                    vehicle_type=item.vehicle_type,
                    vehicle_capacity_m3=item.vehicle_capacity_m3,
                    vehicle_capacity_kg=item.vehicle_capacity_kg,
                    freight_cost_per_km=item.freight_cost_per_km,
                    vehicle_capacity_pallets=item.vehicle_capacity_pallets,
                )
                for item in configured
                if item.enabled
            ]
            if configured is not None
            else load_vehicle_types(conn)
        )
        if not vehicle_types:
            raise HTTPException(status_code=400, detail="Enable at least one vehicle type.")

        routes: dict[tuple[str, str], list[ProgrammingLine]] = defaultdict(list)
        for line in lines:
            routes[(line.origin, line.destiny)].append(line)

        solutions = [
            solve_route(
                route_lines,
                max_weight_kg=request.max_weight_kg,
                max_pallets=request.max_pallets,
                time_limit_seconds=request.time_limit_seconds,
                vehicle_types=vehicle_types,
                distance_km=route_lines[0].google_driving_distance_km,
                vehicle_count_weight=request.vehicle_count_weight,
                freight_cost_weight=request.freight_cost_weight,
            )
            for route_lines in routes.values()
        ]
        vehicles = [vehicle for solution in solutions for vehicle in solution.vehicles]
        run_id = (
            persist_solution(
                conn,
                programming_date,
                lines,
                solutions,
                max(vehicle.vehicle_capacity_kg for vehicle in vehicle_types),
                max(vehicle.vehicle_capacity_pallets for vehicle in vehicle_types),
                request.vehicle_count_weight,
                request.freight_cost_weight,
            )
            if request.persist
            else None
        )
    finally:
        conn.close()

    statuses = {solution.status for solution in solutions}
    status = "OPTIMAL" if statuses == {"OPTIMAL"} else "FEASIBLE" if "HEURISTIC" not in statuses else "HEURISTIC"
    vehicle_mix = {
        vehicle_type: sum(vehicle.vehicle_type == vehicle_type for vehicle in vehicles)
        for vehicle_type in sorted({vehicle.vehicle_type for vehicle in vehicles})
    }
    return SolveResponse(
        run_id=run_id,
        programming_date=programming_date,
        status=status,
        solver_names=sorted({solution.solver_name for solution in solutions}),
        routes=len(solutions),
        vehicles=len(vehicles),
        demand_lines=len(lines),
        total_weight_kg=round(sum(line.total_weight_kg for line in lines), 3),
        total_pallets=round(sum(line.total_pallets for line in lines), 6),
        average_weight_utilization=round(
            sum(vehicle.weight_utilization for vehicle in vehicles) / len(vehicles), 6
        ),
        average_pallet_utilization=round(
            sum(vehicle.pallet_utilization for vehicle in vehicles) / len(vehicles), 6
        ),
        average_volume_utilization=round(
            sum(vehicle.volume_utilization for vehicle in vehicles) / len(vehicles), 6
        ),
        total_freight_cost=round(sum(vehicle.freight_cost for vehicle in vehicles), 2),
        vehicle_mix=vehicle_mix,
    )
