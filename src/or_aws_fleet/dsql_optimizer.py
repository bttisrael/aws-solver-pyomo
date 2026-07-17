from __future__ import annotations

import os
import ssl
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

import boto3
import pg8000.dbapi

from or_aws_fleet.programming_model import ProgrammingLine, RouteSolution


CREATE_RUNS_SQL = """
CREATE TABLE IF NOT EXISTS logistics.optimization_runs (
    run_id VARCHAR(36) PRIMARY KEY,
    programming_date DATE NOT NULL,
    created_at TIMESTAMP NOT NULL,
    status VARCHAR(20) NOT NULL,
    solver_name VARCHAR(40) NOT NULL,
    vehicle_count INTEGER NOT NULL,
    route_count INTEGER NOT NULL,
    demand_line_count INTEGER NOT NULL,
    total_weight_kg NUMERIC(18, 3) NOT NULL,
    total_pallets NUMERIC(18, 6) NOT NULL,
    max_weight_kg NUMERIC(12, 3) NOT NULL,
    max_pallets NUMERIC(10, 3) NOT NULL
)
""".strip()

CREATE_VEHICLES_SQL = """
CREATE TABLE IF NOT EXISTS logistics.optimization_vehicle_assignments (
    run_id VARCHAR(36) NOT NULL,
    vehicle_id VARCHAR(100) NOT NULL,
    origin VARCHAR(40) NOT NULL,
    destiny VARCHAR(40) NOT NULL,
    load_weight_kg NUMERIC(16, 3) NOT NULL,
    load_pallets NUMERIC(16, 6) NOT NULL,
    load_boxes NUMERIC(16, 6) NOT NULL,
    weight_utilization NUMERIC(10, 6) NOT NULL,
    pallet_utilization NUMERIC(10, 6) NOT NULL,
    PRIMARY KEY (run_id, vehicle_id)
)
""".strip()

CREATE_LINES_SQL = """
CREATE TABLE IF NOT EXISTS logistics.optimization_line_assignments (
    run_id VARCHAR(36) NOT NULL,
    demand_id VARCHAR(40) NOT NULL,
    vehicle_id VARCHAR(100) NOT NULL,
    PRIMARY KEY (run_id, demand_id)
)
""".strip()

CREATE_LOAD_PLAN_SQL = """
CREATE TABLE IF NOT EXISTS logistics.optimization_load_plan (
    run_id VARCHAR(36) NOT NULL,
    vehicle_id VARCHAR(100) NOT NULL,
    position_number INTEGER NOT NULL,
    load_level VARCHAR(10) NOT NULL,
    load_item_label VARCHAR(40) NOT NULL,
    origin VARCHAR(40) NOT NULL,
    destination VARCHAR(40) NOT NULL,
    material_code VARCHAR(20) NOT NULL,
    boxes NUMERIC(16, 6) NOT NULL,
    units_by_material INTEGER NOT NULL,
    total_units INTEGER NOT NULL,
    weight_kg NUMERIC(16, 3) NOT NULL,
    pallet_volume NUMERIC(16, 6) NOT NULL,
    demand_id VARCHAR(40) NOT NULL,
    PRIMARY KEY (run_id, demand_id)
)
""".strip()


@dataclass(frozen=True)
class DatabaseSettings:
    region: str
    endpoint: str
    database: str
    user: str

    @classmethod
    def from_env(cls) -> "DatabaseSettings":
        region = os.getenv("DSQL_REGION") or os.getenv("AWS_REGION") or "us-east-2"
        client = boto3.client("dsql", region_name=region)
        endpoint = os.getenv("DSQL_CLUSTER_ENDPOINT")
        if not endpoint:
            identifier = os.getenv("DSQL_CLUSTER_IDENTIFIER") or os.getenv("DSQL_CLUSTER_ID")
            if not identifier:
                raise RuntimeError("Set DSQL_CLUSTER_ENDPOINT or DSQL_CLUSTER_IDENTIFIER.")
            endpoint = client.get_cluster(identifier=identifier)["endpoint"]
        return cls(
            region=region,
            endpoint=endpoint,
            database=os.getenv("DSQL_DATABASE", "postgres"),
            user=os.getenv("DSQL_DB_USER", "admin"),
        )

    def connect(self):
        token = boto3.client("dsql", region_name=self.region).generate_db_connect_admin_auth_token(
            self.endpoint, self.region
        )
        return pg8000.dbapi.connect(
            host=self.endpoint,
            database=self.database,
            user=self.user,
            password=token,
            ssl_context=ssl.create_default_context(),
            timeout=30,
        )


