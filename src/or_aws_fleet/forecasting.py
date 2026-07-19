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


@dataclass(frozen=True)
class AutoMLForecastResult:
    forecast: pd.DataFrame
    model_version: str
    validation_wape: float
    baseline_wape: float
    champion: AutoMLChampion


@dataclass
class AutoMLChampion:
    model: object
    model_version: str
    lower_factor: float
    upper_factor: float
    validation_wape: float
    baseline_wape: float
    trained_on: date


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


def _daily_features(day: pd.Timestamp, values: list[float], start: pd.Timestamp) -> list[float]:
    """Build leakage-free calendar, lag, and rolling features for one forecast day."""
    day_of_year = day.dayofyear
    weekday = day.weekday()
    return [
        float((day - start).days),
        float(np.sin(2 * np.pi * weekday / 7)),
        float(np.cos(2 * np.pi * weekday / 7)),
        float(np.sin(2 * np.pi * day_of_year / 365.25)),
        float(np.cos(2 * np.pi * day_of_year / 365.25)),
        float(values[-1]),
        float(values[-7]),
        float(values[-14]),
        float(values[-21]),
        float(values[-28]),
        float(np.mean(values[-7:])),
        float(np.mean(values[-14:])),
        float(np.mean(values[-28:])),
        float(np.std(values[-7:])),
        float(np.std(values[-28:])),
    ]


