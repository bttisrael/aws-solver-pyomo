from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import boto3
import psycopg
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials


ROOT = Path(__file__).resolve().parents[1]
SPREADSHEET_ID = "1VAieAYeLDk7NiDkBREptA1gWlSvDZyFJRhjHJHrRotE"
SKU_TAB = "MASTER DATA - SKU RANDOM"
VEHICLE_TAB = "VEHICLE MASTER DATA"
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS logistics"

SKU_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS logistics.master_data_sku_random (
    description TEXT NOT NULL,
    sku TEXT PRIMARY KEY,
    type_of_box VARCHAR(2) NOT NULL,
    box_m3 NUMERIC(10, 6) NOT NULL,
    sku_weight INTEGER NOT NULL,
    sku_m3 NUMERIC(10, 6) NOT NULL,
    box_fillrate NUMERIC(10, 6) NOT NULL,
    category TEXT NOT NULL,
    box_weight INTEGER NOT NULL,
    sku_unit_measurement VARCHAR(20) NOT NULL,
    box_unit_measurement VARCHAR(20) NOT NULL
)
""".strip()

VEHICLE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS logistics.vehicle_master_data (
    vehicle_type TEXT PRIMARY KEY,
    vehicle_capacity_m3 NUMERIC(10, 2) NOT NULL,
    vehicle_capacity_kg INTEGER NOT NULL,
    freight_cost_per_km NUMERIC(10, 2) NOT NULL
)
""".strip()

SKU_COLUMNS = [
    "description",
    "sku",
    "type_of_box",
    "box_m3",
    "sku_weight",
    "sku_m3",
    "box_fillrate",
    "category",
    "box_weight",
    "sku_unit_measurement",
    "box_unit_measurement",
]

VEHICLE_COLUMNS = [
    "vehicle_type",
    "vehicle_capacity_m3",
    "vehicle_capacity_kg",
    "freight_cost_per_km",
]


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.strip().lower())


def decimal_value(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def int_value(value: Any) -> int | None:
    dec = decimal_value(value)
    return None if dec is None else int(dec)


def get_required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def auth_google() -> Credentials:
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or str(ROOT / "service-account.json")
    creds = Credentials.from_service_account_file(credentials_path, scopes=GOOGLE_SCOPES)
    creds.refresh(Request())
    return creds


def sheets_get_values(creds: Credentials, range_name: str) -> list[list[str]]:
    encoded_range = urllib.parse.quote(range_name, safe="")
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}"
        f"/values/{encoded_range}?majorDimension=ROWS"
    )
    last_error: str | None = None
    for attempt in range(7):
        creds.refresh(Request())
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {creds.token}"})
        try:
            body = urllib.request.urlopen(req, timeout=180).read().decode()
            return json.loads(body).get("values", [])
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            last_error = f"HTTP {exc.code}: {detail[:800]}"
            if exc.code not in (429, 500, 502, 503, 504):
                raise RuntimeError(last_error) from exc
            time.sleep(min(60, 2**attempt))
    raise RuntimeError(last_error or "Google Sheets request failed")


def read_sheet_table(creds: Credentials, tab: str, range_suffix: str) -> tuple[list[str], list[list[str]]]:
    rows = sheets_get_values(creds, f"'{tab}'!{range_suffix}")
    if not rows:
        raise RuntimeError(f"No rows returned from tab {tab!r}")
    headers = rows[0]
    return headers, rows[1:]


def row_dict(headers: list[str], row: list[str]) -> dict[str, str]:
    mapping = {normalize_header(header): idx for idx, header in enumerate(headers)}
    values: dict[str, str] = {}
    for key, idx in mapping.items():
        values[key] = row[idx] if idx < len(row) else ""
    return values


def prepare_sku_rows(headers: list[str], rows: list[list[str]]) -> list[tuple[Any, ...]]:
    prepared: list[tuple[Any, ...]] = []
    for row in rows:
        data = row_dict(headers, row)
        sku = data.get("sku", "").strip()
        if not sku:
            continue
        prepared.append(
            (
                data.get("description", "").strip(),
                sku,
                data.get("typeofbox", "").strip(),
                decimal_value(data.get("boxm3")),
                int_value(data.get("skuweight")),
                decimal_value(data.get("skum3")),
                decimal_value(data.get("boxfillrate")),
                data.get("category", "").strip(),
                int_value(data.get("boxweight")),
                data.get("skuunitmeasurement", "").strip(),
                data.get("boxunitmeasurement", "").strip(),
            )
        )
    return prepared


def prepare_vehicle_rows(headers: list[str], rows: list[list[str]]) -> list[tuple[Any, ...]]:
    prepared: list[tuple[Any, ...]] = []
    for row in rows:
        data = row_dict(headers, row)
        vehicle_type = data.get("vehicletype", "").strip()
        if not vehicle_type:
            continue
        prepared.append(
            (
                vehicle_type,
                decimal_value(data.get("vehiclecapacitym3")),
                int_value(data.get("vehiclecapacitykg")),
                decimal_value(data.get("freightcostperkm")),
            )
        )
    return prepared


