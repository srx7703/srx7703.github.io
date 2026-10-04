"""Fetch SEC XBRL companyfacts for one universe and keep the facts we use.

Usage:
    uv run python -m pipelines.sec.ingest [--universe saas|payments] [--tickers DDOG,NET]

Writes (per universe, see config.UNIVERSES; `saas` -> data/snapshots/sec, `payments` -> data/snapshots/sec_payments):
    <snap>/facts/<TICKER>.parquet   duration facts for mapped tags (deduped, latest filing wins)
    <snap>/companies.json           ticker -> cik, name, fetched_at, facts per metric (+ checks for payments)
    <snap>/refresh_log.jsonl        one line per run

SEC asks for a descriptive User-Agent and <= 10 requests/second; both are honoured. Override the
User-Agent with SEC_USER_AGENT if you want to add a contact address. The payments universe refuses to run
without SEC_USER_AGENT (it is a CI secret): it never falls back to the default string or to anyone's address.

Payments-only behaviour:
  * 20-F / 6-K filers and the ifrs-full taxonomy are accepted. Monetary facts may be in any ISO currency;
    one reporting currency is chosen per filer (USD when it reports in USD at all) and recorded.
  * companyfacts can lag a filing by days. When a filer's latest quarter for an income-statement metric is
    older than the latest calendar quarter that should be filed by now, the matching `frames` cell
    (one tag x unit x calendar quarter, every filer) is fetched and the filer's row is taken from it. Each
    use is recorded as a check, so the page says which numbers came that way.
  * Every company and every frame fails on its own: an error is logged and recorded, never fatal.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from collections import Counter
from collections.abc import Callable
from datetime import date, timedelta

import httpx
import polars as pl

from pipelines.common.checks import check
from pipelines.common.http import HttpClient
from pipelines.common.log import setup_logging
from pipelines.common.storage import SNAP_DIR, append_jsonl, utc_now, write_json, write_parquet
from pipelines.sec.config import CURRENCY, FRAMES_METRICS, PAYMENTS_FILERS, TAG_MAP, TICKERS, UNIVERSES, Universe

log = logging.getLogger("sec.ingest")

SEC_UA = os.environ.get("SEC_USER_AGENT") or "portfolio-pipelines/0.1 (github.com/srx7703)"
FACT_COLUMNS = [
    "ticker",
    "cik",
    "metric",
    "tag",
    "unit",
    "start",
    "end",
    "days",
    "val",
    "fy",
    "fp",
    "form",
    "filed",
    "frame",
    "accn",
]
FACT_DTYPES: dict[str, pl.DataType] = {
    "ticker": pl.Utf8,
    "cik": pl.Utf8,
    "metric": pl.Utf8,
    "tag": pl.Utf8,
    "unit": pl.Utf8,
    "start": pl.Utf8,
    "end": pl.Utf8,
    "days": pl.Int64,
    "val": pl.Float64,
    "fy": pl.Int64,
    "fp": pl.Utf8,
    "form": pl.Utf8,
    "filed": pl.Utf8,
    "frame": pl.Utf8,
    "accn": pl.Utf8,
}
ISO_CCY = re.compile(r"^[A-Z]{3}$")
FRAMES_FORM = "frames"  # `form` value of a fact taken from the frames API rather than companyfacts
QUARTER_FILING_LAG_DAYS = 45  # 10-Q deadline for non-accelerated filers; earlier filers are simply not stale


class SecUserAgentMissing(RuntimeError):
    """Raised before any network call in a universe that requires SEC_USER_AGENT."""


def payments_user_agent() -> str:
    ua = (os.environ.get("SEC_USER_AGENT") or "").strip()
    if not ua:
        raise SecUserAgentMissing(
            "SEC_USER_AGENT is not set. The payments SEC universe needs it (an Actions secret: "
            "'<name> <contact email>'); it never falls back to a default or personal address."
        )
    return ua


class SecClient:
    def __init__(self, user_agent: str | None = None) -> None:
        self.http = HttpClient(
            min_interval=0.12, headers={"User-Agent": user_agent or SEC_UA, "Accept-Encoding": "gzip, deflate"}
        )
        self._frames: dict[str, dict | None] = {}

    def tickers(self) -> dict[str, tuple[str, str]]:
        data = self.http.get_json("https://www.sec.gov/files/company_tickers.json")
        return {v["ticker"].upper(): (f"{int(v['cik_str']):010d}", v["title"]) for v in data.values()}

    def companyfacts(self, cik: str) -> dict:
        return self.http.get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")

    def frame(self, taxonomy: str, tag: str, unit: str, period: str) -> dict | None:
        """One frames cell, cached per run (a frame serves every lagging filer). None when SEC has no frame (404)."""
        url = frames_url(taxonomy, tag, unit, period)
        if url not in self._frames:
            try:
                self._frames[url] = self.http.get_json(url)
            except httpx.HTTPStatusError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    self._frames[url] = None
                else:
                    raise
        return self._frames[url]


def frames_url(taxonomy: str, tag: str, unit: str, period: str) -> str:
    """https://data.sec.gov/api/xbrl/frames/{taxonomy}/{tag}/{unit}/{period}.json

    period: CY2026Q2 (duration, ~91 days), CY2026Q2I (instant), CY2025 (annual duration).
    """
    if not re.fullmatch(r"CY\d{4}(Q[1-4]I?)?", period):
        raise ValueError(f"bad frames period {period!r}")
    return f"https://data.sec.gov/api/xbrl/frames/{taxonomy}/{tag}/{unit}/{period}.json"


def _split_tag(tag: str, default_taxonomy: str) -> tuple[str, str]:
    return tuple(tag.split(":", 1)) if ":" in tag else (default_taxonomy, tag)  # type: ignore[return-value]


def reporting_currency(cf: dict, universe: Universe) -> str | None:
    """The filer's reporting currency: USD if any revenue fact is in USD, else its most common ISO unit."""
    facts = cf.get("facts", {})
    counts: Counter[str] = Counter()
    for metric, (tags, unit) in universe.tag_map.items():
        if unit != CURRENCY:
            continue
        for tag in tags:
            tax, name = _split_tag(tag, universe.default_taxonomy)
            for u, items in facts.get(tax, {}).get(name, {}).get("units", {}).items():
                if ISO_CCY.match(u):
                    counts[u] += len(items) * (10 if metric == "revenue" else 1)
    if not counts:
        return None
    return "USD" if "USD" in counts else counts.most_common(1)[0][0]


