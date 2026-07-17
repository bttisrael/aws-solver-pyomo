from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(minimum, parsed)


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return max(minimum, parsed)


def parse_run_date(value: str | None) -> date:
    if not value:
        return datetime.utcnow().date()
    return datetime.strptime(value, "%Y-%m-%d").date()


@dataclass(frozen=True)
class PipelineConfig:
    run_date: date
    demand_points: int
    vehicle_capacity: int
    max_vehicles: int
    output_dir: Path
    s3_bucket: str | None
    s3_prefix: str
    random_seed: int | None
    distance_weight: float

    @classmethod
    def from_env(cls) -> "PipelineConfig":
        run_date = parse_run_date(os.getenv("RUN_DATE"))
        seed_value = os.getenv("RANDOM_SEED")
        return cls(
            run_date=run_date,
            demand_points=_env_int("DEMAND_POINTS", 120),
            vehicle_capacity=_env_int("VEHICLE_CAPACITY", 100),
            max_vehicles=_env_int("MAX_VEHICLES", 200),
            output_dir=Path(os.getenv("OUTPUT_DIR", "data/runs")),
            s3_bucket=os.getenv("S3_BUCKET") or None,
            s3_prefix=os.getenv("S3_PREFIX", "or-fleet"),
            random_seed=int(seed_value) if seed_value else None,
            distance_weight=_env_float("DISTANCE_WEIGHT", 0.0001),
        )
