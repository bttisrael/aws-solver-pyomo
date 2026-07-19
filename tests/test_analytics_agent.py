from __future__ import annotations

import json

import pytest

from or_aws_fleet import analytics_agent


def test_catalog_exposes_only_curated_read_only_datasets():
    catalog = json.loads(analytics_agent.query_project_data("catalog"))

    assert set(catalog) == {
        "daily_demand",
        "routes",
        "vehicles",
        "latest_optimization",
        "forecast",
    }
    assert "sql" not in catalog


def test_daily_demand_uses_parameterized_date(monkeypatch):
    captured = {}

    def fake_query(sql, parameters=()):
        captured["sql"] = sql
        captured["parameters"] = parameters
        return [{"date": "2026-07-19", "units": 1200}]

    monkeypatch.setattr(analytics_agent, "_query", fake_query)

    result = json.loads(analytics_agent.query_project_data("daily_demand", "2026-07-19"))

    assert result[0]["units"] == 1200
    assert "WHERE date = %s" in captured["sql"]
    assert captured["parameters"][0].isoformat() == "2026-07-19"


def test_question_validation_happens_before_budget_reservation(monkeypatch):
    reserved = False

    def fake_reserve():
        nonlocal reserved
        reserved = True

    monkeypatch.setattr(analytics_agent, "reserve_daily_budget", fake_reserve)

    with pytest.raises(ValueError, match="Enter a question"):
        analytics_agent.run_analytics_agent("   ")
    with pytest.raises(ValueError, match="800 characters"):
        analytics_agent.run_analytics_agent("x" * 801)

    assert reserved is False


def test_usage_metric_aliases_are_supported():
    class Metrics:
        input_tokens = 123
        output_tokens = 45

    assert analytics_agent._usage_value(Metrics(), "prompt_tokens", "input_tokens") == 123
    assert analytics_agent._usage_value(Metrics(), "completion_tokens", "output_tokens") == 45


def test_chart_definition_is_built_only_from_approved_fields(monkeypatch):
    monkeypatch.setattr(
        analytics_agent,
        "query_project_data",
        lambda dataset, programming_date="": json.dumps(
            [{"forecast_date": "2026-07-20", "p50_units": 1250}]
        ),
    )

    chart = analytics_agent.build_project_chart(
        "forecast", "line", "forecast_date", "p50_units", "21-day demand forecast"
    )

    assert chart.dataset == "forecast"
    assert chart.chart_type == "line"
    assert chart.data[0]["p50_units"] == 1250

    with pytest.raises(ValueError, match="Unsupported numeric y field"):
        analytics_agent.build_project_chart(
            "forecast", "bar", "forecast_date", "secret_column", "Unsafe chart"
        )


def test_chart_definition_rejects_catalog_and_empty_results(monkeypatch):
    with pytest.raises(ValueError, match="approved analytical datasets"):
        analytics_agent.build_project_chart("catalog", "bar", "dataset", "rows", "Catalog")

    monkeypatch.setattr(analytics_agent, "query_project_data", lambda *args: "[]")
    with pytest.raises(ValueError, match="No data"):
        analytics_agent.build_project_chart(
            "vehicles", "bar", "vehicle_type", "vehicle_capacity_kg", "Capacity"
        )
