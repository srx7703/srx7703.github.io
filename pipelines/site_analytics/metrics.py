"""Product metrics for the site, computed with DuckDB SQL from a Mixpanel raw export (JSONL).

The definitions are the ones in docs/analytics/TRACKING_PLAN.md: activation funnel, engagement, weekly retention,
dashboard feature adoption and high-intent visitors. Inputs are pseudonymous events and stay in data/private/
(gitignored); only the aggregated tables returned here may ever be published.

    python -m pipelines.site_analytics.metrics data/private/site_analytics/events.jsonl
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

DAY = 86_400
FUNNEL_STEPS = [
    "Visited the site",
    "Opened a project page",
    "Interacted with a chart",
    "Downloaded data or clicked through",
]

_SCHEMA = {
    "event": pl.Utf8,
    "distinct_id": pl.Utf8,
    "ts": pl.Int64,
    "page_type": pl.Utf8,
    "project_slug": pl.Utf8,
    "link_type": pl.Utf8,
    "dashboard_group": pl.Utf8,
    "depth": pl.Int64,
}


def events_from_jsonl(path: str | Path) -> pl.DataFrame:
    """Flatten Mixpanel export lines ({"event", "properties": {...}}) into one row per event."""
    rows: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        p = e.get("properties", {})
        rows.append({
            "event": e["event"],
            "distinct_id": str(p.get("distinct_id")),
            "ts": int(p["time"]),
            "page_type": p.get("page_type"),
            "project_slug": p.get("project_slug"),
            "link_type": p.get("link_type"),
            "dashboard_group": p.get("dashboard_group"),
            "depth": p.get("depth"),
        })
    return pl.DataFrame(rows, schema=_SCHEMA)


def _con(events: pl.DataFrame) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.register("ev", events.to_arrow())
    return con


def funnel(events: pl.DataFrame, window_s: int = DAY) -> pl.DataFrame:
    """Visitors reaching each step in order, every step within `window_s` of the visitor's first page view."""
    sql = f"""
    WITH s1 AS (
        SELECT distinct_id, MIN(ts) AS t1 FROM ev WHERE event = 'Page Viewed' GROUP BY 1),
    s2 AS (
        SELECT s1.distinct_id, s1.t1, MIN(e.ts) AS t2 FROM s1 JOIN ev e
          ON e.distinct_id = s1.distinct_id AND e.event = 'Page Viewed' AND e.page_type = 'project'
         AND e.ts BETWEEN s1.t1 AND s1.t1 + {window_s}
        GROUP BY 1, 2),
    s3 AS (
        SELECT s2.distinct_id, s2.t1, MIN(e.ts) AS t3 FROM s2 JOIN ev e
          ON e.distinct_id = s2.distinct_id AND e.event = 'Chart Interacted'
         AND e.ts BETWEEN s2.t2 AND s2.t1 + {window_s}
        GROUP BY 1, 2),
    s4 AS (
        SELECT DISTINCT s3.distinct_id FROM s3 JOIN ev e
          ON e.distinct_id = s3.distinct_id AND e.event IN ('CSV Downloaded', 'Outbound Link Clicked')
         AND e.ts BETWEEN s3.t3 AND s3.t1 + {window_s})
    SELECT (SELECT COUNT(*) FROM s1) AS s1, (SELECT COUNT(*) FROM s2) AS s2,
           (SELECT COUNT(*) FROM s3) AS s3, (SELECT COUNT(*) FROM s4) AS s4
    """
    counts = _con(events).execute(sql).fetchone() or (0, 0, 0, 0)
    first = counts[0] or 0
    pairs = zip(counts, counts[1:], strict=False)
    return pl.DataFrame({
        "step": FUNNEL_STEPS,
        "visitors": list(counts),
        "share_of_first_step": [round(c / first, 4) if first else None for c in counts],
        "conversion_from_previous": [None] + [round(b / a, 4) if a else None for a, b in pairs],
    })


def engagement(events: pl.DataFrame) -> pl.DataFrame:
    """Per project page: share of page views with 30 s of visible time, and share read to 75 % of the page.

    Both events fire at most once per page view, so their counts divided by page views are shares of page views.
    """
    sql = """
    WITH c AS (
        SELECT project_slug,
               COUNT(*) FILTER (WHERE event = 'Page Viewed') AS page_views,
               COUNT(*) FILTER (WHERE event = 'Page Engaged') AS engaged,
               COUNT(*) FILTER (WHERE event = 'Scroll Depth Reached' AND depth = 75) AS read_75
        FROM ev WHERE page_type = 'project' GROUP BY 1)
    SELECT project_slug, page_views,
           ROUND(engaged / NULLIF(page_views, 0), 4) AS engaged_share,
           ROUND(read_75 / NULLIF(page_views, 0), 4) AS read_75_share
    FROM c ORDER BY page_views DESC, project_slug
    """
    return _con(events).execute(sql).pl()


