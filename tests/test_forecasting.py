from datetime import date, timedelta

import pandas as pd

from or_aws_fleet.forecasting import (
    ForecastMetrics,
    calculate_metrics,
    retraining_decision,
    seasonal_naive_forecast,
)


def history_frame() -> pd.DataFrame:
    start = date(2026, 1, 1)
    return pd.DataFrame(
        [
            {
                "date": start + timedelta(days=offset),
                "origin": "FAC_01",
                "destiny": "DC_01",
                "cod_material": "MAT_001",
                "units": 100 + 10 * (offset % 7),
                "qty_by_box": 12,
                "qty_by_pallet": 40,
                "material_weight": 350,
            }
            for offset in range(140)
        ]
    )


def test_seasonal_forecast_has_exactly_21_future_dates_and_quantiles() -> None:
    run_date = date(2026, 5, 20)
    result = seasonal_naive_forecast(history_frame(), run_date)
    assert result["forecast_date"].nunique() == 21
    assert result["forecast_date"].min() == run_date + timedelta(days=1)
    assert result["forecast_date"].max() == run_date + timedelta(days=21)
    assert (result["p10_units"] <= result["p50_units"]).all()
    assert (result["p50_units"] <= result["p90_units"]).all()


def test_metrics_and_retraining_require_three_failures_and_cooldown() -> None:
    metrics = calculate_metrics(
        pd.Series([100, 100, 100]), pd.Series([60, 60, 60]),
        pd.Series([50, 50, 50]), pd.Series([70, 70, 70]),
    )
    assert metrics.wape == 0.4
    assert not retraining_decision(metrics, consecutive_failures=2, days_since_training=30).should_retrain
    assert not retraining_decision(metrics, consecutive_failures=3, days_since_training=6).should_retrain
    assert retraining_decision(metrics, consecutive_failures=3, days_since_training=7).should_retrain


def test_healthy_metrics_do_not_trigger_retraining() -> None:
    metrics = ForecastMetrics(wape=0.10, mase=0.7, bias=0.01, interval_coverage=0.80)
    assert not retraining_decision(metrics, consecutive_failures=10, days_since_training=30).should_retrain