def extract_facts(cf: dict, ticker: str, cik: str, universe: Universe | None = None) -> list[dict]:
    """Duration facts (start & end) for every mapped tag and accepted form, deduped on (metric, tag, start, end).

    With the default (SaaS) universe this is exactly the original behaviour: us-gaap tags, USD/shares units,
    10-K/10-Q forms. The `tag` column holds the tag as written in the universe's tag map.
    """
    u = universe or UNIVERSES["saas"]
    facts = cf.get("facts", {})
    ccy = reporting_currency(cf, u) if any(unit == CURRENCY for _, unit in u.tag_map.values()) else None
    rows: dict[tuple, dict] = {}
    for metric, (tags, unit_spec) in u.tag_map.items():
        unit = ccy if unit_spec == CURRENCY else unit_spec
        if unit is None:
            continue
        for tag in tags:
            tax, name = _split_tag(tag, u.default_taxonomy)
            node = facts.get(tax, {}).get(name)
            if not node:
                continue
            for f in node.get("units", {}).get(unit, []):
                if not f.get("start") or not f.get("end") or f.get("form") not in u.forms:
                    continue
                s, e = date.fromisoformat(f["start"]), date.fromisoformat(f["end"])
                key = (metric, tag, f["start"], f["end"])
                row = {
                    "ticker": ticker,
                    "cik": cik,
                    "metric": metric,
                    "tag": tag,
                    "unit": unit,
                    "start": f["start"],
                    "end": f["end"],
                    "days": (e - s).days,
                    "val": float(f["val"]),
                    "fy": f.get("fy"),
                    "fp": f.get("fp"),
                    "form": f.get("form"),
                    "filed": f.get("filed"),
                    "frame": f.get("frame"),
                    "accn": f.get("accn"),
                }
                prev = rows.get(key)
                if prev is None or (row["filed"] or "") > (prev["filed"] or ""):
                    rows[key] = row  # latest filing wins (restatements)
    return list(rows.values())


# ------------------------------------------------------------------ frames fallback (payments universe)


def quarter_end(year: int, q: int) -> date:
    return date(year, 3 * q, 31 if q in (1, 4) else 30)


def calendar_quarter(d: date) -> tuple[int, int]:
    """Calendar quarter a period end belongs to, the way frames align it (nearest quarter end)."""
    best = min(
        ((y, q) for y in (d.year - 1, d.year, d.year + 1) for q in (1, 2, 3, 4)),
        key=lambda yq: abs((quarter_end(*yq) - d).days),
    )
    return best


