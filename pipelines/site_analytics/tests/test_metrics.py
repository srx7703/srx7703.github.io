"""Metric definitions from docs/analytics/TRACKING_PLAN.md, checked on a small synthetic event log."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import polars as pl

from pipelines.site_analytics.metrics import (
    dashboard_adoption,
    engagement,
    events_from_jsonl,
    funnel,
    high_intent_visitors,
    weekly_retention,
)

MON = int(datetime(2026, 10, 19, 12, tzinfo=UTC).timestamp())  # a Monday
H = 3600
WEEK = 7 * 24 * H


def ev(event: str, who: str, ts: int, **props: object) -> dict[str, object]:
    return {"event": event, "properties": {"distinct_id": who, "time": ts, **props}}


LOG = [
    # a: full funnel in one visit, two projects, filter use, LinkedIn click; returns a week later
    ev("Page Viewed", "a", MON, page_type="home"),
    ev("Page Viewed", "a", MON + 60, page_type="project", project_slug="payments-landscape"),
    ev("Chart Interacted", "a", MON + 120, page_type="project", project_slug="payments-landscape"),
    ev("Scroll Depth Reached", "a", MON + 130, page_type="project", project_slug="payments-landscape", depth=75),
    ev("Page Engaged", "a", MON + 140, page_type="project", project_slug="payments-landscape", seconds=30),
    ev("Dashboard Filter Changed", "a", MON + 150, page_type="project", project_slug="payments-landscape", dashboard_group="g"),
    ev("Page Viewed", "a", MON + 300, page_type="project", project_slug="saas-benchmark"),
    ev("Outbound Link Clicked", "a", MON + 400, page_type="project", project_slug="saas-benchmark", link_type="linkedin"),
    ev("Page Viewed", "a", MON + WEEK, page_type="home"),
    # b: reaches a project and a chart, but never downloads or clicks out
    ev("Page Viewed", "b", MON, page_type="project", project_slug="payments-landscape"),
    ev("Chart Interacted", "b", MON + 30, page_type="project", project_slug="payments-landscape"),
    ev("Scroll Depth Reached", "b", MON + 40, page_type="project", project_slug="payments-landscape", depth=25),
    # c: home page only; a chart interaction two days later falls outside the 1-day funnel window
    ev("Page Viewed", "c", MON, page_type="home"),
    ev("Page Viewed", "c", MON + 2 * 24 * H, page_type="project", project_slug="saas-benchmark"),
]


def _events(tmp_path) -> pl.DataFrame:
    p = tmp_path / "events.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in LOG) + "\n", encoding="utf-8")
    return events_from_jsonl(p)


def test_funnel_counts_steps_in_order_within_window(tmp_path):
    f = funnel(_events(tmp_path))
    assert f["visitors"].to_list() == [3, 2, 2, 1]
    assert f["conversion_from_previous"].to_list() == [None, round(2 / 3, 4), 1.0, 0.5]


def test_engagement_is_share_of_project_page_views(tmp_path):
    rows = {r["project_slug"]: r for r in engagement(_events(tmp_path)).to_dicts()}
    pay = rows["payments-landscape"]
    assert (pay["page_views"], pay["engaged_share"], pay["read_75_share"]) == (2, 0.5, 0.5)  # b only reached 25 %
    assert rows["saas-benchmark"]["engaged_share"] == 0.0


def test_weekly_retention_counts_returning_visitors(tmp_path):
    r = weekly_retention(_events(tmp_path))
    first = r.filter(pl.col("week_k") == 0)["visitors"].to_list()
    later = r.filter(pl.col("week_k") == 1)
    assert first == [3]
    assert later["visitors"].to_list() == [1]
    assert later["retained_share"].to_list() == [round(1 / 3, 4)]


def test_dashboard_adoption_is_share_of_visitor_days(tmp_path):
    a = dashboard_adoption(_events(tmp_path))
    row = a.filter(pl.col("project_slug") == "payments-landscape").to_dicts()[0]
    assert (row["visitor_days"], row["with_filter_change"], row["adoption_rate"]) == (2, 1, 0.5)


def test_high_intent_needs_two_projects_and_an_intent_action(tmp_path):
    h = high_intent_visitors(_events(tmp_path))
    assert h["high_intent_visitors"].to_list() == [1]  # only visitor a