def aws_region() -> str:
    return (
        os.getenv("DSQL_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or os.getenv("AWS_REGION")
        or "sa-east-1"
    )


def resolve_dsql_endpoint(region: str) -> str:
    endpoint = os.getenv("DSQL_CLUSTER_ENDPOINT")
    if endpoint:
        return endpoint

    identifier = os.getenv("DSQL_CLUSTER_IDENTIFIER") or os.getenv("DSQL_CLUSTER_ID")
    if not identifier:
        raise RuntimeError(
            "Set DSQL_CLUSTER_ENDPOINT, or set DSQL_CLUSTER_IDENTIFIER/DSQL_CLUSTER_ID "
            "so the script can discover the endpoint."
        )

    client = boto3.client("dsql", region_name=region)
    cluster = client.get_cluster(identifier=identifier)
    return cluster["endpoint"]


def generate_admin_token(endpoint: str, region: str) -> str:
    client = boto3.client("dsql", region_name=region)
    return client.generate_db_connect_admin_auth_token(endpoint, region)


def connect_dsql(endpoint: str, region: str) -> psycopg.Connection:
    token = generate_admin_token(endpoint, region)
    return psycopg.connect(
        host=endpoint,
        dbname=os.getenv("DSQL_DATABASE", "postgres"),
        user=os.getenv("DSQL_DB_USER", "admin"),
        password=token,
        sslmode="require",
        connect_timeout=30,
    )


def execute_ddl(conn: psycopg.Connection, sql: str) -> None:
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(sql)


def recreate_tables(conn: psycopg.Connection) -> None:
    for sql in (
        "DROP TABLE IF EXISTS logistics.master_data_sku_random",
        SCHEMA_SQL,
        VEHICLE_TABLE_SQL,
    ):
        execute_ddl(conn, sql)


def ensure_tables(conn: psycopg.Connection) -> None:
    for sql in (SCHEMA_SQL, VEHICLE_TABLE_SQL):
        execute_ddl(conn, sql)


def upsert_rows(
    conn: psycopg.Connection,
    table: str,
    columns: list[str],
    pk_column: str,
    rows: list[tuple[Any, ...]],
    batch_size: int,
) -> int:
    if batch_size > 3000:
        raise ValueError("Aurora DSQL DML transactions can modify at most 3,000 rows.")
    placeholders = ", ".join(["%s"] * len(columns))
    column_sql = ", ".join(columns)
    update_sql = ", ".join(f"{col} = EXCLUDED.{col}" for col in columns if col != pk_column)
    sql = (
        f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders}) "
        f"ON CONFLICT ({pk_column}) DO UPDATE SET {update_sql}"
    )

    conn.autocommit = False
    inserted = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        with conn.cursor() as cur:
            cur.executemany(sql, batch)
        conn.commit()
        inserted += len(batch)
        print(f"uploaded {table}: {inserted}/{len(rows)} rows")
    return inserted


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload prepared Google Sheets tabs to Aurora DSQL.")
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--replace", action="store_true", help="Drop and recreate target tables before loading.")
    parser.add_argument("--dry-run", action="store_true", help="Read sheets and validate AWS settings, but do not connect.")
    parser.add_argument("--skip-master", action="store_true")
    parser.add_argument("--skip-vehicle", action="store_true")
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

    if args.batch_size < 1 or args.batch_size > 3000:
        raise RuntimeError("--batch-size must be between 1 and 3000 for Aurora DSQL.")

    google_creds = auth_google()
    vehicle_rows: list[tuple[Any, ...]] = []

    if not args.skip_master:
        print("master SKU upload is disabled; use daily_programming as the optimizer input")

    if not args.skip_vehicle:
        vehicle_headers, vehicle_raw_rows = read_sheet_table(google_creds, VEHICLE_TAB, "A1:D100")
        vehicle_rows = prepare_vehicle_rows(vehicle_headers, vehicle_raw_rows)
        print(f"prepared vehicle rows: {len(vehicle_rows)}")

    region = aws_region()
    print(f"DSQL region: {region}")

    if args.dry_run:
        print("dry run complete; no DSQL endpoint lookup or connection opened.")
        return

    endpoint = resolve_dsql_endpoint(region)
    print(f"DSQL endpoint: {endpoint}")

    with connect_dsql(endpoint, region) as conn:
        if args.replace:
            recreate_tables(conn)
        else:
            ensure_tables(conn)

        if vehicle_rows:
            upsert_rows(
                conn,
                "logistics.vehicle_master_data",
                VEHICLE_COLUMNS,
                "vehicle_type",
                vehicle_rows,
                args.batch_size,
            )

    print("Aurora DSQL upload complete.")


if __name__ == "__main__":
    main()

