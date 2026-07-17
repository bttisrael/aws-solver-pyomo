"""Extend optimization result tables for multi-vehicle cost optimization."""

from upload_sheets_to_dsql import aws_region, connect_dsql, load_dotenv, resolve_dsql_endpoint


RUN_COLUMNS = (
    "ADD COLUMN IF NOT EXISTS total_freight_cost NUMERIC(18, 2)",
    "ADD COLUMN IF NOT EXISTS vehicle_count_weight NUMERIC(12, 6)",
    "ADD COLUMN IF NOT EXISTS freight_cost_weight NUMERIC(12, 6)",
)
VEHICLE_COLUMNS = (
    "ADD COLUMN IF NOT EXISTS vehicle_type TEXT",
    "ADD COLUMN IF NOT EXISTS load_volume_m3 NUMERIC(16, 6)",
    "ADD COLUMN IF NOT EXISTS volume_utilization NUMERIC(10, 6)",
    "ADD COLUMN IF NOT EXISTS route_distance_km NUMERIC(10, 2)",
    "ADD COLUMN IF NOT EXISTS freight_cost NUMERIC(16, 2)",
)


def main() -> None:
    load_dotenv()
    region = aws_region()
    endpoint = resolve_dsql_endpoint(region)
    with connect_dsql(endpoint, region) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            for definition in RUN_COLUMNS:
                cur.execute(f"ALTER TABLE logistics.optimization_runs {definition}")
            for definition in VEHICLE_COLUMNS:
                cur.execute(
                    f"ALTER TABLE logistics.optimization_vehicle_assignments {definition}"
                )
            cur.execute(
                """
                UPDATE logistics.optimization_runs
                SET total_freight_cost = COALESCE(total_freight_cost, 0),
                    vehicle_count_weight = COALESCE(vehicle_count_weight, 1),
                    freight_cost_weight = COALESCE(freight_cost_weight, 0.001)
                WHERE total_freight_cost IS NULL
                   OR vehicle_count_weight IS NULL
                   OR freight_cost_weight IS NULL
                """
            )
            cur.execute(
                """
                UPDATE logistics.optimization_vehicle_assignments AS assignment
                SET vehicle_type = COALESCE(vehicle_type, 'Legacy configured vehicle'),
                    load_volume_m3 = COALESCE(load_volume_m3, 0),
                    volume_utilization = COALESCE(volume_utilization, 0),
                    route_distance_km = COALESCE(
                        route_distance_km, route.google_driving_distance_km
                    ),
                    freight_cost = COALESCE(freight_cost, 0)
                FROM logistics.route AS route
                WHERE route.origin = assignment.origin
                  AND route.destiny = assignment.destiny
                  AND (
                      assignment.vehicle_type IS NULL
                      OR assignment.load_volume_m3 IS NULL
                      OR assignment.volume_utilization IS NULL
                      OR assignment.route_distance_km IS NULL
                      OR assignment.freight_cost IS NULL
                  )
                """
            )
            cur.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM logistics.optimization_runs),
                    (SELECT COUNT(*) FROM logistics.optimization_vehicle_assignments),
                    (SELECT COUNT(*) FROM logistics.optimization_vehicle_assignments
                     WHERE vehicle_type IS NULL OR route_distance_km IS NULL)
                """
            )
            runs, vehicles, missing = cur.fetchone()
    if missing:
        raise RuntimeError(f"{missing} legacy vehicle result rows remain incomplete")
    print(f"multi-vehicle result schema ready: runs={runs}, vehicles={vehicles}")


if __name__ == "__main__":
    main()