def load_programming(conn, programming_date: date) -> list[ProgrammingLine]:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT demand_id, origin, destiny, cod_material,
               total_weight_kg, total_pallets, total_boxes, units, qty_by_box
        FROM logistics.daily_programming
        WHERE date = %s
        ORDER BY origin, destiny, demand_id
        """,
        (programming_date,),
    )
    rows = [
        ProgrammingLine(
            demand_id=str(demand_id),
            origin=str(origin),
            destiny=str(destiny),
            cod_material=str(cod_material),
            total_weight_kg=float(weight),
            total_pallets=float(pallets),
            total_boxes=float(boxes),
            units=int(units),
            qty_by_box=int(qty_by_box),
        )
        for demand_id, origin, destiny, cod_material, weight, pallets, boxes, units, qty_by_box in cursor.fetchall()
    ]
    cursor.close()
    conn.rollback()
    return rows


def ensure_result_tables(conn) -> None:
    cursor = conn.cursor()
    for statement in (CREATE_RUNS_SQL, CREATE_VEHICLES_SQL, CREATE_LINES_SQL, CREATE_LOAD_PLAN_SQL):
        cursor.execute(statement)
        conn.commit()
    cursor.close()


def execute_multirow_insert(cursor, prefix: str, rows: list[tuple], columns: int, batch_size: int = 500) -> None:
    placeholders = "(" + ", ".join(["%s"] * columns) + ")"
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        statement = prefix + ", ".join([placeholders] * len(batch))
        parameters = tuple(value for row in batch for value in row)
        cursor.execute(statement, parameters)


def persist_solution(
    conn,
    programming_date: date,
    lines: list[ProgrammingLine],
    solutions: list[RouteSolution],
    max_weight_kg: float,
    max_pallets: float,
) -> str:
    ensure_result_tables(conn)
    run_id = str(uuid.uuid4())
    vehicles = [vehicle for solution in solutions for vehicle in solution.vehicles]
    statuses = {solution.status for solution in solutions}
    status = "OPTIMAL" if statuses == {"OPTIMAL"} else "FEASIBLE" if "HEURISTIC" not in statuses else "HEURISTIC"
    solvers = sorted({solution.solver_name for solution in solutions})

    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO logistics.optimization_runs (
            run_id, programming_date, created_at, status, solver_name,
            vehicle_count, route_count, demand_line_count, total_weight_kg,
            total_pallets, max_weight_kg, max_pallets
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            run_id,
            programming_date,
            datetime.now(timezone.utc).replace(tzinfo=None),
            status,
            ",".join(solvers),
            len(vehicles),
            len(solutions),
            len(lines),
            Decimal(str(round(sum(line.total_weight_kg for line in lines), 3))),
            Decimal(str(round(sum(line.total_pallets for line in lines), 6))),
            Decimal(str(max_weight_kg)),
            Decimal(str(max_pallets)),
        ),
    )
    vehicle_rows = [
        (
            run_id, vehicle.vehicle_id, vehicle.origin, vehicle.destiny,
            vehicle.load_weight_kg, vehicle.load_pallets, vehicle.load_boxes,
            vehicle.weight_utilization, vehicle.pallet_utilization,
        )
        for vehicle in vehicles
    ]
    execute_multirow_insert(
        cursor,
        """
        INSERT INTO logistics.optimization_vehicle_assignments (
            run_id, vehicle_id, origin, destiny, load_weight_kg, load_pallets,
            load_boxes, weight_utilization, pallet_utilization
        ) VALUES
        """,
        vehicle_rows,
        9,
    )
    line_rows = [
        (run_id, demand_id, vehicle.vehicle_id)
        for vehicle in vehicles
        for demand_id in vehicle.demand_ids
    ]
    execute_multirow_insert(
        cursor,
        """
        INSERT INTO logistics.optimization_line_assignments (run_id, demand_id, vehicle_id)
        VALUES
        """,
        line_rows,
        3,
    )
    lines_by_id = {line.demand_id: line for line in lines}
    load_plan_rows: list[tuple] = []
    item_sequence = 1
    for vehicle in vehicles:
        assigned = sorted(
            (lines_by_id[demand_id] for demand_id in vehicle.demand_ids),
            key=lambda line: line.total_weight_kg,
            reverse=True,
        )
        base_count = (len(assigned) + 1) // 2
        bases = assigned[:base_count]
        tops = assigned[base_count:]
        ordered = []
        for position, base in enumerate(bases, start=1):
            ordered.append((position, "BASE", base))
            if position <= len(tops):
                ordered.append((position, "TOP", tops[position - 1]))
        for position, level, line in ordered:
            load_plan_rows.append(
                (
                    run_id,
                    vehicle.vehicle_id,
                    position,
                    level,
                    f"LOAD ITEM {item_sequence:05d} {level}",
                    line.origin,
                    line.destiny,
                    line.cod_material,
                    line.total_boxes,
                    line.units,
                    line.units,
                    line.total_weight_kg,
                    line.total_pallets,
                    line.demand_id,
                )
            )
            item_sequence += 1
    execute_multirow_insert(
        cursor,
        """
        INSERT INTO logistics.optimization_load_plan (
            run_id, vehicle_id, position_number, load_level, load_item_label,
            origin, destination, material_code, boxes, units_by_material,
            total_units, weight_kg, pallet_volume, demand_id
        ) VALUES
        """,
        load_plan_rows,
        14,
    )
    conn.commit()
    cursor.close()
    return run_id
