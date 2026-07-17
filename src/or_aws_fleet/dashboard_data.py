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
               total_weight_kg, total_pallets, total_boxes
        FROM logistics.daily_programming
        WHERE date = %s
        ORDER BY origin, destiny, cod_material, demand_id
        """,
        (programming_date,),
    )


def optimization_runs(limit: int = 50) -> pd.DataFrame:
    return _query(
        """
        SELECT run_id, programming_date, created_at, status, solver_name,
               vehicle_count, route_count, demand_line_count, total_weight_kg,
               total_pallets, max_weight_kg, max_pallets
        FROM logistics.optimization_runs
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (limit,),
    )


def vehicle_summary(run_id: str) -> pd.DataFrame:
    return _query(
        """
        SELECT origin, destiny, vehicle_id, load_pallets, load_boxes,
               load_weight_kg, weight_utilization, pallet_utilization
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
        """SELECT run_id, forecast_run_date, created_at, model_version, horizon_days,
                  status, wape, mase, bias, interval_coverage, retraining_recommended,
                  retraining_reasons
           FROM logistics.forecast_runs
           WHERE status = 'COMPLETE'
           ORDER BY created_at DESC
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
