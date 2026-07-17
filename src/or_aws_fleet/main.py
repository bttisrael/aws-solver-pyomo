from __future__ import annotations

import argparse
import json
import os

from or_aws_fleet.config import PipelineConfig
from or_aws_fleet.demand import generate_daily_demand
from or_aws_fleet.model import solve_fleet_sizing
from or_aws_fleet.storage import write_run_outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run daily OR fleet-sizing pipeline.")
    parser.add_argument("--run-date", help="Run date in YYYY-MM-DD format.")
    parser.add_argument("--demand-points", type=int, help="Number of customer demand points.")
    parser.add_argument("--vehicle-capacity", type=int, help="Capacity units per vehicle.")
    parser.add_argument("--max-vehicles", type=int, help="Upper bound of candidate vehicles.")
    return parser.parse_args()


def apply_cli_overrides(args: argparse.Namespace) -> None:
    if args.run_date:
        os.environ["RUN_DATE"] = args.run_date
    if args.demand_points is not None:
        os.environ["DEMAND_POINTS"] = str(args.demand_points)
    if args.vehicle_capacity is not None:
        os.environ["VEHICLE_CAPACITY"] = str(args.vehicle_capacity)
    if args.max_vehicles is not None:
        os.environ["MAX_VEHICLES"] = str(args.max_vehicles)


def run_pipeline(config: PipelineConfig | None = None) -> dict:
    config = config or PipelineConfig.from_env()
    demand_points = generate_daily_demand(
        run_date=config.run_date,
        demand_points=config.demand_points,
        random_seed=config.random_seed,
    )
    solution = solve_fleet_sizing(
        demand_points=demand_points,
        vehicle_capacity=config.vehicle_capacity,
        max_vehicles=config.max_vehicles,
        distance_weight=config.distance_weight,
    )
    return write_run_outputs(config, demand_points, solution)


def main() -> None:
    args = parse_args()
    apply_cli_overrides(args)
    summary = run_pipeline()
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
