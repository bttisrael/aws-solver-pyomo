from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import boto3


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from or_aws_fleet.dsql_optimizer import DatabaseSettings  # noqa: E402


def load_dotenv() -> None:
    for raw_line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def physical_id(resources: list[dict], resource_type: str) -> str:
    return next(item["PhysicalResourceId"] for item in resources if item["ResourceType"] == resource_type)


def main() -> None:
    load_dotenv()
    region = os.getenv("AWS_DEFAULT_REGION") or "us-east-2"
    cloudformation = boto3.client("cloudformation", region_name=region)
    resources = cloudformation.list_stack_resources(StackName="OrFleetOptimizationStack")["StackResourceSummaries"]
    cluster = physical_id(resources, "AWS::ECS::Cluster")
    service_name = physical_id(resources, "AWS::ECS::Service")
    target_group = physical_id(resources, "AWS::ElasticLoadBalancingV2::TargetGroup")

    ecs = boto3.client("ecs", region_name=region)
    service = ecs.describe_services(cluster=cluster, services=[service_name])["services"][0]
    targets = boto3.client("elbv2", region_name=region).describe_target_health(
        TargetGroupArn=target_group
    )["TargetHealthDescriptions"]

    settings = DatabaseSettings.from_env()
    conn = settings.connect()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT run_id, programming_date, status, solver_name, vehicle_count,
                   route_count, demand_line_count, total_weight_kg, total_pallets
            FROM logistics.optimization_runs
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        run = cursor.fetchone()
        cursor.execute(
            """
            SELECT vehicle_id, position_number, load_level, load_item_label,
                   destination, material_code, boxes, total_units, weight_kg, pallet_volume
            FROM logistics.optimization_load_plan
            WHERE run_id = %s
            ORDER BY origin, destination, vehicle_id, position_number, load_level
            LIMIT 12
            """,
            (run[0],),
        )
        sample = cursor.fetchall()
        cursor.close()
    finally:
        conn.close()

    result = {
        "ecs": {
            "cluster": cluster,
            "service": service_name,
            "desired_count": service["desiredCount"],
            "running_count": service["runningCount"],
            "pending_count": service["pendingCount"],
            "target_health": [target["TargetHealth"]["State"] for target in targets],
        },
        "optimization_run": {
            "run_id": str(run[0]),
            "programming_date": str(run[1]),
            "status": str(run[2]),
            "solver": str(run[3]),
            "vehicles": int(run[4]),
            "routes": int(run[5]),
            "demand_lines": int(run[6]),
            "total_weight_kg": float(run[7]),
            "total_pallets": float(run[8]),
        },
        "load_plan_sample": [
            {
                "vehicle": str(row[0]),
                "position": int(row[1]),
                "level": str(row[2]),
                "load_item": str(row[3]),
                "destination": str(row[4]),
                "material": str(row[5]),
                "boxes": float(row[6]),
                "total_units": int(row[7]),
                "weight_kg": float(row[8]),
                "pallet_volume": float(row[9]),
            }
            for row in sample
        ],
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
