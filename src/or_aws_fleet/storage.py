from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

import boto3

from or_aws_fleet.config import PipelineConfig
from or_aws_fleet.demand import DemandPoint
from or_aws_fleet.model import FleetSolution


def _write_demand_csv(path: Path, demand_points: list[DemandPoint]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(asdict(demand_points[0]).keys()))
        writer.writeheader()
        for point in demand_points:
            writer.writerow(asdict(point))


def _write_assignments_csv(path: Path, solution: FleetSolution) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "vehicle_id",
                "load_units",
                "capacity_units",
                "utilization",
                "route_distance_proxy_km",
                "customer_count",
                "customers",
            ],
        )
        writer.writeheader()
        for assignment in solution.assignments:
            writer.writerow(
                {
                    "vehicle_id": assignment.vehicle_id,
                    "load_units": assignment.load_units,
                    "capacity_units": assignment.capacity_units,
                    "utilization": assignment.utilization,
                    "route_distance_proxy_km": assignment.route_distance_proxy_km,
                    "customer_count": len(assignment.customers),
                    "customers": "|".join(assignment.customers),
                }
            )


def _write_summary_json(path: Path, config: PipelineConfig, solution: FleetSolution) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "run_date": config.run_date.isoformat(),
        "status": solution.status,
        "solver_name": solution.solver_name,
        "demand_points": config.demand_points,
        "vehicle_capacity": config.vehicle_capacity,
        "vehicle_count": solution.vehicle_count,
        "total_demand_units": solution.total_demand_units,
        "total_capacity_units": solution.total_capacity_units,
        "average_utilization": solution.average_utilization,
    }
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def _upload_file(bucket_name: str, local_path: Path, key: str) -> None:
    client = boto3.client("s3")
    client.upload_file(str(local_path), bucket_name, key)


def write_run_outputs(
    config: PipelineConfig,
    demand_points: list[DemandPoint],
    solution: FleetSolution,
) -> dict:
    run_date = config.run_date.isoformat()
    run_dir = config.output_dir / run_date

    demand_path = run_dir / "demand.csv"
    assignments_path = run_dir / "vehicle_assignments.csv"
    summary_path = run_dir / "summary.json"

    _write_demand_csv(demand_path, demand_points)
    _write_assignments_csv(assignments_path, solution)
    summary = _write_summary_json(summary_path, config, solution)

    if config.s3_bucket:
        prefix = config.s3_prefix.strip("/")
        _upload_file(
            config.s3_bucket,
            demand_path,
            f"{prefix}/demand/dt={run_date}/demand.csv",
        )
        _upload_file(
            config.s3_bucket,
            assignments_path,
            f"{prefix}/solutions/dt={run_date}/vehicle_assignments.csv",
        )
        _upload_file(
            config.s3_bucket,
            summary_path,
            f"{prefix}/summaries/dt={run_date}/summary.json",
        )

    summary["output_dir"] = str(run_dir)
    summary["s3_bucket"] = config.s3_bucket
    return summary
