from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from or_aws_fleet.dsql_optimizer import DatabaseSettings


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_NAME = "beverage-demand-units-x4-v1"


def ensure_ledger(conn) -> None:
    cursor = conn.cursor()
    cursor.execute("CREATE SCHEMA IF NOT EXISTS logistics")
    conn.commit()
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS logistics.pipeline_migrations (
               migration_key VARCHAR(180) PRIMARY KEY,
               applied_at TIMESTAMP NOT NULL,
               details VARCHAR(1000) NOT NULL
           )"""
    )
    conn.commit()
    cursor.close()


def already_applied(conn, migration_key: str) -> bool:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT migration_key FROM logistics.pipeline_migrations WHERE migration_key = %s",
        (migration_key,),
    )
    found = cursor.fetchone() is not None
    cursor.close()
    conn.rollback()
    return found


def record_migration(cursor, migration_key: str, details: str) -> None:
    cursor.execute(
        """INSERT INTO logistics.pipeline_migrations (migration_key, applied_at, details)
           VALUES (%s, %s, %s)""",
        (migration_key, datetime.now(timezone.utc).replace(tzinfo=None), details),
    )


def available_dates(conn, through_date: date) -> list[date]:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT DISTINCT date FROM logistics.daily_demand WHERE date <= %s ORDER BY date",
        (through_date,),
    )
    values = [row[0] for row in cursor.fetchall()]
    cursor.close()
    conn.rollback()
    return values


def scale_date(conn, demand_date: date, multiplier: int) -> tuple[int, int]:
    demand_key = f"{MIGRATION_NAME}:daily_demand:{demand_date.isoformat()}"
    demand_rows = 0
    if not already_applied(conn, demand_key):
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE logistics.daily_demand SET units = units * %s WHERE date = %s",
            (multiplier, demand_date),
        )
        demand_rows = cursor.rowcount
        record_migration(cursor, demand_key, f"units multiplied by {multiplier}; rows={demand_rows}")
        conn.commit()
        cursor.close()

    programming_key = f"{MIGRATION_NAME}:daily_programming:{demand_date.isoformat()}"
    programming_rows = 0
    if not already_applied(conn, programming_key):
        cursor = conn.cursor()
        cursor.execute(
            """UPDATE logistics.daily_programming
               SET units = units * %s,
                   total_weight_kg = total_weight_kg * %s,
                   total_pallets = total_pallets * %s,
                   total_boxes = total_boxes * %s
               WHERE date = %s""",
            (multiplier, multiplier, multiplier, multiplier, demand_date),
        )
        programming_rows = cursor.rowcount
        record_migration(
            cursor,
            programming_key,
            f"derived quantities multiplied by {multiplier}; rows={programming_rows}",
        )
        conn.commit()
        cursor.close()
    return demand_rows, programming_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Idempotently multiply historical beverage demand and programming by four."
    )
    parser.add_argument("--multiplier", type=int, default=4)
    parser.add_argument("--through-date", type=date.fromisoformat, default=date.today())
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.multiplier != 4:
        raise ValueError("This versioned migration is specifically reserved for multiplier 4.")
    load_dotenv(ROOT / ".env")
    settings = DatabaseSettings.from_env()
    conn = settings.connect()
    try:
        ensure_ledger(conn)
        dates = available_dates(conn, args.through_date)
        demand_total = 0
        programming_total = 0
        for index, demand_date in enumerate(dates, start=1):
            demand_rows, programming_rows = scale_date(conn, demand_date, args.multiplier)
            demand_total += demand_rows
            programming_total += programming_rows
            if index % 30 == 0 or index == len(dates):
                print(f"processed {index}/{len(dates)} dates through {demand_date}")
        print(
            f"migration={MIGRATION_NAME} dates={len(dates)} "
            f"daily_demand_rows={demand_total} daily_programming_rows={programming_total}"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