def expected_latest_quarter(today: date, lag_days: int = QUARTER_FILING_LAG_DAYS) -> tuple[int, int]:
    """Latest calendar quarter whose 10-Q should be on file by `today`."""
    y, q = calendar_quarter(today)
    while quarter_end(y, q) + timedelta(days=lag_days) > today:
        y, q = (y, q - 1) if q > 1 else (y - 1, 4)
    return y, q


def missing_quarters(rows: list[dict], metric: str, today: date) -> tuple[list[tuple[int, int]], dict | None]:
    """Calendar quarters after the filer's latest covered quarter for `metric`, up to the expected latest one.

    Only quarterly reporters are checked (a filer with no 3-month fact files annual/half-year reports, and a
    calendar-quarter frame would not contain it). A fiscal-year span also covers its last quarter, because the
    transform derives Q4 = FY - 9M and 10-Ks rarely tag a discrete Q4; so coverage runs to the latest end of
    either a 3-month or a fiscal-year fact. Returns the quarters and the latest 3-month fact (tag/unit to reuse).
    """
    q_rows = [r for r in rows if r["metric"] == metric and 80 <= r["days"] <= 100]
    if not q_rows:
        return [], None
    latest = max(q_rows, key=lambda r: (r["end"], r["filed"] or ""))
    cover = q_rows + [r for r in rows if r["metric"] == metric and 350 <= r["days"] <= 380]
    have = calendar_quarter(date.fromisoformat(max(r["end"] for r in cover)))
    target = expected_latest_quarter(today)
    out: list[tuple[int, int]] = []
    y, q = have
    while (y, q) < target:
        y, q = (y, q + 1) if q < 4 else (y + 1, 1)
        out.append((y, q))
    return out, latest


def frames_fallback(
    rows: list[dict], ticker: str, cik: str, universe: Universe, frame_fn: Callable[..., dict | None], today: date
) -> tuple[list[dict], list[dict]]:
    """Add rows from `frames` for quarters companyfacts does not have yet. Returns (new rows, checks).

    frame_fn(taxonomy, tag, unit, period) -> frames JSON or None. Any exception is caught per frame.
    """
    added: list[dict] = []
    checks: list[dict] = []
    cik_int = int(cik)
    for metric in FRAMES_METRICS:
        if metric not in universe.tag_map:
            continue
        gaps, latest = missing_quarters(rows, metric, today)
        if not gaps or latest is None:
            continue
        tax, name = _split_tag(latest["tag"], universe.default_taxonomy)
        for y, q in gaps:
            period = f"CY{y}Q{q}"
            try:
                data = frame_fn(tax, name, latest["unit"], period)
            except Exception as exc:  # noqa: BLE001 - one frame failing must not lose the filer
                log.warning("%s %s frames %s failed: %r", ticker, metric, period, exc)
                checks.append(check(f"Frames {ticker} {metric}", False, f"frames {period} request failed: {exc!r}"))
                continue
            hit = next((d for d in (data or {}).get("data", []) if int(d.get("cik", -1)) == cik_int), None)
            if hit is None or not hit.get("start") or hit["end"] <= latest["end"]:
                checks.append(
                    check(
                        f"Frames {ticker} {metric}",
                        False,
                        f"{ticker} {metric} {period} missing in companyfacts and in frames {tax}/{name}",
                        warn=True,
                    )
                )
                continue
            s, e = date.fromisoformat(hit["start"]), date.fromisoformat(hit["end"])
            added.append(
                {
                    "ticker": ticker,
                    "cik": cik,
                    "metric": metric,
                    "tag": latest["tag"],
                    "unit": latest["unit"],
                    "start": hit["start"],
                    "end": hit["end"],
                    "days": (e - s).days,
                    "val": float(hit["val"]),
                    "fy": None,
                    "fp": None,
                    "form": FRAMES_FORM,
                    "filed": None,
                    "frame": (data or {}).get("ccp") or period,
                    "accn": hit.get("accn"),
                }
            )
            checks.append(
                check(
                    f"Frames {ticker} {metric}",
                    False,  # a warning, so the page shows which numbers came this way
                    f"frames fallback used for {ticker} {metric} {period} ({tax}/{name}, accn {hit.get('accn')})",
                    warn=True,
                )
            )
    return added, checks


# ------------------------------------------------------------------ run


