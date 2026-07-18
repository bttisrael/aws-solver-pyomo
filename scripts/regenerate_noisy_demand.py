"""Regenerate historical demand with the production noise model.

The operation replaces one date at a time, so every committed day is resumable and
Aurora DSQL transactions remain bounded. Credentials are read from the local .env
and are never written to output or source control.
"""

from __future__ import annotations

import argparse
import importlib.util
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
    conn = handler.connect_dsql(endpoint, region)
    total_rows = 0
    try:
        handler.ensure_table(conn)
        materials = handler.load_materials(conn)
        run_dates = list(dates_between(args.start_date, args.end_date))
        for index, run_date in enumerate(run_dates, 1):
            rows, factor = handler.generate_rows(
                run_date,
                materials,
                args.baseline_rows,
                args.seed,
                args.units_multiplier,
            )
            if not args.dry_run:
                handler.replace_daily_rows(conn, run_date, rows)
                programming_rows = handler.replace_daily_programming(conn, run_date)
                if programming_rows != len(rows):
                    raise RuntimeError(
                        f"Programming row mismatch for {run_date}: {programming_rows} != {len(rows)}"
                    )
            total_rows += len(rows)
            if index == 1 or index % 25 == 0 or index == len(run_dates):
                print(
                    f"progress={index}/{len(run_dates)} date={run_date} "
                    f"rows={len(rows)} factor={factor:.4f}"
                )
    finally:
        conn.close()
    print(
        f"completed dates={len(run_dates)} rows={total_rows} dry_run={args.dry_run} "
        f"units_multiplier={args.units_multiplier}"
    )


if __name__ == "__main__":
    main()
