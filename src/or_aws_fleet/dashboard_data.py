from __future__ import annotations

from datetime import date

import pandas as pd

from or_aws_fleet.dsql_optimizer import DatabaseSettings


def _query(sql: str, parameters: tuple = ()) -> pd.DataFrame:
    conn = DatabaseSettings.from_env().connect()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, parameters)
        columns = [item[0] for item in cursor.description]
        frame = pd.DataFrame(cursor.fetchall(), columns=columns)
        cursor.close()
        conn.rollback()
        return frame
    finally:
        conn.close()


def available_programming_dates(limit: int = 120) -> list[date]:
    frame = _query(
        """
        SELECT DISTINCT date
        FROM logistics.daily_programming
        ORDER BY date DESC
        LIMIT %s
        """,
        (limit,),
    )
    return [value for value in frame["date"].tolist()]


def daily_programming(programming_date: date) -> pd.DataFrame:
    return _query(
        """
        SELECT demand_id, origin, destiny, cod_material, date, time_to_ship_days,
               units, qty_by_box, qty_by_pallet, material_weight,
               total_weight_kg, total_pallets, total_boxes, total_volume_m3,
               google_driving_distance_km
        FROM logistics.daily_programming
        WHERE date = %s
        ORDER BY origin, destiny, cod_material, demand_id
        """,
        (programming_date,),
    )


def vehicle_master_data() -> pd.DataFrame:
    return _query(
        """
        SELECT vehicle_type, vehicle_capacity_m3, vehicle_capacity_kg,
               freight_cost_per_km, vehicle_capacity_pallets
        FROM logistics.vehicle_master_data
        ORDER BY vehicle_capacity_kg, vehicle_type
        """
    )


def route_network() -> pd.DataFrame:
    return _query(
        """
        WITH latest_run AS (
            SELECT run_id
            FROM logistics.optimization_runs
            ORDER BY created_at DESC
            LIMIT 1
        ),
        route_metrics AS (
            SELECT assignment.origin, assignment.destiny,
                   COUNT(*) AS vehicle_count,
                   SUM(assignment.load_weight_kg) AS load_weight_kg,
                   SUM(assignment.load_pallets) AS load_pallets,
                   SUM(assignment.load_boxes) AS load_boxes,
                   SUM(assignment.freight_cost) AS freight_cost,
                   AVG(
                       GREATEST(
                           assignment.weight_utilization,
                           assignment.pallet_utilization,
                           assignment.volume_utilization
                       )
                   ) AS average_occupancy
            FROM logistics.optimization_vehicle_assignments AS assignment
            WHERE assignment.run_id = (SELECT run_id FROM latest_run)
            GROUP BY assignment.origin, assignment.destiny
        )
        SELECT route.origin, route.destiny,
               route.origin_latitude, route.origin_longitude,
               route.destiny_latitude, route.destiny_longitude,
               route.distance_km,
               route.google_driving_distance_km,
               COALESCE(metrics.vehicle_count, 0) AS vehicle_count,
               COALESCE(metrics.load_weight_kg, 0) AS load_weight_kg,
               COALESCE(metrics.load_pallets, 0) AS load_pallets,
               COALESCE(metrics.load_boxes, 0) AS load_boxes,
               COALESCE(metrics.freight_cost, 0) AS freight_cost,
               COALESCE(metrics.average_occupancy, 0) AS average_occupancy
        FROM logistics.route AS route
        LEFT JOIN route_metrics AS metrics
          ON metrics.origin = route.origin AND metrics.destiny = route.destiny
        ORDER BY route.origin, route.destiny
        """
    )


