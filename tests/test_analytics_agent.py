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
