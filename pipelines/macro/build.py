"""FOMC page, macro layer: the rolling policy expectation beside rates, inflation compensation, oil and
claims, and the release event study.

Usage:
    uv run python -m pipelines.macro.build              # everything
    uv run python -m pipelines.macro.build --no-events  # overlay only (no one-minute fetches)

Reads the decision-market history assembled by `pipelines.predmarkets.fomc` (backfill, archive and
snapshots, dated in New York price time), FRED and ALFRED, the release calendar, and Kalshi's release
ladders and one-minute quotes. Writes:

    data/snapshots/macro/fred_daily.parquet            FRED daily series (merged; new rows win)
    data/snapshots/macro/claims_first_print.parquet    claims as first published (merged; first print kept)
    data/snapshots/macro/ladders.json                  settled release ladders (never refetched)
    data/snapshots/macro/events/<release>_<date>.parquet   one-minute quotes around each release
    data/snapshots/macro/placebo/<date>.parquet        one-minute quotes around each placebo window
    data/marts/predmarkets/fomc_macro_daily.json       the four stacked panels
    data/marts/predmarkets/fomc_event_study.json       one row per release event
    data/marts/predmarkets/fomc_event_paths.json       mean path around releases, by surprise sign
    data/facts/fomc_macro.json

Nothing between January 2025 and September 22, 2026 is pre-registered: that history was looked at
while this was designed. The forward claims in docs/EVALUATION_PLAN.md are scored only on data from
FORWARD_FROM on.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import UTC, date, datetime, timedelta

import polars as pl

from pipelines.common.checks import check
from pipelines.common.log import setup_logging
from pipelines.common.storage import FACTS_DIR, MARTS_DIR, SNAP_DIR, utc_now, write_json, write_parquet
from pipelines.macro import config, fred
from pipelines.macro import eventstudy as es
from pipelines.macro import releases as rel
from pipelines.macro.intraday import QUOTE_COLS
from pipelines.predmarkets import archive, fomc, read
from pipelines.predmarkets.fomc import BPS, MEETINGS
from pipelines.predmarkets.kalshi import KalshiClient
from pipelines.predmarkets.polymarket import PolymarketClient
from pipelines.valuation.fx import FredClient

log = logging.getLogger("macro.build")

MACRO_DIR = SNAP_DIR / "macro"
MART_DIR = MARTS_DIR / "predmarkets"
PLATFORMS = ("polymarket", "kalshi")
FORWARD_FROM = "2026-09-24"  # the day after the claims were committed to docs/EVALUATION_PLAN.md
CARRY_DAYS = 3
OIL_MOVE = 0.05  # a "big oil day": WTI moves this much or more
GAP_BPS = 5.0  # platforms "disagree" when their two-meeting expectations differ by more than this
PLACEBO_EVERY = 2
PATH_MINUTES = range(-30, 61)


# --- small helpers ----------------------------------------------------------------------------------
def merge(new: pl.DataFrame, path, key: list[str], keep: str) -> pl.DataFrame:
    """Union with what is on disk; `keep="last"` lets this run win, `"first"` keeps stored rows."""
    df = new
    if path.exists():
        df = pl.concat([pl.read_parquet(path), new], how="vertical_relaxed").unique(
            subset=key, keep=keep, maintain_order=True
        )
    write_parquet(df.sort(key), path)
    return df


def days(start: str, end: str) -> list[str]:
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    return [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


def shift(day: str, n: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()


def corr(x: list[float], y: list[float]) -> float | None:
    n = len(x)
    if n < 10:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    return None if not sx or not sy else sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True)) / (sx * sy)


def r(x: float | None, nd: int = 3) -> float | None:
    return None if x is None else round(x, nd)


# --- FRED -------------------------------------------------------------------------------------------
def refresh_fred() -> tuple[pl.DataFrame, pl.DataFrame, list[str]]:
    since = shift(config.WINDOW_START, -45)
    new, failed = fred.daily(FredClient(), list(config.DAILY_SERIES), since)
    daily = merge(new, MACRO_DIR / "fred_daily.parquet", ["series", "date"], keep="last")
    path = MACRO_DIR / "claims_first_print.parquet"
    try:
        claims = fred.claims_first_print(fred.AlfredClient(), config.CLAIMS_SERIES, shift(config.WINDOW_START, -21))
        claims = merge(claims, path, ["series", "week_end"], keep="first")
    except Exception as exc:  # noqa: BLE001 - claims failing must not lose the other panels
        log.warning("ALFRED %s failed: %s", config.CLAIMS_SERIES, exc)
        failed.append(config.CLAIMS_SERIES)
        claims = pl.read_parquet(path) if path.exists() else pl.DataFrame(schema=fred.CLAIMS_COLS)
    return daily, claims, failed


def series_map(daily: pl.DataFrame, sid: str) -> dict[str, float]:
    return dict(daily.filter(pl.col("series") == sid).select("date", "value").iter_rows())


def value_on_or_before(m: dict[str, float], day: str, max_back: int = 7) -> float | None:
    for k in range(max_back + 1):
        v = m.get(shift(day, -k))
        if v is not None:
            return v
    return None


# --- rolling expectation ----------------------------------------------------------------------------
def rolling(prices: pl.DataFrame, grid: pl.DataFrame, start: str, end: str) -> pl.DataFrame:
    """Per platform and New York date: the expected move at the next meeting and summed over the next two.

    A meeting's expected move on a day counts only if every one of its outcome markets has a price that
    day; a missing day is filled from the previous complete day, at most CARRY_DAYS back. The next
    meeting is the first decided after the day, because the day's price is the evening price and the
    decision is announced at 14:00.
    """
    total = grid.group_by("platform", "meeting").agg(pl.len().alias("n_total"))
    per = (
        prices.group_by("platform", "meeting", "date")
        .agg(
            (pl.col("price") * pl.col("bucket").replace_strict(BPS, return_dtype=pl.Float64)).sum().alias("ebps"),
            pl.len().alias("n"),
        )
        .join(total, on=["platform", "meeting"])
        .filter(pl.col("n") == pl.col("n_total"))
    )
    lk = {(a, b, c): v for a, b, c, v in per.select("platform", "meeting", "date", "ebps").iter_rows()}
    order = sorted(MEETINGS, key=MEETINGS.get)
    rows = []
    for d in days(start, end):
        nxt = [m for m in order if MEETINGS[m] > d][: config.NEXT_K]
        for pf in PLATFORMS:
            vals = []
            for m in nxt:
                v = None
                for back in range(CARRY_DAYS + 1):
                    v = lk.get((pf, m, shift(d, -back)))
                    if v is not None:
                        break
                vals.append(v)
            rows.append(
                {
                    "date": d,
                    "platform": pf,
                    "next_meeting": nxt[0] if nxt else None,
                    "next1_bps": r(vals[0], 2) if vals and vals[0] is not None else None,
                    "next2_bps": r(sum(vals), 2) if len(vals) == config.NEXT_K and None not in vals else None,
                }
            )
    return pl.DataFrame(rows)


def overlay(roll: pl.DataFrame, daily: pl.DataFrame, claims: pl.DataFrame, start: str, end: str) -> list[dict]:
    """Long rows for the stacked panels: date, panel, series, value (and `segment` for broken lines)."""
    target = series_map(daily, "DFEDTARU")
    out: list[dict] = []
    for x in roll.filter(pl.col("next2_bps").is_not_null()).iter_rows(named=True):
        # the target moves the day after a decision, so the evening price on D sits on top of D + 1
        t = value_on_or_before(target, shift(x["date"], 1))
        if t is None:
            continue
        out.append(
            {
                "date": x["date"],
                "panel": "rate",
                "series": f"implied_{x['platform']}",
                "value": round(t + x["next2_bps"] / 100, 4),
                "segment": x["next_meeting"],
            }
        )
    for sid, panel, name in (
        ("DFEDTARU", "rate", "target"),
        ("DGS2", "rate", "dgs2"),
        ("T5YIE", "inflation", "t5yie"),
        ("T5YIFR", "inflation", "t5yifr"),
        ("DCOILWTICO", "oil", "wti"),
        ("DCOILBRENTEU", "oil", "brent"),
    ):
        for d, v in sorted(series_map(daily, sid).items()):
            if start <= d <= end:
                out.append({"date": d, "panel": panel, "series": name, "value": v, "segment": None})
    # claims: the newest week in each release, as first printed, placed on the release day
    latest = (
        claims.filter(pl.col("release_date") >= start)
        .sort("week_end")
        .group_by("release_date", maintain_order=True)
        .agg(pl.col("value").last(), pl.col("week_end").last())
        .sort("release_date")
    )
    for x in latest.iter_rows(named=True):
        out.append(
            {
                "date": x["release_date"],
                "panel": "claims",
                "series": "ic4wsa",
                "value": x["value"] / 1000,
                "segment": None,
            }
        )
    return out


# --- event study ------------------------------------------------------------------------------------
def fomc_markets(dim: pl.DataFrame) -> pl.DataFrame:
    """The decision-market grid with each market's YES token (Polymarket) and, for archived Kalshi
    markets, which endpoint serves them."""
    grid = fomc.full_grid(dim)
    toks = [dim.select("platform", "market_id", "yes_token").with_columns(pl.lit("live").alias("source"))]
    arch = fomc.ARCHIVE_DIR / "markets.parquet"
    if arch.exists():
        toks.append(pl.read_parquet(arch).select("platform", "market_id", "yes_token", "source"))
    tok = pl.concat(toks, how="vertical_relaxed").unique(subset=["platform", "market_id"], keep="first")
    return grid.join(tok, on=["platform", "market_id"], how="left")


def trim_market(m: dict) -> dict:
    keep = ("ticker", "floor_strike", "strike_type", "result", "expiration_value", "close_time")
    return {k: m.get(k) for k in keep}


def load_ladders(kc: KalshiClient) -> dict[str, list[dict]]:
    """series -> settled ladder events since the window start. Settled ladders never change, so an event
    already stored is not refetched (its historical markets cost one call each)."""
    path = MACRO_DIR / "ladders.json"
    store: dict[str, dict] = json.loads(path.read_text()) if path.exists() else {}
    for series in config.LADDERS:
        for ev in kc.iter_events(status="settled", series_ticker=series):
            et = ev["event_ticker"]
            if et in store:
                continue
            markets, source = ev.get("markets") or [], "live"
            if not markets:
                markets, source = list(kc.iter_historical_markets(event_ticker=et)), "historical"
            closes = sorted({m.get("close_time") for m in markets if m.get("close_time")})
            if not closes or closes[-1][:10] < config.WINDOW_START:
                continue
            store[et] = {
                "series": series,
                "event": et,
                "close_ts": rel.iso_epoch(closes[-1]),
                "source": source,
                "markets": [trim_market(m) for m in markets],
            }
    write_json(dict(sorted(store.items())), path)
    out: dict[str, list[dict]] = {s: [] for s in config.LADDERS}
    for ev in store.values():
        out[ev["series"]].append(ev)
    return out


def event_quotes(kc, pc, key: str, markets: pl.DataFrame, t0: int, close_ts: int, folder: str) -> pl.DataFrame:
    """Cached one-minute quotes for an event; markets missing from the cache are fetched and added."""
    path = MACRO_DIR / folder / f"{key}.parquet"
    cached = pl.read_parquet(path) if path.exists() else pl.DataFrame(schema=QUOTE_COLS)
    have = set(zip(cached["platform"].to_list(), cached["market_id"].to_list(), strict=True))
    todo = markets.filter(
        ~pl.struct("platform", "market_id").map_elements(
            lambda x: (x["platform"], x["market_id"]) in have, return_dtype=pl.Boolean
        )
    )
    if todo.height == 0:
        return cached
    keep_from = t0 - 60 * 60
    start = t0 - config.PRE_MIN * 60 - config.CARRY_HOURS * 3600
    q, failed = es.fetch_quotes(kc, pc, todo, start, close_ts, keep_from)
    out = pl.concat([cached, q], how="vertical_relaxed")
    if out.height and not failed:  # a partial fetch is used this run but retried next run
        write_parquet(out, path)
    return out


def run_events(dim: pl.DataFrame) -> dict:
    cal = rel.calendar()
    kc, pc = KalshiClient(), PolymarketClient()
    kc.http.min_interval = 0.3
    ladders = load_ladders(kc)
    mk_all = fomc_markets(dim)
    now = int(datetime.now(UTC).timestamp())
    cache_path = MACRO_DIR / "consensus.json"
    cache: dict[str, dict] = json.loads(cache_path.read_text()) if cache_path.exists() else {}

    events, unmatched, path_rows = [], [], []
    for release, spec in config.RELEASES.items():
        primary = config.PRIMARY[release]
        official = cal.filter((pl.col("release") == release) & (pl.col("date_et") >= config.WINDOW_START))
        official = official.filter(pl.col("t0") + 8 * 3600 < now)  # the 16:00 read has happened
        matched, missing = rel.match(official, ladders[primary], release)
        unmatched += missing
        for mt in matched:
            key = f"{release}_{mt['date_et']}"
            t0, day = mt["t0"], mt["date_et"]
            surprises = {}
            for series in spec["ladders"]:
                ck = f"{key}:{series}"
                if ck not in cache:
                    hits, _ = rel.match(official.filter(pl.col("t0") == t0), ladders[series], release)
                    cache[ck] = (
                        rel.consensus(kc, hits[0]["ladder"], t0) if hits else {"series": series, "missing": True}
                    )
                surprises[series] = cache[ck]
            nm = es.next_meetings(day)
            mk = mk_all.filter(pl.col("meeting").is_in(nm))
            close_ts = rel.et_epoch(day, config.CLOSE_ET)
            q = event_quotes(kc, pc, key, mk, t0, close_ts, "events")
            pre, post = t0 - config.PRE_MIN * 60, t0 + config.POST_MIN * 60
            mk1 = mk.filter(pl.col("meeting") == nm[0])
            d_post, d_close, d1 = (
                es.delta_bps(mk, q, pre, post),
                es.delta_bps(mk, q, pre, close_ts),
                es.delta_bps(mk1, q, pre, post),
            )
            prim = surprises[primary]
            events.append(
                {
                    "key": key,
                    "release": release,
                    "date": day,
                    "t0_utc": rel.utc_iso(t0),
                    "title": mt["title"],
                    "meetings": nm,
                    "primary": primary,
                    "consensus": prim.get("consensus"),
                    "actual": prim.get("actual"),
                    "surprise": prim.get("surprise"),
                    "hawkish_surprise": prim.get("hawkish_surprise"),
                    "secondary": {
                        s: {k: v.get(k) for k in ("consensus", "actual", "surprise", "hawkish_surprise")}
                        for s, v in surprises.items()
                        if s != primary
                    },
                    "n_strikes": prim.get("n_strikes"),
                    "actual_from": prim.get("actual_from"),
                    "kalshi": {
                        "d_post": (d_post["kalshi"] or {}).get("d_bps"),
                        "d_close": (d_close["kalshi"] or {}).get("d_bps"),
                        "d_post_next1": (d1["kalshi"] or {}).get("d_bps"),
                        "level_pre": (d_post["kalshi"] or {}).get("level_pre_bps"),
                        "coverage": (d_post["kalshi"] or {}).get("coverage"),
                    },
                    "polymarket": {
                        "d_post": (d_post["polymarket"] or {}).get("d_bps"),
                        "d_close": (d_close["polymarket"] or {}).get("d_bps"),
                        "d_post_next1": (d1["polymarket"] or {}).get("d_bps"),
                        "level_pre": (d_post["polymarket"] or {}).get("level_pre_bps"),
                        "coverage": (d_post["polymarket"] or {}).get("coverage"),
                    },
                }
            )
            hs = prim.get("hawkish_surprise")
            if hs is not None:  # an event without a consensus has no sign to average under
                sign = "hawkish" if hs > 0 else "dovish" if hs < 0 else "in line"
                for p in es.path(mk, q, t0, PATH_MINUTES):
                    path_rows.append({"release": release, "sign": sign, **p})
    write_json(dict(sorted(cache.items())), cache_path)

    # placebo windows (Kalshi): same clock, quiet days
    today = datetime.now(UTC).astimezone(rel.ET).date().isoformat()
    pdays = es.placebo_days(cal, config.WINDOW_START, shift(today, -1))[::PLACEBO_EVERY]
    placebo = []
    for day in pdays:
        t0 = rel.et_epoch(day, config.PLACEBO_CLOCK_ET)
        nm = es.next_meetings(day)
        mk = mk_all.filter(pl.col("meeting").is_in(nm))
        q = event_quotes(kc, pc, day, mk, t0, t0 + config.POST_MIN * 60, "placebo")
        d = es.delta_bps(mk, q, t0 - config.PRE_MIN * 60, t0 + config.POST_MIN * 60)
        placebo.append(
            {
                "date": day,
                **{f"{pf}_d_bps": (d[pf] or {}).get("d_bps") for pf in PLATFORMS},
                **{f"{pf}_coverage": (d[pf] or {}).get("coverage") for pf in PLATFORMS},
            }
        )
    return {"events": events, "unmatched": unmatched, "paths": path_rows, "placebo": placebo, "calendar": cal}


# --- statistics -------------------------------------------------------------------------------------
def path_points(paths: list[dict], minutes: tuple[int, ...] = (5, 20, 60)) -> dict:
    """Mean path on the reaction platform across all releases at a few minutes after T0, by the sign of
    the surprise."""
    out: dict = {}
    for sign in ("hawkish", "dovish"):
        rows = [p for p in paths if p["platform"] == config.REACTION_PLATFORM and p["sign"] == sign]
        point: dict = {"n": sum(1 for p in rows if p["minute"] == 0)}
        for m in minutes:
            at = [p["d_bps"] for p in rows if p["minute"] == m]
            point[f"m{m}"] = r(sum(at) / len(at), 2) if at else None
        out[sign] = point
    return out


def event_stats(events: list[dict], placebo: list[dict]) -> dict:
    """Summary statistics on the reaction platform, with the other platform as a replication."""
    main = config.REACTION_PLATFORM
    other = "kalshi" if main == "polymarket" else "polymarket"
    noise = {pf: [abs(p[f"{pf}_d_bps"]) for p in placebo if p.get(f"{pf}_d_bps") is not None] for pf in PLATFORMS}
    p90 = {pf: es.quantile(v, 0.9) for pf, v in noise.items()}
    out: dict = {
        "platform": main,
        "placebo": {
            "n": len(noise[main]),
            "median_abs_bps": r(es.quantile(noise[main], 0.5), 2),
            "p90_abs_bps": r(p90[main], 2),
            "clock_et": config.PLACEBO_CLOCK_ET,
            "by_platform": {
                pf: {"n": len(v), "median_abs_bps": r(es.quantile(v, 0.5), 2), "p90_abs_bps": r(p90[pf], 2)}
                for pf, v in noise.items()
            },
        },
        "by_release": {},
    }
    for release in [*config.RELEASES, "all"]:
        ev = [e for e in events if release in ("all", e["release"])]
        pairs = [
            (e["hawkish_surprise"], e[main]["d_post"])
            for e in ev
            if e["hawkish_surprise"] is not None and e[main]["d_post"] is not None
        ]
        pairs_other = [
            (e["hawkish_surprise"], e[other]["d_post"])
            for e in ev
            if e["hawkish_surprise"] is not None and e[other]["d_post"] is not None
        ]
        moves = [abs(e[main]["d_post"]) for e in ev if e[main]["d_post"] is not None]
        both = [
            (e[main]["d_post"], e[other]["d_post"])
            for e in ev
            if e[main]["d_post"] is not None and e[other]["d_post"] is not None
        ]
        keep = [
            (e[main]["d_post"], e[main]["d_close"])
            for e in ev
            if e[main]["d_post"] is not None and e[main]["d_close"] is not None
        ]
        fit = es.ols([s for s, _ in pairs], [d for _, d in pairs]) if release != "all" else None
        out["by_release"][release] = {
            "n_events": len(ev),
            "sign": es.sign_agreement(pairs),
            "sign_other_platform": es.sign_agreement(pairs_other),
            "median_abs_bps": r(es.quantile(moves, 0.5), 2),
            "share_above_placebo_p90": r(sum(m > p90[main] for m in moves) / len(moves), 3)
            if moves and p90[main] is not None
            else None,
            "slope": None if fit is None else {k: r(v, 3) for k, v in fit.items()},
            "platform_corr": r(corr([a for a, _ in both], [b for _, b in both])) if len(both) >= 10 else None,
            "platform_same_sign": es.sign_agreement(both),
            "close_same_sign": es.sign_agreement(keep),
        }
    return out


def overlay_stats(roll: pl.DataFrame, daily: pl.DataFrame, start: str, end: str) -> dict:
    """Descriptive, in-sample statistics for the stacked panels, and the same statistics on the forward
    sample that the pre-registered claims are scored on."""
    target = series_map(daily, "DFEDTARU")
    dgs2, wti = series_map(daily, "DGS2"), series_map(daily, "DCOILWTICO")
    pm = {x["date"]: x for x in roll.filter(pl.col("platform") == "polymarket").iter_rows(named=True)}
    kx = {x["date"]: x for x in roll.filter(pl.col("platform") == "kalshi").iter_rows(named=True)}

    def implied(x: dict | None, d: str) -> float | None:
        if not x or x["next2_bps"] is None:
            return None
        t = value_on_or_before(target, shift(d, 1))
        return None if t is None else t + x["next2_bps"] / 100

    def window(lo: str, hi: str) -> dict:
        dd = [d for d in days(lo, hi)]
        # daily changes on consecutive trading days where the next-two-meeting set did not roll over
        pairs_2y, pairs_oil, oil_big, gaps = [], [], [], []
        prev = None
        for d in dd:
            if d not in dgs2:
                continue
            if prev is not None:
                a, b = pm.get(prev), pm.get(d)
                if a and b and a["next_meeting"] == b["next_meeting"]:
                    ia, ib = implied(a, prev), implied(b, d)
                    if ia is not None and ib is not None:
                        pairs_2y.append(((ib - ia) * 100, (dgs2[d] - dgs2[prev]) * 100))
                        if d in wti and prev in wti and wti[prev]:
                            ch = wti[d] / wti[prev] - 1
                            pairs_oil.append((b["next2_bps"] - a["next2_bps"], ch))
                            if abs(ch) >= OIL_MOVE:
                                oil_big.append((ch, b["next2_bps"] - a["next2_bps"]))
            prev = d
        for d in dd:
            a, b = pm.get(d), kx.get(d)
            if a and b and a["next2_bps"] is not None and b["next2_bps"] is not None:
                gaps.append(abs(a["next2_bps"] - b["next2_bps"]))
        same = sum(1 for ch, m in oil_big if m and (ch > 0) == (m > 0))
        return {
            "from": lo,
            "to": hi,
            "n_days_2y": len(pairs_2y),
            "corr_daily_change_vs_2y": r(corr([a for a, _ in pairs_2y], [b for _, b in pairs_2y])),
            "n_days_oil": len(pairs_oil),
            "corr_daily_change_vs_wti": r(corr([a for a, _ in pairs_oil], [b for _, b in pairs_oil])),
            "wti_5pct_days": len(oil_big),
            "wti_5pct_same_direction": same,
            "n_days_both_platforms": len(gaps),
            "mean_abs_platform_gap_bps": r(sum(gaps) / len(gaps), 2) if gaps else None,
            "share_days_gap_over_5bps": r(sum(g > GAP_BPS for g in gaps) / len(gaps), 3) if gaps else None,
        }

    coverage = {
        pf: {
            "days": int(roll.filter(pl.col("platform") == pf).height),
            "with_next2": int(roll.filter((pl.col("platform") == pf) & pl.col("next2_bps").is_not_null()).height),
            "first_next2": roll.filter((pl.col("platform") == pf) & pl.col("next2_bps").is_not_null())["date"].min(),
        }
        for pf in PLATFORMS
    }

    def ends(m: dict[str, float]) -> list[float | None]:
        ds = sorted(d for d in m if start <= d <= end)
        return [m[ds[0]], m[ds[-1]]] if ds else [None, None]

    implied_by_day = {d: implied(x, d) for d, x in pm.items()}
    low = min(((v, d) for d, v in implied_by_day.items() if v is not None and start <= d <= end), default=(None, None))
    return {
        "coverage": coverage,
        "oil_move": OIL_MOVE,
        "gap_bps": GAP_BPS,
        "endpoints": {
            "implied_polymarket": [r(v, 2) for v in ends({d: v for d, v in implied_by_day.items() if v is not None})],
            "implied_polymarket_low": {"value": r(low[0], 2), "date": low[1]},
            "target": ends(target),
            "dgs2": ends(dgs2),
            "wti": ends(wti),
        },
        "in_sample": window(start, shift(FORWARD_FROM, -1)),
        "forward": window(FORWARD_FROM, end) if end >= FORWARD_FROM else None,
    }


# --- publish ----------------------------------------------------------------------------------------
def build(with_events: bool = True) -> dict:
    daily, claims, fred_failed = refresh_fred()
    dim = read.dim("fomc")
    grid = fomc.full_grid(dim)
    prices = fomc.daily_prices(grid)
    start = config.WINDOW_START
    end = prices["date"].max()
    roll = rolling(prices, grid, start, end)
    panels = overlay(roll, daily, claims, start, end)
    MART_DIR.mkdir(parents=True, exist_ok=True)
    write_json(panels, MART_DIR / "fomc_macro_daily.json")
    ostats = overlay_stats(roll, daily, start, end)

    ev_out = None
    if with_events:
        ev_out = run_events(dim)
        evs = ev_out["events"]
        write_json(evs, MART_DIR / "fomc_event_study.json")
        agg = pl.DataFrame()
        if ev_out["paths"]:
            paths = pl.DataFrame(ev_out["paths"])
            paths = pl.concat([paths, paths.with_columns(pl.lit("all").alias("release"))])
            agg = (
                paths.group_by("release", "sign", "platform", "minute")
                .agg(pl.col("d_bps").mean().round(2).alias("d_bps"), pl.len().alias("n"))
                .sort("release", "sign", "platform", "minute")
            )
        write_json(agg.to_dicts(), MART_DIR / "fomc_event_paths.json")

    facts_path = FACTS_DIR / "fomc_macro.json"
    prev = json.loads(facts_path.read_text()) if facts_path.exists() else {}
    last = {sid: daily.filter(pl.col("series") == sid)["date"].max() for sid in config.DAILY_SERIES}
    cal = rel.calendar()
    in_window = cal.filter((pl.col("date_et") >= start) & (pl.col("date_et") <= end))
    checks = [
        check(
            "FRED series",
            not fred_failed,
            "all fetched" if not fred_failed else f"failed this run (last good data kept): {', '.join(fred_failed)}",
            warn=True,
        ),
        check(
            "Market data current",
            all(v is not None and v >= shift(end, -7) for v in last.values()),
            ", ".join(f"{k} {v}" for k, v in last.items()),
            warn=True,
        ),
        check(
            "Claims as first printed",
            claims.height >= 80,
            f"{claims.height} weekly first prints from ALFRED vintages, each dated by its release day",
        ),
        check(
            "Rolling expectation coverage",
            ostats["coverage"]["polymarket"]["with_next2"] >= 0.95 * ostats["coverage"]["polymarket"]["days"],
            "; ".join(f"{pf}: {v['with_next2']}/{v['days']} days" for pf, v in ostats["coverage"].items()),
            warn=True,
        ),
        check(
            "Release calendar ahead",
            cal["date_et"].max() >= shift(end, 45),
            f"official calendar runs to {cal['date_et'].max()}; extend it from the BLS and BEA schedules each December",
            warn=True,
        ),
    ]
    facts: dict = {
        "project": "fomc-markets",
        "generated_at": utc_now().isoformat(timespec="seconds"),
        "window": {"from": start, "to": end},
        "forward_from": FORWARD_FROM,
        "series": {sid: {"label": lbl, "last": last[sid]} for sid, lbl in config.DAILY_SERIES.items()},
        "claims": {
            "series": config.CLAIMS_SERIES,
            "n_first_prints": int(claims.height),
            "last_release": claims["release_date"].max() if claims.height else None,
        },
        "overlay": ostats,
        "decisions": [{"meeting": m, "date": d} for m, d in sorted(MEETINGS.items()) if start <= d <= end],
        "releases_on_axis": [
            {"release": x["release"], "date": x["date_et"]}
            for x in in_window.filter(pl.col("release").is_in(["cpi", "jobs"])).iter_rows(named=True)
        ],
        "archive_from": archive.ARCHIVE_FROM,
        "event_study": None,
        "sources": [
            {"name": "FRED (St. Louis Fed)", "url": "https://fred.stlouisfed.org/"},
            {"name": "ALFRED vintages (St. Louis Fed)", "url": "https://alfred.stlouisfed.org/"},
            {"name": "BLS release schedule", "url": "https://www.bls.gov/schedule/2026/home.htm"},
            {"name": "BEA release schedule", "url": "https://www.bea.gov/news/schedule"},
            {"name": "Kalshi public trade API v2", "url": "https://docs.kalshi.com/"},
            {"name": "Polymarket CLOB API", "url": "https://docs.polymarket.com/"},
        ],
    }
    if ev_out is not None:
        evs = ev_out["events"]
        stats = event_stats(evs, ev_out["placebo"])
        stats["mean_path"] = path_points(ev_out["paths"])
        n_off = {
            k: int(
                ev_out["calendar"]
                .filter((pl.col("release") == k) & (pl.col("date_et") >= start) & (pl.col("date_et") <= end))
                .height
            )
            for k in config.RELEASES
        }

        def complete(e: dict, pf: str) -> bool:
            cov = e[pf]["coverage"]
            return bool(cov) and cov.split("/")[0] == cov.split("/")[1]

        full_cov = {pf: sum(complete(e, pf) for e in evs) for pf in PLATFORMS}
        facts["event_study"] = {
            "platform": config.REACTION_PLATFORM,
            "complete_quotes": full_cov,
            "window_min": {"pre": config.PRE_MIN, "post": config.POST_MIN, "close_et": config.CLOSE_ET},
            "next_k": config.NEXT_K,
            "primary": config.PRIMARY,
            "ladders": {k: v["label"] for k, v in config.LADDERS.items()},
            "n_events": len(evs),
            "official_releases": n_off,
            "unmatched": ev_out["unmatched"],
            "stats": stats,
        }
        checks += [
            check(
                "Release matching",
                len(evs) >= 0.8 * max(1, sum(n_off.values())),
                f"{len(evs)} of {sum(n_off.values())} releases matched to a Kalshi ladder still trading ten minutes "
                "before; dropped: "
                + ("; ".join(f"{u['release']} {u['date_et']} ({u['reason']})" for u in ev_out["unmatched"]) or "none"),
                warn=True,
            ),
            check(
                "Consensus and print",
                all(e["consensus"] is not None and e["actual"] is not None for e in evs),
                f"{sum(e['surprise'] is not None for e in evs)}/{len(evs)} events have a ladder mean and a print",
                warn=True,
            ),
            check(
                "Quote coverage",
                full_cov[config.REACTION_PLATFORM] >= 0.9 * max(1, len(evs)),
                "events with a minute price for every outcome of the next two meetings: "
                + ", ".join(f"{pf} {full_cov[pf]}/{len(evs)}" for pf in PLATFORMS)
                + f" (statistics use {config.REACTION_PLATFORM})",
                warn=True,
            ),
            check(
                "Placebo windows",
                stats["placebo"]["n"] >= 50,
                f"{stats['placebo']['n']} quiet-day windows on {config.REACTION_PLATFORM}",
            ),
        ]
    elif prev.get("event_study"):
        facts["event_study"] = prev["event_study"]
    facts["preregistered"] = preregistered(ostats, (ev_out or {}).get("events") or [])
    facts["checks"] = checks
    write_json(facts, facts_path)
    return facts


#: The forward claims registered in docs/EVALUATION_PLAN.md on 2026-09-22 (gold dropped the next day,
#: before any forward data existed). Thresholds and minimum samples are copied from that file; the page
#: shows "collecting" until a claim has its minimum sample, and the verdict is final on FINAL_ON.
FINAL_ON = "2027-09-22"
CLAIMS = [
    {
        "id": "oil",
        "label": "On days WTI moves 5% or more, the two-meeting expectation moves the same way",
        "threshold": "share > 0.5",
        "min_n": 8,
    },
    {
        "id": "treasury",
        "label": "Daily changes in the implied rate and the 2-year yield correlate",
        "threshold": "correlation > 0.3",
        "min_n": 60,
    },
    {
        "id": "cpi",
        "label": "The CPI surprise and the 20-minute move in the expectation have the same sign",
        "threshold": "share > 0.6",
        "min_n": 6,
    },
    {
        "id": "platforms",
        "label": "Polymarket and Kalshi two-meeting expectations stay within 5 bps",
        "threshold": "share of days over 5 bps < 0.1",
        "min_n": 60,
    },
]


def preregistered(ostats: dict, events: list[dict]) -> dict:
    fwd = ostats.get("forward") or {}
    main = config.REACTION_PLATFORM
    cpi = [
        (e["hawkish_surprise"], e[main]["d_post"])
        for e in events
        if e["release"] == "cpi"
        and e["date"] >= FORWARD_FROM
        and e["hawkish_surprise"] is not None
        and e[main]["d_post"] is not None
    ]
    cpi_sign = es.sign_agreement(cpi)
    oil_n = fwd.get("wti_5pct_days", 0)
    values = {
        "oil": (oil_n, (fwd.get("wti_5pct_same_direction", 0) / oil_n) if oil_n else None, lambda v: v > 0.5),
        "treasury": (fwd.get("n_days_2y", 0), fwd.get("corr_daily_change_vs_2y"), lambda v: v > 0.3),
        "cpi": (cpi_sign["n"], cpi_sign["share"], lambda v: v > 0.6),
        "platforms": (fwd.get("n_days_both_platforms", 0), fwd.get("share_days_gap_over_5bps"), lambda v: v < 0.1),
    }
    out = []
    for c in CLAIMS:
        n, v, ok = values[c["id"]]
        status = "collecting" if n < c["min_n"] or v is None else ("holding" if ok(v) else "failing")
        out.append({**c, "n": n, "value": r(v), "status": status})
    return {"from": FORWARD_FROM, "final_on": FINAL_ON, "claims": out}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-events", action="store_true", help="skip the one-minute event study")
    args = ap.parse_args(argv)
    setup_logging()
    facts = build(with_events=not args.no_events)
    bad = [c for c in facts["checks"] if c["status"] == "fail"]
    for c in facts["checks"]:
        log.info("%s %s: %s", c["status"].upper(), c["name"], c["detail"])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
