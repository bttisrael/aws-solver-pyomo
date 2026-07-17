from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from or_aws_fleet.dsql_optimizer import DatabaseSettings, load_programming, persist_solution
from or_aws_fleet.programming_model import ProgrammingLine, solve_route


app = FastAPI(title="Beverage Fleet Optimizer", version="1.0.0")


class SolveRequest(BaseModel):
    programming_date: date | None = None
    max_weight_kg: float = Field(default=25_000, gt=0)
    max_pallets: float = Field(default=60, gt=0)
    time_limit_seconds: int = Field(default=60, ge=1, le=300)
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

        routes: dict[tuple[str, str], list[ProgrammingLine]] = defaultdict(list)
        for line in lines:
            routes[(line.origin, line.destiny)].append(line)

        solutions = [
            solve_route(
                route_lines,
                max_weight_kg=request.max_weight_kg,
                max_pallets=request.max_pallets,
                time_limit_seconds=request.time_limit_seconds,
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
                request.max_weight_kg,
                request.max_pallets,
            )
            if request.persist
            else None
        )
    finally:
        conn.close()

    statuses = {solution.status for solution in solutions}
    status = "OPTIMAL" if statuses == {"OPTIMAL"} else "FEASIBLE" if "HEURISTIC" not in statuses else "HEURISTIC"
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
    )
