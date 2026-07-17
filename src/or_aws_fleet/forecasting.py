from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd


FORECAST_HORIZON_DAYS = 21


@dataclass(frozen=True)
class ForecastMetrics:
    wape: float
    mase: float
    bias: float
    interval_coverage: float


@dataclass(frozen=True)
class RetrainingDecision:
    should_retrain: bool
    reasons: tuple[str, ...]


def _quantile(values: pd.Series, probability: float) -> float:
    return float(values.quantile(probability)) if len(values) else 0.0


def seasonal_naive_forecast(
    history: pd.DataFrame,
    run_date: date,
    horizon_days: int = FORECAST_HORIZON_DAYS,
) -> pd.DataFrame:
    """Create route/SKU forecasts using recent observations from the same weekday.

    This is the inexpensive champion baseline. AutoML challengers must beat it before
    promotion; daily forecast generation therefore never depends on a training job.
    """
    required = {"date", "origin", "destiny", "cod_material", "units"}
    missing = required.difference(history.columns)
    if missing:
        raise ValueError(f"Forecast history is missing columns: {sorted(missing)}")
    if history.empty:
        raise ValueError("At least one historical demand observation is required.")

    frame = history.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame["units"] = pd.to_numeric(frame["units"], errors="coerce").fillna(0).clip(lower=0)
    frame["weekday"] = frame["date"].dt.weekday
    keys = ["origin", "destiny", "cod_material"]
    # Synthetic orders are sparse across the full SKU/route cartesian product.
    # Plan only the series present in the newest operational snapshot; using every
    # combination observed in 180 days would create a large, stale forecast matrix.
    latest_observation_date = frame["date"].max()
    active_keys = frame.loc[frame["date"] == latest_observation_date, keys].drop_duplicates()
    frame = frame.merge(active_keys, on=keys, how="inner")
    latest_attributes = [
        column
        for column in ("qty_by_box", "qty_by_pallet", "material_weight")
        if column in frame.columns
    ]
    attributes = (
        frame.sort_values("date").groupby(keys, as_index=False).tail(1)[keys + latest_attributes]
    )

    rows: list[dict] = []
    for forecast_date in (run_date + timedelta(days=offset) for offset in range(1, horizon_days + 1)):
        weekday_history = frame.loc[frame["weekday"] == forecast_date.weekday()]
        grouped = weekday_history.groupby(keys, sort=False)["units"]
        for series_key, values in grouped:
            recent = values.tail(8)
            p50 = _quantile(recent, 0.50)
            rows.append(
                {
                    "forecast_run_date": run_date,
                    "forecast_date": forecast_date,
                    **dict(zip(keys, series_key, strict=True)),
                    "p10_units": max(0, int(round(_quantile(recent, 0.10)))),
                    "p50_units": max(0, int(round(p50))),
                    "p90_units": max(0, int(round(_quantile(recent, 0.90)))),
                    "model_version": "seasonal-naive-v1",
                }
            )
    result = pd.DataFrame(rows)
    return result.merge(attributes, on=keys, how="left")


def calculate_metrics(actual: pd.Series, predicted: pd.Series, lower: pd.Series, upper: pd.Series) -> ForecastMetrics:
    actual_values = np.asarray(actual, dtype=float)
    predicted_values = np.asarray(predicted, dtype=float)
    error = actual_values - predicted_values
    actual_total = max(float(np.abs(actual_values).sum()), 1.0)
    wape = float(np.abs(error).sum() / actual_total)
    bias = float(error.sum() / actual_total)
    naive_scale = float(np.abs(np.diff(actual_values)).mean()) if len(actual_values) > 1 else 0.0
    mase = float(np.abs(error).mean() / naive_scale) if naive_scale > 0 else wape
    coverage = float(
        np.mean((actual_values >= np.asarray(lower, dtype=float)) & (actual_values <= np.asarray(upper, dtype=float)))
    )
    return ForecastMetrics(wape=wape, mase=mase, bias=bias, interval_coverage=coverage)


def retraining_decision(
    metrics: ForecastMetrics,
    consecutive_failures: int,
    days_since_training: int,
) -> RetrainingDecision:
    reasons: list[str] = []
    if metrics.wape > 0.20:
        reasons.append("WAPE > 20%")
    if metrics.mase > 1.0:
        reasons.append("MASE > 1.0")
    if abs(metrics.bias) > 0.07:
        reasons.append("absolute bias > 7%")
    if not 0.70 <= metrics.interval_coverage <= 0.90:
        reasons.append("prediction interval coverage outside 70%-90%")
    should_retrain = bool(reasons) and consecutive_failures >= 3 and days_since_training >= 7
    return RetrainingDecision(should_retrain=should_retrain, reasons=tuple(reasons))
