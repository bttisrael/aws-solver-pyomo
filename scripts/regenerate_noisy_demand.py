"""Regenerate historical demand with the production noise model.

The operation replaces one date at a time, so every committed day is resumable and
Aurora DSQL transactions remain bounded. Credentials are read from the local .env
and are never written to output or source control.
"""

from __future__ import annotations

import argparse
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_handler():
    path = PROJECT_ROOT / "lambda" / "dsql_daily_demand" / "handler.py"
    spec = importlib.util.spec_from_file_location("noisy_demand_handler", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load demand handler from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dates_between(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2025, 1, 1))
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--baseline-rows", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=271828)
    parser.add_argument("--units-multiplier", type=int, default=4)
    parser.add_argument(
        "--programming-days",
        type=int,
        default=30,
        help="Rebuild daily_programming only for this rolling number of latest dates.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Concurrent date-level database workers used for historical backfills.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.end_date < args.start_date:
        raise ValueError("end-date must be on or after start-date")
    load_dotenv(PROJECT_ROOT / ".env")
    handler = load_handler()
    region = handler.aws_region()
    endpoint = handler.resolve_dsql_endpoint(region)
    programming_start = args.end_date - timedelta(days=max(args.programming_days - 1, 0))
    total_rows = 0
    conn = handler.connect_dsql(endpoint, region)
    try:
        handler.ensure_table(conn)
        materials = handler.load_materials(conn)
        if not args.dry_run and args.programming_days > 0:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT DISTINCT date FROM logistics.daily_programming "
                "WHERE date < %s ORDER BY date",
                (programming_start,),
            )
            expired_dates = [row[0] for row in cursor.fetchall()]
            for expired_date in expired_dates:
                cursor.execute(
                    "DELETE FROM logistics.daily_programming WHERE date = %s",
                    (expired_date,),
                )
                conn.commit()
            cursor.close()
    finally:
        conn.close()

    run_dates = list(dates_between(args.start_date, args.end_date))

    def process_date(run_date: date):
        rows, factor = handler.generate_rows(
            run_date,
            materials,
            args.baseline_rows,
            args.seed,
            args.units_multiplier,
        )
        if not args.dry_run:
            worker_conn = handler.connect_dsql(endpoint, region)
            try:
                handler.replace_daily_rows(worker_conn, run_date, rows)
                if args.programming_days > 0 and run_date >= programming_start:
                    programming_rows = handler.replace_daily_programming(worker_conn, run_date)
                    if programming_rows != len(rows):
                        raise RuntimeError(
                            f"Programming row mismatch for {run_date}: "
                            f"{programming_rows} != {len(rows)}"
                        )
            finally:
                worker_conn.close()
        return run_date, len(rows), factor

    with ThreadPoolExecutor(max_workers=max(args.workers, 1)) as executor:
        for index, (run_date, row_count, factor) in enumerate(
            executor.map(process_date, run_dates), 1
        ):
            total_rows += row_count
            if index == 1 or index % 25 == 0 or index == len(run_dates):
                print(
                    f"progress={index}/{len(run_dates)} date={run_date} "
                    f"rows={row_count} factor={factor:.4f}",
                    flush=True,
                )
    print(
        f"completed dates={len(run_dates)} rows={total_rows} dry_run={args.dry_run} "
        f"units_multiplier={args.units_multiplier}"
    )


if __name__ == "__main__":
    main()
