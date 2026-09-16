import polars as pl

from pipelines.common.storage import load_dim, write_dim_delta

KEY = ["platform", "market_id"]


def _dim(rows):
    return pl.DataFrame(
        rows, schema={"platform": pl.Utf8, "market_id": pl.Utf8, "question": pl.Utf8}, orient="row"
    )


def test_dim_delta_writes_only_new_or_changed_rows(tmp_path):
    d = tmp_path / "dim_markets"
    day1 = _dim([("pm", "1", "a"), ("pm", "2", "b")])
    assert write_dim_delta(day1, d, KEY, "t1", "2026-09-15", "0600") == 2

    # nothing changed -> no file written
    assert write_dim_delta(day1, d, KEY, "t2", "2026-09-15", "1800") == 0
    assert len(list(d.glob("*/*.parquet"))) == 1

    # one changed, one new, one unchanged
    day2 = _dim([("pm", "1", "a"), ("pm", "2", "b2"), ("pm", "3", "c")])
    assert write_dim_delta(day2, d, KEY, "t3", "2026-09-16", "0600") == 2

    cur = load_dim(d, KEY).sort("market_id")
    assert cur["market_id"].to_list() == ["1", "2", "3"]
    assert cur["question"].to_list() == ["a", "b2", "c"]
    assert cur["first_seen"].to_list() == ["t1", "t1", "t3"]
    assert cur["valid_from"].to_list() == ["t1", "t3", "t3"]


def test_dim_delta_treats_null_vs_value_as_change(tmp_path):
    d = tmp_path / "dim_markets"
    write_dim_delta(_dim([("pm", "1", None)]), d, KEY, "t1", "2026-09-15", "0600")
    assert write_dim_delta(_dim([("pm", "1", "now set")]), d, KEY, "t2", "2026-09-15", "1800") == 1
    assert load_dim(d, KEY)["question"].to_list() == ["now set"]
