import json
import subprocess

from pipelines.servers import config as cfg
from pipelines.servers.publish import idc_odm, public_rows, registered_forecasts


def test_registered_forecast_table_parses_all_eight_rows():
    rows = registered_forecasts()
    assert [r["code"] for r in rows] == ["2382", "6669", "3231", "2356", "2317", "2376", "2357", "3706"]
    assert all(r["lo80_k"] < r["point_k"] < r["hi80_k"] for r in rows)


def test_prereg_files_are_append_only_against_the_registration_commit():
    # The registration commit is the public record; any later change must be an appended amendment.
    for p in (cfg.PREREG_RULES, cfg.PREREG_FORECASTS):
        old = subprocess.run(["git", "show", f"9988231d:{p.relative_to(cfg.REPO_ROOT)}"], capture_output=True,
                             text=True, cwd=cfg.REPO_ROOT)
        if old.returncode != 0:   # shallow clone without the commit: nothing to compare
            continue
        assert p.read_text(encoding="utf-8").startswith(old.stdout.split("## Amendments")[0])


def test_public_rows_drop_unsourced_low_and_working_notes():
    rows = [{"source_url": "u", "confidence": "high", "curator_notes": "x", "v": 1},
            {"source_url": "", "confidence": "high"}, {"source_url": "u", "confidence": "low"}]
    assert public_rows(rows) == [{"source_url": "u", "confidence": "high", "v": 1}]


def test_facts_never_carry_curator_notes():
    if cfg.FACTS_PATH.exists():
        assert "curator_notes" not in cfg.FACTS_PATH.read_text()


def test_idc_odm_parses_share_and_year_ago():
    rows = [{"entity": "ODM Direct (aggregate)", "period": "2Q26", "publish_date": "2026-09-10", "source_url": "u",
             "value": "89,660.3 (53.9%); 2Q25: 66,328.4 (60.6%); YoY +35.2%"}]
    r = idc_odm(rows)[0]
    assert (r["share"], r["share_year_ago"]) == (0.539, 0.606)


def test_q5_snapshot_is_frozen():
    import polars as pl
    snap = pl.read_parquet(cfg.SNAP / "q5_valuation_2026-11-09.parquet")
    assert snap.height == 25 and set(snap["price_as_of"].str.slice(0, 10)) == {"2026-11-09"}
    assert json.loads(json.dumps(snap.columns))  # columns are plain strings