def optimization_runs(limit: int = 50) -> pd.DataFrame:
    return _query(
        """
        SELECT run_id, programming_date, created_at, status, solver_name,
               vehicle_count, route_count, demand_line_count, total_weight_kg,
               total_pallets, max_weight_kg, max_pallets, total_freight_cost,
               vehicle_count_weight, freight_cost_weight
        FROM logistics.optimization_runs
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (limit,),
    )


def vehicle_summary(run_id: str) -> pd.DataFrame:
    return _query(
        """
        SELECT origin, destiny, vehicle_id, vehicle_type, load_pallets, load_boxes,
               load_weight_kg, load_volume_m3, weight_utilization,
               pallet_utilization, volume_utilization, route_distance_km, freight_cost
        FROM logistics.optimization_vehicle_assignments
        WHERE run_id = %s
        ORDER BY origin, destiny, vehicle_id
        """,
        (run_id,),
    )


def operational_load_plan(run_id: str) -> pd.DataFrame:
    return _query(
        """
        SELECT origin, destination, vehicle_id, position_number, load_level,
               load_item_label, material_code, boxes, units_by_material,
               total_units, weight_kg, pallet_volume, demand_id
        FROM logistics.optimization_load_plan
        WHERE run_id = %s
        ORDER BY origin, destination, vehicle_id, position_number,
                 CASE WHEN load_level = 'BASE' THEN 0 ELSE 1 END
        """,
        (run_id,),
    )


def latest_forecast_run() -> pd.DataFrame:
    return _query(
        """SELECT runs.run_id, runs.forecast_run_date, runs.created_at,
                  runs.model_version, runs.horizon_days, runs.status,
                  runs.wape, runs.mase, runs.bias, runs.interval_coverage,
                  runs.retraining_recommended, runs.retraining_reasons,
                  registry.validation_wape, registry.baseline_wape
           FROM logistics.forecast_runs AS runs
           LEFT JOIN logistics.forecast_model_registry AS registry
             ON registry.model_version = runs.model_version
           WHERE runs.status = 'COMPLETE'
           ORDER BY runs.created_at DESC
           LIMIT 1"""
    )


def forecast_optimization_summary(run_id: str) -> pd.DataFrame:
    return _query(
        """SELECT forecast_date, scenario, status, solver_name, vehicle_count,
                  route_count, demand_line_count, total_units, total_weight_kg,
                  total_pallets, average_occupancy
           FROM logistics.forecast_optimization_runs
           WHERE run_id = %s
           ORDER BY forecast_date, scenario""",
        (run_id,),
    )


def forecast_demand_comparison(run_id: str, run_date: date) -> pd.DataFrame:
    forecast = _query(
        """SELECT forecast_date, SUM(p50_units) AS model_forecast
           FROM logistics.demand_forecast
           WHERE run_id = %s
           GROUP BY forecast_date
           ORDER BY forecast_date""",
        (run_id,),
    )
    history = _query(
        """SELECT date, SUM(units) AS daily_units
           FROM logistics.daily_programming
           WHERE date >= %s AND date <= %s
           GROUP BY date
           ORDER BY date""",
        (run_date - pd.Timedelta(days=56), run_date),
    )
    if forecast.empty or history.empty:
        return pd.DataFrame()
    history["weekday"] = pd.to_datetime(history["date"]).dt.weekday
    weekday_average = history.groupby("weekday")["daily_units"].mean()
    comparison = forecast.copy()
    comparison["weekday"] = pd.to_datetime(comparison["forecast_date"]).dt.weekday
    comparison["moving_average"] = comparison["weekday"].map(weekday_average)
    return comparison[["forecast_date", "model_forecast", "moving_average"]]


def forecast_vehicle_summary(run_id: str, forecast_date: date, scenario: str) -> pd.DataFrame:
    return _query(
        """SELECT origin, destiny, vehicle_id, load_pallets, load_boxes,
                  load_weight_kg, weight_utilization, pallet_utilization
           FROM logistics.forecast_vehicle_assignments
           WHERE run_id = %s AND forecast_date = %s AND scenario = %s
           ORDER BY origin, destiny, vehicle_id""",
        (run_id, forecast_date, scenario),
    )


def forecast_load_plan(run_id: str, forecast_date: date, scenario: str) -> pd.DataFrame:
    return _query(
        """SELECT origin, destiny, vehicle_id, cod_material, units, boxes,
                  pallets, weight_kg, demand_id
           FROM logistics.forecast_load_plan
           WHERE run_id = %s AND forecast_date = %s AND scenario = %s
           ORDER BY origin, destiny, vehicle_id, weight_kg DESC""",
        (run_id, forecast_date, scenario),
    )