def resolve_filers(universe: Universe, lookup: dict[str, tuple[str, str]], only: list[str]) -> dict:
    """ticker -> (cik, name) or an error string. Payments filers try aliases and prefer a pinned CIK."""
    out: dict[str, tuple[str, str] | str] = {}
    specs = {f["ticker"]: f for f in PAYMENTS_FILERS} if universe.name == "payments" else {}
    for t in only:
        spec = specs.get(t)
        aliases = spec["tickers"] if spec else (t,)
        hit = next((lookup[a] for a in aliases if a in lookup), None)
        if spec and spec.get("cik"):
            out[t] = (spec["cik"], hit[1] if hit else spec["company"])
        elif hit:
            out[t] = hit
        else:
            out[t] = f"{t}: not in SEC ticker file (tried {', '.join(aliases)})"
    return out


def run(universe: Universe, client, tickers: list[str], today: date | None = None, snap_dir=None) -> dict:
    """Fetch, extract and write every ticker; one failure never stops the others. Returns companies.json."""
    snap = (snap_dir or SNAP_DIR) / universe.snap_subdir
    out_dir = snap / "facts"
    today = today or utc_now().date()
    companies: dict[str, dict] = {}
    failed: list[str] = []
    checks: list[dict] = []
    try:
        lookup = client.tickers()
    except Exception as exc:  # noqa: BLE001 - pinned CIKs can still be fetched
        if universe.name == "saas":
            raise  # SaaS has no pinned CIKs: fail before writing anything, as before
        log.exception("SEC company_tickers lookup failed")
        lookup = {}
        checks.append(check("SEC ticker lookup", False, f"company_tickers.json failed: {exc!r}"))
    for t, res in resolve_filers(universe, lookup, tickers).items():
        if isinstance(res, str):
            log.warning("%s; skipped", res)
            failed.append(t if universe.name == "saas" else res)
            continue
        cik, name = res
        try:
            cf = client.companyfacts(cik)
            rows = extract_facts(cf, t, cik, universe)
            fb_checks: list[dict] = []
            if universe.frames_fallback:
                extra, fb_checks = frames_fallback(rows, t, cik, universe, client.frame, today)
                rows += extra
                checks += fb_checks
            df = pl.DataFrame([{c: r.get(c) for c in FACT_COLUMNS} for r in rows], schema=FACT_DTYPES)
        except Exception as exc:  # noqa: BLE001 - keep going for the other companies
            log.exception("%s companyfacts failed", t)
            failed.append(f"{t}: {exc!r}")
            continue
        write_parquet(df, out_dir / f"{t}.parquet")
        per_metric = {m: int(n) for m, n in df.group_by("metric").agg(pl.len()).iter_rows()}
        companies[t] = {
            "cik": cik,
            "name": cf.get("entityName") or name,
            "fetched_at": utc_now().isoformat(timespec="seconds"),
            "facts": df.height,
            "facts_per_metric": per_metric,
            "last_end": df["end"].max() if df.height else None,
        }
        if universe.name != "saas":
            forms = set(df["form"].drop_nulls().to_list())
            companies[t] |= {
                "currency": df["unit"].filter(df["metric"] == "revenue").mode().first() if df.height else None,
                "taxonomy": sorted({tg.split(":", 1)[0] for tg in df["tag"].unique().to_list()}),
                "foreign_private_issuer": bool(forms & {"20-F", "20-F/A", "6-K", "6-K/A", "40-F"}),
                "frames_rows": int((df["form"] == FRAMES_FORM).sum()),
            }
        log.info("%s: %d facts, last period end %s", t, df.height, companies[t]["last_end"])
    now = utc_now().isoformat(timespec="seconds")
    out = {"generated_at": now, "companies": companies, "failed": failed}
    if universe.name != "saas":
        out["checks"] = checks
    if not companies:
        # Everything failed: keep the last good companies.json and do not log a refresh that never happened.
        log.error("no company fetched for universe %s; companies.json and refresh_log left untouched", universe.name)
        return out
    write_json(out, snap / "companies.json")
    append_jsonl({"ts": now, "ok": len(companies), "failed": len(failed)}, snap / "refresh_log.jsonl")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--universe", choices=sorted(UNIVERSES), default="saas")
    ap.add_argument("--tickers", default=None, help="comma-separated subset (default: the whole universe)")
    args = ap.parse_args(argv)
    setup_logging()
    universe = UNIVERSES[args.universe]
    tickers = [x.strip().upper() for x in (args.tickers or ",".join(universe.tickers)).split(",") if x.strip()]
    client = SecClient(payments_user_agent() if universe.require_user_agent else None)
    out = run(universe, client, tickers)
    n = len(TICKERS) if universe.name == "saas" else len(universe.tickers)
    return 1 if out["failed"] and len(out["failed"]) == n else 0


__all__ = ["SEC_UA", "SecClient", "TAG_MAP", "extract_facts", "frames_url", "run"]


if __name__ == "__main__":
    sys.exit(main())
