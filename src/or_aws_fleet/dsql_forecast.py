from __future__ import annotations

import json
import math
import os
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd

from or_aws_fleet.dsql_optimizer import DatabaseSettings, execute_multirow_insert
from or_aws_fleet.forecasting import (
    FORECAST_HORIZON_DAYS,
    ForecastMetrics,
    calculate_metrics,
    retraining_decision,
    seasonal_naive_forecast,
)
from or_aws_fleet.programming_model import ProgrammingLine, solve_route


DDL = (
    """CREATE TABLE IF NOT EXISTS logistics.forecast_runs (
        run_id VARCHAR(36) PRIMARY KEY, forecast_run_date DATE NOT NULL,
        created_at TIMESTAMP NOT NULL, model_version VARCHAR(80) NOT NULL,
        horizon_days INTEGER NOT NULL, status VARCHAR(20) NOT NULL,
        wape NUMERIC(10,6), mase NUMERIC(10,6), bias NUMERIC(10,6),
        interval_coverage NUMERIC(10,6), consecutive_failures INTEGER NOT NULL,
        retraining_recommended BOOLEAN NOT NULL,
        retraining_reasons VARCHAR(1000) NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS logistics.demand_forecast (
        run_id VARCHAR(36) NOT NULL, forecast_date DATE NOT NULL,
        origin VARCHAR(40) NOT NULL, destiny VARCHAR(40) NOT NULL,
        cod_material VARCHAR(40) NOT NULL, p10_units INTEGER NOT NULL,
        p50_units INTEGER NOT NULL, p90_units INTEGER NOT NULL,
        qty_by_box INTEGER NOT NULL, qty_by_pallet INTEGER NOT NULL,
        material_weight NUMERIC(16,3) NOT NULL, model_version VARCHAR(80) NOT NULL,
        PRIMARY KEY (run_id, forecast_date, origin, destiny, cod_material)
    )""",
    """CREATE TABLE IF NOT EXISTS logistics.forecast_optimization_runs (
        run_id VARCHAR(36) NOT NULL, forecast_date DATE NOT NULL,
        scenario VARCHAR(10) NOT NULL, status VARCHAR(20) NOT NULL,
        solver_name VARCHAR(80) NOT NULL, vehicle_count INTEGER NOT NULL,
        route_count INTEGER NOT NULL, demand_line_count INTEGER NOT NULL,
        total_units INTEGER NOT NULL, total_weight_kg NUMERIC(18,3) NOT NULL,
        total_pallets NUMERIC(18,6) NOT NULL, average_occupancy NUMERIC(10,6) NOT NULL,
        PRIMARY KEY (run_id, forecast_date, scenario)
    )""",
    """CREATE TABLE IF NOT EXISTS logistics.forecast_vehicle_assignments (
        run_id VARCHAR(36) NOT NULL, forecast_date DATE NOT NULL,
        scenario VARCHAR(10) NOT NULL, vehicle_id VARCHAR(100) NOT NULL,
        origin VARCHAR(40) NOT NULL, destiny VARCHAR(40) NOT NULL,
        load_weight_kg NUMERIC(16,3) NOT NULL, load_pallets NUMERIC(16,6) NOT NULL,
        load_boxes NUMERIC(16,6) NOT NULL, weight_utilization NUMERIC(10,6) NOT NULL,
        pallet_utilization NUMERIC(10,6) NOT NULL,
        PRIMARY KEY (run_id, forecast_date, scenario, vehicle_id)
    )""",
    """CREATE TABLE IF NOT EXISTS logistics.forecast_load_plan (
        run_id VARCHAR(36) NOT NULL, forecast_date DATE NOT NULL,
        scenario VARCHAR(10) NOT NULL, vehicle_id VARCHAR(100) NOT NULL,
        demand_id VARCHAR(80) NOT NULL, origin VARCHAR(40) NOT NULL,
        destiny VARCHAR(40) NOT NULL, cod_material VARCHAR(40) NOT NULL,
        units INTEGER NOT NULL, boxes NUMERIC(16,6) NOT NULL,
        pallets NUMERIC(16,6) NOT NULL, weight_kg NUMERIC(16,3) NOT NULL,
        PRIMARY KEY (run_id, forecast_date, scenario, demand_id)
    )""",
)


def ensure_forecast_tables(conn) -> None:
    cursor = conn.cursor()
    for statement in DDL:
        cursor.execute(statement)
        conn.commit()
    cursor.close()


def insert_committed_batches(conn, prefix: str, rows: list[tuple], columns: int, batch_size: int = 200) -> None:
    """Keep each Aurora DSQL transaction safely below its row-modification limit."""
    for start in range(0, len(rows), batch_size):
        cursor = conn.cursor()
        execute_multirow_insert(cursor, prefix, rows[start : start + batch_size], columns, batch_size)
        conn.commit()
        cursor.close()


