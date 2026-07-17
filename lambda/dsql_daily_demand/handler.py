from __future__ import annotations

import json
import os
import random
import ssl
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import boto3
import pg8000.dbapi


DEFAULT_TIMEZONE = "America/Sao_Paulo"
FACTORIES = (
    "FAC_01_SERRA_NORTE",
    "FAC_02_VALE_LESTE",
    "FAC_03_PLANALTO_CENTRAL",
    "FAC_04_COSTA_SUL",
    "FAC_05_RIO_OESTE",
)
DISTRIBUTION_CENTERS = (
    "DC_01_NORTE",
    "DC_02_NORDESTE",
    "DC_03_CENTRO_OESTE",
    "DC_04_SUDESTE_1",
    "DC_05_SUDESTE_2",
    "DC_06_SUL_1",
    "DC_07_SUL_2",
    "DC_08_INTERIOR_1",
    "DC_09_INTERIOR_2",
)
MONTH_FACTORS = {
    1: 1.24, 2: 1.18, 3: 1.07, 4: 0.97, 5: 0.89, 6: 0.84,
    7: 0.86, 8: 0.91, 9: 0.98, 10: 1.06, 11: 1.15, 12: 1.34,
}
WEEKDAY_FACTORS = (1.08, 1.05, 1.03, 1.08, 1.17, 0.86, 0.73)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS logistics.daily_demand (
    demand_id VARCHAR(40) PRIMARY KEY,
    origin VARCHAR(40) NOT NULL,
    destiny VARCHAR(40) NOT NULL,
    cod_material VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    time_to_ship_days SMALLINT NOT NULL,
    units INTEGER NOT NULL
)
""".strip()

INSERT_SQL = """
INSERT INTO logistics.daily_demand (
    demand_id, origin, destiny, cod_material, date, time_to_ship_days, units
) VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (demand_id) DO UPDATE SET
    origin = EXCLUDED.origin,
    destiny = EXCLUDED.destiny,
    cod_material = EXCLUDED.cod_material,
    date = EXCLUDED.date,
    time_to_ship_days = EXCLUDED.time_to_ship_days,
    units = EXCLUDED.units
""".strip()

CREATE_PROGRAMMING_SQL = """
CREATE TABLE IF NOT EXISTS logistics.daily_programming (
    demand_id VARCHAR(40) PRIMARY KEY,
    origin VARCHAR(40) NOT NULL,
    destiny VARCHAR(40) NOT NULL,
    cod_material VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    time_to_ship_days SMALLINT NOT NULL,
    units INTEGER NOT NULL,
    qty_by_box INTEGER NOT NULL,
    qty_by_pallet INTEGER NOT NULL,
    material_weight INTEGER NOT NULL,
    total_weight_kg NUMERIC(16, 3) NOT NULL,
    total_pallets NUMERIC(16, 6) NOT NULL,
    total_boxes NUMERIC(16, 6) NOT NULL
)
""".strip()

INSERT_PROGRAMMING_SQL = """
INSERT INTO logistics.daily_programming (
    demand_id, origin, destiny, cod_material, date, time_to_ship_days, units,
    qty_by_box, qty_by_pallet, material_weight,
    total_weight_kg, total_pallets, total_boxes
)
SELECT
    demand.demand_id,
    demand.origin,
    demand.destiny,
    demand.cod_material,
    demand.date,
    demand.time_to_ship_days,
    demand.units,
    master.qty_by_box,
    master.qty_by_pallet,
    master.material_weight,
    ROUND((demand.units * master.material_weight)::NUMERIC / 1000, 3),
    ROUND(demand.units::NUMERIC / (master.qty_by_box * master.qty_by_pallet), 6),
    ROUND(demand.units::NUMERIC / master.qty_by_box, 6)
FROM logistics.daily_demand AS demand
JOIN logistics.master_data AS master
    ON master.cod_material = demand.cod_material
