"""Remove legacy demand/master tables while preserving vehicle_master_data.

Run with the normal AWS/DSQL environment variables loaded from .env.  This
script is intentionally explicit: it never drops logistics.vehicle_master_data.
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg


def main() -> None:
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in raw and not raw.lstrip().startswith("#"):
                key, value = raw.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    region = os.getenv("AWS_REGION", "us-east-2")
    cluster = os.getenv("DSQL_CLUSTER_IDENTIFIER")
    if not cluster:
        raise RuntimeError("DSQL_CLUSTER_IDENTIFIER must be set")
    # Aurora DSQL's endpoint is resolved by the SDK helper used by the loader.
    from upload_sheets_to_dsql import generate_admin_token, resolve_dsql_endpoint

    host = resolve_dsql_endpoint(region)
    with psycopg.connect(
        host=host,
        dbname=os.getenv("DSQL_DATABASE", "postgres"),
        user=os.getenv("DSQL_DB_USER", "admin"),
        password=generate_admin_token(host, region),
        sslmode="require",
        connect_timeout=30,
    ) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            for table in (
                "logistics.master_data_sku_random",
                "logistics.dc_1",
                "logistics.dc_2",
                "logistics.dc_3",
            ):
                cur.execute(f"DROP TABLE IF EXISTS {table}")
                print(f"dropped {table}")
            cur.execute("SELECT to_regclass('logistics.vehicle_master_data')")
            if cur.fetchone()[0] != "logistics.vehicle_master_data":
                raise RuntimeError("vehicle_master_data was not found")
            print("preserved logistics.vehicle_master_data")


if __name__ == "__main__":
    main()