def weekly_retention(events: pl.DataFrame) -> pl.DataFrame:
    """Cohorts by the week of a visitor's first page view; `retained_share` is the cohort share active k weeks later."""
    sql = """
    WITH pv AS (
        SELECT distinct_id, date_trunc('week', to_timestamp(ts)) AS wk FROM ev WHERE event = 'Page Viewed'),
    firsts AS (SELECT distinct_id, MIN(wk) AS cohort FROM pv GROUP BY 1),
    act AS (
        SELECT DISTINCT pv.distinct_id, f.cohort, CAST(date_diff('week', f.cohort, pv.wk) AS INTEGER) AS week_k
        FROM pv JOIN firsts f USING (distinct_id))
    SELECT CAST(cohort AS DATE) AS cohort_week, week_k, COUNT(*) AS visitors,
           ROUND(COUNT(*) / FIRST_VALUE(COUNT(*)) OVER (PARTITION BY cohort ORDER BY week_k), 4) AS retained_share
    FROM act GROUP BY cohort, week_k ORDER BY cohort, week_k
    """
    return _con(events).execute(sql).pl()


def dashboard_adoption(events: pl.DataFrame) -> pl.DataFrame:
    """Per project page: share of visitor-days on it that include at least one dashboard filter change.

    Pages without dashboards simply show 0; read the rate only for pages that have controls.
    """
    sql = """
    WITH views AS (
        SELECT DISTINCT distinct_id, project_slug, CAST(to_timestamp(ts) AS DATE) AS d
        FROM ev WHERE event = 'Page Viewed' AND page_type = 'project'),
    uses AS (
        SELECT DISTINCT distinct_id, project_slug, CAST(to_timestamp(ts) AS DATE) AS d
        FROM ev WHERE event = 'Dashboard Filter Changed')
    SELECT v.project_slug, COUNT(*) AS visitor_days, COUNT(u.distinct_id) AS with_filter_change,
           ROUND(COUNT(u.distinct_id) / COUNT(*), 4) AS adoption_rate
    FROM views v LEFT JOIN uses u USING (distinct_id, project_slug, d)
    GROUP BY 1
    ORDER BY adoption_rate DESC, visitor_days DESC
    """
    return _con(events).execute(sql).pl()


def high_intent_visitors(events: pl.DataFrame) -> pl.DataFrame:
    """Visitors per calendar week who opened at least 2 distinct project pages and showed an intent action that week.

    Intent = Chart Interacted, CSV Downloaded, or Outbound Link Clicked to LinkedIn or GitHub. This is the
    product-qualified-lead idea from product-led growth, applied to a portfolio site.
    """
    sql = """
    WITH wk AS (
        SELECT *, CAST(date_trunc('week', to_timestamp(ts)) AS DATE) AS week FROM ev),
    breadth AS (
        SELECT distinct_id, week, COUNT(DISTINCT project_slug) AS projects
        FROM wk WHERE event = 'Page Viewed' AND page_type = 'project' GROUP BY 1, 2),
    intent AS (
        SELECT DISTINCT distinct_id, week FROM wk
        WHERE event IN ('Chart Interacted', 'CSV Downloaded')
           OR (event = 'Outbound Link Clicked' AND link_type IN ('linkedin', 'github')))
    SELECT b.week, COUNT(*) AS high_intent_visitors
    FROM breadth b JOIN intent i USING (distinct_id, week)
    WHERE b.projects >= 2
    GROUP BY 1 ORDER BY 1
    """
    return _con(events).execute(sql).pl()


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    events = events_from_jsonl(argv[0])
    pl.Config.set_tbl_rows(50)
    print(f"{events.height} events from {events['distinct_id'].n_unique()} visitors\n")
    tables = (
        ("Activation funnel", funnel(events)),
        ("Engagement", engagement(events)),
        ("Weekly retention", weekly_retention(events)),
        ("Dashboard adoption", dashboard_adoption(events)),
        ("High-intent visitors", high_intent_visitors(events)),
    )
    for name, table in tables:
        print(f"{name}\n{table}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
