from __future__ import annotations

import argparse
import os
from datetime import date
from pathlib import Path

from or_aws_fleet.api import SolveRequest, solve


ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path) -> None:
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2026-07-16")
    parser.add_argument("--persist", action="store_true")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    result = solve(
        SolveRequest(
            programming_date=date.fromisoformat(args.date),
            max_weight_kg=25_000,
            max_pallets=60,
            time_limit_seconds=30,
            persist=args.persist,
        )
    )
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