def load_history(conn, run_date: date, lookback_days: int = 180) -> pd.DataFrame:
    cursor = conn.cursor()
    cursor.execute(
        """SELECT date, origin, destiny, cod_material, units, qty_by_box,
                  qty_by_pallet, material_weight
           FROM logistics.daily_programming
           WHERE date >= %s AND date <= %s
           ORDER BY date, origin, destiny, cod_material""",
        (run_date - timedelta(days=lookback_days), run_date),
    )
    columns = [column[0] for column in cursor.description]
    frame = pd.DataFrame(cursor.fetchall(), columns=columns)
    cursor.close()
    conn.rollback()
    return frame


def evaluate_latest_forecast(conn, run_date: date) -> tuple[ForecastMetrics | None, int]:
    cursor = conn.cursor()
    cursor.execute(
        """SELECT run_id, consecutive_failures FROM logistics.forecast_runs
           WHERE forecast_run_date < %s AND status = 'COMPLETE'
           ORDER BY forecast_run_date DESC LIMIT 1""",
        (run_date,),
    )
    previous = cursor.fetchone()
    if not previous:
        cursor.close()
        conn.rollback()
        return None, 0
    previous_run_id, previous_failures = previous
    cursor.execute(
        """SELECT f.origin, f.destiny, f.cod_material, f.p10_units, f.p50_units,
                  f.p90_units, COALESCE(SUM(d.units), 0) AS actual_units
           FROM logistics.demand_forecast f
           LEFT JOIN logistics.daily_programming d
             ON d.date = f.forecast_date AND d.origin = f.origin
            AND d.destiny = f.destiny AND d.cod_material = f.cod_material
           WHERE f.run_id = %s AND f.forecast_date = %s
           GROUP BY f.origin, f.destiny, f.cod_material, f.p10_units, f.p50_units, f.p90_units""",
        (previous_run_id, run_date),
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.rollback()
    if not rows:
        return None, int(previous_failures)
    frame = pd.DataFrame(rows, columns=["origin", "destiny", "cod_material", "p10", "p50", "p90", "actual"])
    metrics = calculate_metrics(frame["actual"], frame["p50"], frame["p10"], frame["p90"])
    failed = bool(retraining_decision(metrics, 3, 999).reasons)
    return metrics, int(previous_failures) + 1 if failed else 0


def _lines_for_day(frame: pd.DataFrame, scenario: str, max_weight: float, max_pallets: float) -> list[ProgrammingLine]:
    units_column = f"{scenario.lower()}_units"
    lines: list[ProgrammingLine] = []
    for row in frame.itertuples(index=False):
        total_units = int(getattr(row, units_column))
        if total_units <= 0:
            continue
        qty_box = max(int(row.qty_by_box), 1)
        qty_pallet = max(int(row.qty_by_pallet), 1)
        unit_weight = float(row.material_weight)
        total_weight = total_units * unit_weight / 1000
        total_pallets = total_units / (qty_box * qty_pallet)
        chunks = max(1, math.ceil(max(total_weight / max_weight, total_pallets / max_pallets)))
        remaining = total_units
        for chunk in range(1, chunks + 1):
            units = math.ceil(remaining / (chunks - chunk + 1))
            remaining -= units
            lines.append(
                ProgrammingLine(
                    demand_id=f"{row.forecast_date:%Y%m%d}-{row.origin}-{row.destiny}-{row.cod_material}-{scenario}-{chunk}",
                    origin=str(row.origin), destiny=str(row.destiny), cod_material=str(row.cod_material),
                    total_weight_kg=units * unit_weight / 1000,
                    total_pallets=units / (qty_box * qty_pallet), total_boxes=units / qty_box,
                    units=units, qty_by_box=qty_box,
                )
            )
    return lines


def _optimize(lines: list[ProgrammingLine], max_weight: float, max_pallets: float, time_limit: int):
    routes: dict[tuple[str, str], list[ProgrammingLine]] = defaultdict(list)
    for line in lines:
        routes[(line.origin, line.destiny)].append(line)
    return [solve_route(route, max_weight, max_pallets, time_limit) for route in routes.values()]


def run_daily_forecast(
    run_date: date | None = None,
    max_weight_kg: float = 25_000,
    max_pallets: float = 60,
    time_limit_seconds: int = 30,
) -> str:
    run_date = run_date or datetime.now(timezone.utc).date()
    conn = DatabaseSettings.from_env().connect()
    try:
        ensure_forecast_tables(conn)
        history = load_history(conn, run_date)
        metrics, consecutive_failures = evaluate_latest_forecast(conn, run_date)
        decision = (
            retraining_decision(metrics, consecutive_failures, days_since_training=999)
            if metrics else None
        )
        forecast = seasonal_naive_forecast(history, run_date, FORECAST_HORIZON_DAYS)
        if forecast.empty:
            raise RuntimeError("The forecast pipeline produced no rows.")
        run_id = str(uuid.uuid4())
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO logistics.forecast_runs (
                   run_id, forecast_run_date, created_at, model_version, horizon_days, status,
                   wape, mase, bias, interval_coverage, consecutive_failures,
                   retraining_recommended, retraining_reasons
               ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (run_id, run_date, datetime.now(timezone.utc).replace(tzinfo=None), "seasonal-naive-v1",
             FORECAST_HORIZON_DAYS, "RUNNING", metrics.wape if metrics else None,
             metrics.mase if metrics else None, metrics.bias if metrics else None,
             metrics.interval_coverage if metrics else None, consecutive_failures,
             decision.should_retrain if decision else False,
             json.dumps(decision.reasons if decision else ())),
        )
        forecast_rows = [
            (run_id, row.forecast_date, row.origin, row.destiny, row.cod_material,
             int(row.p10_units), int(row.p50_units), int(row.p90_units), int(row.qty_by_box),
             int(row.qty_by_pallet), Decimal(str(row.material_weight)), row.model_version)
            for row in forecast.itertuples(index=False)
        ]
        conn.commit()
        cursor.close()
        insert_committed_batches(conn, """INSERT INTO logistics.demand_forecast (
            run_id, forecast_date, origin, destiny, cod_material, p10_units, p50_units,
            p90_units, qty_by_box, qty_by_pallet, material_weight, model_version) VALUES """,
            forecast_rows, 12, batch_size=200)

        optimization_rows: list[tuple] = []
        vehicle_rows: list[tuple] = []
        load_rows: list[tuple] = []
        for forecast_date, day_frame in forecast.groupby("forecast_date", sort=True):
            for scenario in ("P50", "P90"):
                lines = _lines_for_day(day_frame, scenario, max_weight_kg, max_pallets)
                solutions = _optimize(lines, max_weight_kg, max_pallets, time_limit_seconds)
                vehicles = [vehicle for solution in solutions for vehicle in solution.vehicles]
                status = "OPTIMAL" if all(item.status == "OPTIMAL" for item in solutions) else "FEASIBLE"
                occupancy = sum(max(v.weight_utilization, v.pallet_utilization) for v in vehicles) / max(len(vehicles), 1)
                optimization_rows.append((run_id, forecast_date, scenario, status,
                    ",".join(sorted({item.solver_name for item in solutions})), len(vehicles), len(solutions),
                    len(lines), sum(line.units for line in lines), sum(line.total_weight_kg for line in lines),
                    sum(line.total_pallets for line in lines), occupancy))
                line_map = {line.demand_id: line for line in lines}
                for vehicle in vehicles:
                    forecast_vehicle_id = f"{forecast_date:%Y%m%d}-{scenario}-{vehicle.vehicle_id}"
                    vehicle_rows.append((run_id, forecast_date, scenario, forecast_vehicle_id,
                        vehicle.origin, vehicle.destiny, vehicle.load_weight_kg, vehicle.load_pallets,
                        vehicle.load_boxes, vehicle.weight_utilization, vehicle.pallet_utilization))
                    for demand_id in vehicle.demand_ids:
                        line = line_map[demand_id]
                        load_rows.append((run_id, forecast_date, scenario, forecast_vehicle_id, demand_id,
                            line.origin, line.destiny, line.cod_material, line.units, line.total_boxes,
                            line.total_pallets, line.total_weight_kg))

        insert_committed_batches(conn, """INSERT INTO logistics.forecast_optimization_runs (
            run_id, forecast_date, scenario, status, solver_name, vehicle_count, route_count,
            demand_line_count, total_units, total_weight_kg, total_pallets, average_occupancy) VALUES """,
            optimization_rows, 12, batch_size=100)
        insert_committed_batches(conn, """INSERT INTO logistics.forecast_vehicle_assignments (
            run_id, forecast_date, scenario, vehicle_id, origin, destiny, load_weight_kg,
            load_pallets, load_boxes, weight_utilization, pallet_utilization) VALUES """,
            vehicle_rows, 11, batch_size=200)
        insert_committed_batches(conn, """INSERT INTO logistics.forecast_load_plan (
            run_id, forecast_date, scenario, vehicle_id, demand_id, origin, destiny, cod_material,
            units, boxes, pallets, weight_kg) VALUES """, load_rows, 12, batch_size=200)
        cursor = conn.cursor()
        cursor.execute("UPDATE logistics.forecast_runs SET status = %s WHERE run_id = %s", ("COMPLETE", run_id))
        conn.commit()
        cursor.close()
        return run_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> None:
    run_date_text = os.getenv("FORECAST_RUN_DATE")
    run_id = run_daily_forecast(date.fromisoformat(run_date_text) if run_date_text else None)
    print(json.dumps({"run_id": run_id, "horizon_days": FORECAST_HORIZON_DAYS}))


if __name__ == "__main__":
    main()