WHERE demand.date = %s
""".strip()


def aws_region() -> str:
    return os.getenv("DSQL_REGION") or os.getenv("AWS_REGION") or "us-east-2"


def resolve_dsql_endpoint(region: str) -> str:
    configured = os.getenv("DSQL_CLUSTER_ENDPOINT")
    if configured:
        return configured
    identifier = os.getenv("DSQL_CLUSTER_IDENTIFIER") or os.getenv("DSQL_CLUSTER_ID")
    if not identifier:
        raise RuntimeError("Set DSQL_CLUSTER_ENDPOINT or DSQL_CLUSTER_IDENTIFIER.")
    return boto3.client("dsql", region_name=region).get_cluster(identifier=identifier)["endpoint"]


def connect_dsql(endpoint: str, region: str):
    token = boto3.client("dsql", region_name=region).generate_db_connect_admin_auth_token(endpoint, region)
    return pg8000.dbapi.connect(
        host=endpoint,
        database=os.getenv("DSQL_DATABASE", "postgres"),
        user=os.getenv("DSQL_DB_USER", "admin"),
        password=token,
        ssl_context=ssl.create_default_context(),
        timeout=30,
    )


def parse_run_date(event: dict[str, Any]) -> date:
    value = event.get("run_date") or os.getenv("RUN_DATE")
    if value:
        return date.fromisoformat(str(value))
    timezone_name = event.get("timezone") or os.getenv("LOCAL_TIMEZONE") or DEFAULT_TIMEZONE
    return datetime.now(ZoneInfo(str(timezone_name))).date()


def stable_seed(run_date: date, seed: int) -> int:
    return seed + int(run_date.strftime("%Y%m%d"))


def demand_factor(run_date: date, seed: int) -> float:
    rng = random.Random(stable_seed(run_date, seed) + 91_337)
    annual_growth = 1.0 + max(0, run_date.year - 2025) * 0.045
    daily_noise = max(0.72, min(1.30, rng.gauss(1.0, 0.095)))
    calendar_effect = 1.0
    if (run_date.month == 12 and run_date.day >= 10) or (run_date.month == 1 and run_date.day <= 7):
        calendar_effect *= 1.16
    event_roll = rng.random()
    if event_roll < 0.025:
        calendar_effect *= rng.uniform(1.22, 1.48)
    elif event_roll > 0.985:
        calendar_effect *= rng.uniform(0.62, 0.82)
    return MONTH_FACTORS[run_date.month] * WEEKDAY_FACTORS[run_date.weekday()] * annual_growth * daily_noise * calendar_effect


def load_materials(conn) -> list[tuple[str, int]]:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT cod_material, qty_by_box FROM logistics.master_data "
        "WHERE cod_material IS NOT NULL ORDER BY cod_material"
    )
    rows = [(str(code), int(qty_by_box)) for code, qty_by_box in cursor.fetchall()]
    cursor.close()
    conn.rollback()
    if not rows:
        raise RuntimeError("No materials found in logistics.master_data.")
    return rows


def choose_case_count(rng: random.Random, seasonal_factor: float) -> int:
    return max(1, round(rng.triangular(1, 30, 8) * (0.72 + 0.28 * seasonal_factor)))


def generate_rows(
    run_date: date,
    materials: list[tuple[str, int]],
    baseline_rows: int,
    seed: int,
    units_multiplier: int,
):
    factor = demand_factor(run_date, seed)
    row_count = max(len(FACTORIES) * len(DISTRIBUTION_CENTERS), round(baseline_rows * factor))
    rng = random.Random(stable_seed(run_date, seed))
    routes = [(origin, destiny) for origin in FACTORIES for destiny in DISTRIBUTION_CENTERS]
    rng.shuffle(routes)
    popular = rng.sample(materials, min(240, len(materials)))
    rows = []
    for sequence in range(1, row_count + 1):
        if sequence <= len(routes):
            origin, destiny = routes[sequence - 1]
        else:
            origin = rng.choices(FACTORIES, weights=(24, 22, 21, 18, 15), k=1)[0]
            destiny = rng.choices(DISTRIBUTION_CENTERS, weights=(8, 11, 12, 17, 15, 10, 8, 10, 9), k=1)[0]
        material_pool = popular if rng.random() < 0.72 else materials
        cod_material, qty_by_box = rng.choice(material_pool)
        units = qty_by_box * choose_case_count(rng, factor) * units_multiplier
        rows.append(
            (
                f"DEM-{run_date:%Y%m%d}-{sequence:06d}",
                origin,
                destiny,
                cod_material,
                run_date,
                3,
                units,
            )
        )
    return rows, factor


def ensure_table(conn) -> None:
    cursor = conn.cursor()
    cursor.execute("CREATE SCHEMA IF NOT EXISTS logistics")
    conn.commit()
    cursor.execute(CREATE_TABLE_SQL)
    conn.commit()
    cursor.execute(CREATE_PROGRAMMING_SQL)
    conn.commit()
    cursor.close()


def replace_daily_rows(conn, run_date: date, rows) -> None:
    cursor = conn.cursor()
    cursor.execute("DELETE FROM logistics.daily_demand WHERE date = %s", (run_date,))
    cursor.executemany(INSERT_SQL, rows)
    conn.commit()
    cursor.close()


def replace_daily_programming(conn, run_date: date) -> int:
    cursor = conn.cursor()
    cursor.execute("DELETE FROM logistics.daily_programming WHERE date = %s", (run_date,))
    cursor.execute(INSERT_PROGRAMMING_SQL, (run_date,))
    inserted = cursor.rowcount
    conn.commit()
    cursor.close()
    return inserted


def lambda_handler(event, context):
    event = event or {}
    run_date = parse_run_date(event)
    dry_run = bool(event.get("dry_run", False))
    baseline_rows = int(event.get("baseline_rows") or os.getenv("BASELINE_DEMAND_ROWS", "1000"))
    seed = int(event.get("seed") or os.getenv("DEMAND_SEED", "271828"))
    units_multiplier = int(
        event.get("units_multiplier") or os.getenv("DEMAND_UNITS_MULTIPLIER", "4")
    )
    if units_multiplier < 1:
        raise ValueError("DEMAND_UNITS_MULTIPLIER must be at least 1.")
    region = aws_region()
    endpoint = resolve_dsql_endpoint(region)

    conn = connect_dsql(endpoint, region)
    try:
        ensure_table(conn)
        materials = load_materials(conn)
        rows, factor = generate_rows(
            run_date, materials, baseline_rows, seed, units_multiplier
        )
        programming_rows = 0
        if not dry_run:
            replace_daily_rows(conn, run_date, rows)
            programming_rows = replace_daily_programming(conn, run_date)
    finally:
        conn.close()

    summary = {
        "run_date": run_date.isoformat(),
        "dry_run": dry_run,
        "rows": len(rows),
        "origins": len(FACTORIES),
        "destinies": len(DISTRIBUTION_CENTERS),
        "available_materials": len(materials),
        "seasonal_factor": round(factor, 6),
        "time_to_ship_days": 3,
        "units_multiplier": units_multiplier,
        "table": "logistics.daily_demand",
        "programming_table": "logistics.daily_programming",
        "programming_rows": programming_rows,
    }
    print(json.dumps(summary, sort_keys=True))
    return summary