def _supervised_daily_totals(daily: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    values = daily.astype(float).tolist()
    dates = list(pd.to_datetime(daily.index))
    features = [
        _daily_features(dates[index], values[:index], dates[0])
        for index in range(28, len(values))
    ]
    return np.asarray(features, dtype=float), np.asarray(values[28:], dtype=float)


def _recursive_daily_forecast(model, history: pd.Series, future_dates: list[pd.Timestamp]) -> np.ndarray:
    values = history.astype(float).tolist()
    start = pd.Timestamp(history.index[0])
    predictions: list[float] = []
    for forecast_date in future_dates:
        features = np.asarray([_daily_features(forecast_date, values, start)], dtype=float)
        prediction = max(float(model.predict(features)[0]), 0.0)
        predictions.append(prediction)
        values.append(prediction)
    return np.asarray(predictions, dtype=float)


def _allocate_total(base: pd.Series, total: float) -> np.ndarray:
    weights = pd.to_numeric(base, errors="coerce").fillna(0).clip(lower=0).to_numpy(float)
    weights = weights / weights.sum() if weights.sum() > 0 else np.full(len(weights), 1 / len(weights))
    raw = weights * max(total, 0.0)
    allocated = np.floor(raw).astype(int)
    remainder = max(int(round(total)) - int(allocated.sum()), 0)
    if remainder:
        order = np.argsort(-(raw - allocated))
        allocated[order[:remainder]] += 1
    return allocated


def automl_forecast(
    history: pd.DataFrame,
    run_date: date,
    horizon_days: int = FORECAST_HORIZON_DAYS,
    champion: AutoMLChampion | None = None,
) -> AutoMLForecastResult:
    """Train and select a real ML champion, then produce route/SKU P10/P50/P90 plans.

    Candidate models are evaluated on a recursive 21-day holdout. The best WAPE
    model is refit on all available history. Its daily business forecast is
    disaggregated to active route/SKU series using same-weekday demand shares.
    """
    from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor
    from sklearn.ensemble import RandomForestRegressor

    baseline = seasonal_naive_forecast(history, run_date, horizon_days)
    frame = history.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame["units"] = pd.to_numeric(frame["units"], errors="coerce").fillna(0).clip(lower=0)
    daily = frame.groupby("date")["units"].sum().sort_index().asfreq("D", fill_value=0)
    if len(daily) < 70:
        raise ValueError("AutoML forecasting requires at least 70 consecutive historical days.")

    if champion is None:
        validation_days = min(21, len(daily) - 56)
        training_daily = daily.iloc[:-validation_days]
        validation_actual = daily.iloc[-validation_days:].to_numpy(float)
        x_train, y_train = _supervised_daily_totals(training_daily)
        candidates = {
            "hist-gradient-boosting": HistGradientBoostingRegressor(
                max_iter=220, learning_rate=0.055, max_leaf_nodes=15,
                l2_regularization=1.0, random_state=271828,
            ),
            "random-forest": RandomForestRegressor(
                n_estimators=180, max_depth=10, min_samples_leaf=3, n_jobs=-1,
                random_state=271828,
            ),
            "extra-trees": ExtraTreesRegressor(
                n_estimators=180, max_depth=12, min_samples_leaf=2, n_jobs=-1,
                random_state=271828,
            ),
        }
        scored: list[tuple[float, str, object, np.ndarray]] = []
        validation_dates = list(pd.to_datetime(daily.index[-validation_days:]))
        for name, model in candidates.items():
            model.fit(x_train, y_train)
            predictions = _recursive_daily_forecast(model, training_daily, validation_dates)
            wape = float(
                np.abs(validation_actual - predictions).sum()
                / max(validation_actual.sum(), 1.0)
            )
            scored.append((wape, name, model, predictions))
        validation_wape, champion_name, model, validation_predictions = min(
            scored, key=lambda item: item[0]
        )
        weekday_baseline = training_daily.groupby(training_daily.index.weekday).mean()
        baseline_predictions = np.asarray(
            [float(weekday_baseline.loc[day.weekday()]) for day in validation_dates]
        )
        baseline_wape = float(
            np.abs(validation_actual - baseline_predictions).sum()
            / max(validation_actual.sum(), 1.0)
        )
        relative_residuals = (validation_actual - validation_predictions) / np.maximum(
            validation_predictions, 1.0
        )
        x_full, y_full = _supervised_daily_totals(daily)
        model.fit(x_full, y_full)
        champion = AutoMLChampion(
            model=model,
            model_version=f"automl-{champion_name}-{run_date:%Y%m%d}",
            lower_factor=max(
                0.35, 1.0 + float(np.quantile(relative_residuals, 0.10))
            ),
            upper_factor=max(
                1.05, 1.0 + float(np.quantile(relative_residuals, 0.90))
            ),
            validation_wape=validation_wape,
            baseline_wape=baseline_wape,
            trained_on=run_date,
        )
    future_dates = [pd.Timestamp(run_date + timedelta(days=offset)) for offset in range(1, horizon_days + 1)]
    daily_p50 = _recursive_daily_forecast(champion.model, daily, future_dates)
    result_frames: list[pd.DataFrame] = []
    for forecast_date, total_p50 in zip(future_dates, daily_p50, strict=True):
        day_frame = baseline.loc[baseline["forecast_date"] == forecast_date.date()].copy()
        if day_frame.empty:
            continue
        day_frame["p50_units"] = _allocate_total(day_frame["p50_units"], total_p50)
        day_frame["p10_units"] = _allocate_total(
            day_frame["p50_units"], total_p50 * champion.lower_factor
        )
        day_frame["p90_units"] = _allocate_total(
            day_frame["p50_units"], total_p50 * champion.upper_factor
        )
        day_frame["model_version"] = champion.model_version
        result_frames.append(day_frame)
    forecast = pd.concat(result_frames, ignore_index=True)
    return AutoMLForecastResult(
        forecast=forecast,
        model_version=champion.model_version,
        validation_wape=champion.validation_wape,
        baseline_wape=champion.baseline_wape,
        champion=champion,
    )


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


def calculate_aggregate_metrics(
    actual_total: float,
    predicted_total: float,
    lower_total: float,
    upper_total: float,
    historical_daily_totals: pd.Series,
) -> ForecastMetrics:
    """Evaluate a sparse operational forecast at the daily business-total level."""
    actual_total = max(float(actual_total), 0.0)
    predicted_total = max(float(predicted_total), 0.0)
    error = actual_total - predicted_total
    denominator = max(actual_total, 1.0)
    wape = abs(error) / denominator
    bias = error / denominator
    history = pd.to_numeric(historical_daily_totals, errors="coerce").dropna()
    naive_scale = float(history.diff().abs().dropna().mean()) if len(history) > 1 else 0.0
    mase = abs(error) / naive_scale if naive_scale > 0 else wape
    coverage = float(lower_total <= actual_total <= upper_total)
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
