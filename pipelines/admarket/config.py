"""Configuration for the advertising-market pipeline (project J).

Everything here is ported from the local research (``code/extract.py``, ``panel.py``, ``tests_q2_q3.py``,
``forecast.py``, ``volume_price.py``, ``q4_decompose.py``) and the registered rules in ``docs/prereg/ad-market/``.
A change to a rule below changes a published result; the reconcile test pins the research numbers.
"""

from __future__ import annotations

from dataclasses import dataclass

from pipelines.common.storage import DATA_DIR, FACTS_DIR, MARTS_DIR, RAW_DIR, REPO_ROOT, SNAP_DIR

# ---- paths ------------------------------------------------------------------------------------------------
RAW = RAW_DIR / "admarket"
RAW_XBRL = RAW / "xbrl"            # one json.gz per filing: the revenue duration facts of its XBRL instance
RAW_TEXT = RAW / "text"            # one json.gz per META/GOOGL 10-Q: the text windows the volume/price parser reads
INDEX_PATH = RAW / "filings.json"  # every 10-Q/10-K the pipeline has extracted (ticker, form, period, filed, accession)
REFRESH_LOG = RAW / "refresh_log.jsonl"
SNAP = SNAP_DIR / "admarket"
MART_DIR = MARTS_DIR / "admarket"
FACTS_PATH = FACTS_DIR / "admarket.json"
REFERENCE_CSV = DATA_DIR / "reference" / "ad_market" / "ad_revenue_quarterly_usd_bn_2026Q2.csv"
REFERENCE_SHA256 = "b01adbcbef8f214bb353df979039628fbaed5652bbf380846ddd9beab87a3450"  # registered in the forecast file
PREREG_DIR = REPO_ROOT / "docs" / "prereg" / "ad-market"
PREREG_FORECASTS = PREREG_DIR / "2026-10-21_forecasts_2026Q3_Q4.md"
PREREG_RULES = PREREG_DIR / "2026-10-21_test_rules.md"

# ---- SEC --------------------------------------------------------------------------------------------------
CIKS: dict[str, int] = {
    "GOOGL": 1652044, "META": 1326801, "AMZN": 1018724, "MSFT": 789019, "SNAP": 1564408,
    "APP": 1751008, "PINS": 1506293, "TTD": 1671933, "RDDT": 1713445,
    "MGNI": 1595974, "PUBM": 1422930, "U": 1810806,
}
FORMS = ("10-Q", "10-K")
MIN_REPORT_DATE = "2020-01-01"     # filings from 2020 carry 2019 comparatives, so the panel starts in 2019Q1
FIRST_QUARTER = "2019Q1"
TEXT_TICKERS = ("META", "GOOGL")   # volume/price disclosures are parsed from these two companies' 10-Q text

# ---- extraction rules (extract.py) ------------------------------------------------------------------------
REV = ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues")  # first one wins for a period
SEGMENT_AXES = ("StatementBusinessSegmentsAxis", "ConsolidationItemsAxis")  # extra dims allowed only on these


@dataclass(frozen=True)
class Rule:
    series: str
    ticker: str
    axis: str | None
    members: tuple[str, ...] | None   # None: total revenue, no dimensions at all
    label: str


RULES: tuple[Rule, ...] = (
    Rule("google_search", "GOOGL", "ProductOrServiceAxis", ("GoogleSearchOtherMember",), "Google Search & other"),
    Rule("youtube_ads", "GOOGL", "ProductOrServiceAxis", ("YouTubeAdvertisingRevenueMember",), "YouTube ads"),
    Rule("google_network", "GOOGL", "ProductOrServiceAxis", ("GoogleNetworkMember",), "Google Network"),
    Rule("google_ads_total", "GOOGL", "ProductOrServiceAxis", ("GoogleAdvertisingRevenueMember",),
         "Google advertising"),
    Rule("meta_ads", "META", "ProductOrServiceAxis", ("AdvertisingMember",), "Meta advertising"),
    Rule("meta_total", "META", None, None, "Meta total revenue"),
    Rule("amazon_ads", "AMZN", "ProductOrServiceAxis", ("AdvertisingServicesMember", "AdvertisingMember"),
         "Amazon advertising services"),
    Rule("msft_search_ads", "MSFT", "ProductOrServiceAxis",
         ("SearchAndNewsAdvertisingMember", "SearchAdvertisingMember"), "Microsoft search & news advertising"),
    Rule("snap_total", "SNAP", None, None, "Snap total revenue"),
    Rule("snap_ads", "SNAP", "ProductOrServiceAxis", ("AdvertisingRevenueMember",), "Snap advertising"),
    Rule("applovin_ads", "APP", "StatementBusinessSegmentsAxis", ("SoftwarePlatformMember", "AdvertisingSegmentMember"),
         "AppLovin advertising segment"),
    Rule("applovin_total", "APP", None, None, "AppLovin total revenue"),
    Rule("pinterest_total", "PINS", None, None, "Pinterest total revenue"),
    Rule("ttd_total", "TTD", None, None, "The Trade Desk total revenue"),
    Rule("reddit_ads", "RDDT", "ProductOrServiceAxis", ("AdvertisingMember",), "Reddit advertising"),
    Rule("reddit_total", "RDDT", None, None, "Reddit total revenue"),
    Rule("magnite_total", "MGNI", None, None, "Magnite total revenue"),
    Rule("pubmatic_total", "PUBM", None, None, "PubMatic total revenue"),
    Rule("unity_total", "U", None, None, "Unity total revenue"),
)
SERIES = tuple(r.series for r in RULES)
SERIES_LABEL = {r.series: r.label for r in RULES}

# ---- composite series (panel.py / forecast.py) -----------------------------------------------------------
APPLOVIN_TOTAL_FROM = "2025Q2"     # the apps business was sold in mid-2025: total revenue = advertising from here
APPLOVIN_FROM = "2022Q1"           # the panel's AppLovin series starts here (segment history before is partial)
REDDIT_VIDEO_FROM = "2024Q1"       # Reddit enters the video subset after its listing

# The video's nine-company subset: name -> (series, first quarter or None)
VIDEO_SUBSET: dict[str, tuple[str, str | None]] = {
    "Google": ("google_ads_total", None),
    "Meta": ("meta_ads", None),
    "Amazon": ("amazon_ads", None),
    "Microsoft": ("msft_search_ads", None),
    "Snap": ("snap_total", None),
    "AppLovin": ("applovin", APPLOVIN_FROM),
    "Pinterest": ("pinterest_total", None),
    "TradeDesk": ("ttd_total", None),
    "Reddit": ("reddit_ads", REDDIT_VIDEO_FROM),
}
PANEL_START = "2021Q1"
SHARE_CHART_GROUPS = ("Google", "Meta", "Amazon")  # the rest are summed as "Other six"

# Rule A forecast series: name -> (series, first quarter or None)
FORECAST_SERIES: dict[str, tuple[str, str | None]] = {
    "Google": ("google_ads_total", None), "Google Search": ("google_search", None), "YouTube": ("youtube_ads", None),
    "Google Network": ("google_network", None), "Meta": ("meta_ads", None), "Amazon": ("amazon_ads", None),
    "Microsoft": ("msft_search_ads", None), "Snap": ("snap_total", None), "AppLovin": ("applovin", APPLOVIN_FROM),
    "Pinterest": ("pinterest_total", None), "TradeDesk": ("ttd_total", None), "Reddit": ("reddit_ads", None),
    "Magnite": ("magnite_total", None), "PubMatic": ("pubmatic_total", None), "Unity": ("unity_total", None),
}
FORECAST_LAST = "2026Q2"
FORECAST_TARGETS = ("2026Q3", "2026Q4")
BACKTEST_FROM = "2023Q1"
FORECAST_LEVEL = 0.80   # the registered intervals are 80% intervals (10th to 90th percentile)
A_MISCALIBRATED_AT_OR_BELOW = 0.60
A_TOO_WIDE_AT_OR_ABOVE = 0.95

# ---- registered tests (docs/prereg/ad-market/2026-10-21_test_rules.md) ----------------------------------
Q2_AFFECTED = ("meta_ads", "snap_total", "pinterest_total")
Q2_CONTROL = ("google_search", "msft_search_ads")
Q2_BEFORE = ("2021Q1", "2021Q2")
Q2_AFTER = ("2022Q1", "2022Q2", "2022Q3", "2022Q4")
Q2_THRESHOLD = 0.05
ATT_RELEASE = "2021-04-26"
ATT_IOS = "iOS 14.5"

Q3_OPEN = ("google_network", "ttd_total", "magnite_total", "pubmatic_total")
Q3_WALLED = ("google_search", "youtube_ads", "meta_ads", "amazon_ads", "msft_search_ads", "snap_total",
             "pinterest_total", "reddit_ads")
Q3_FROM_END = "2023Q2"   # trailing four quarters 2022Q3-2023Q2
Q3_TO_END = "2026Q2"     # trailing four quarters 2025Q3-2026Q2
Q3_YEARS = 3
Q3_THRESHOLD = 0.05

Q1_BEFORE = ("2025Q3", "2025Q4", "2026Q1", "2026Q2")
Q1_AFTER = ("2026Q3", "2026Q4", "2027Q1", "2027Q2")
Q1_THRESHOLD = 0.005
Q5_QUARTERS = Q1_AFTER

# ---- Q4 volume x price (volume_price.py / q4_decompose.py) ----------------------------------------------
# The method check (a quarter's gap above 3 points makes the decomposition unusable for that series) was set in
# the research plan before the text was parsed; it is not part of the registered files.
Q4_SERIES: dict[str, dict[str, str]] = {
    "meta_ads": {"revenue": "meta_ads", "label": "Meta advertising", "volume": "Ad impressions",
                 "price": "Average price per ad"},
    "google_search_clicks": {"revenue": "google_search", "label": "Google Search & other", "volume": "Paid clicks",
                             "price": "Cost per click"},
    "google_network": {"revenue": "google_network", "label": "Google Network", "volume": "Impressions",
                       "price": "Cost per impression"},
}
Q4_MAX_GAP_PP = 3.0

# ---- the video and the claim check ------------------------------------------------------------------------
VIDEO = {
    "channel": "Money or Life",
    "title": "投资 Meta / 谷歌 / AppLovin 前必须懂的广告知识",
    "title_en": "What you need to know about advertising before investing in Meta, Google or AppLovin",
    "date": "2025-11-29",
    "url": "https://www.youtube.com/watch?v=eXXE553YhWE",
    "first_quarter": "2021Q1",
    "last_quarter": "2025Q3",
    "cagr_claimed": 0.172,
    "google_share_claimed": [0.558, 0.492],
}
# Counts from the claim-check table written before this port (local research, 2026-10-21). They are a manual
# comparison of the video's table with the filings and are not recomputed here; the page says so.
CLAIM_CHECK = {
    "claims": 34, "holds": 19, "partly": 10, "wrong": 3, "unchecked": 2,
    "table_cells": 155, "cells_match": 147, "cells_restated": 8,
}

SOURCES = [
    {"name": "SEC EDGAR: 10-Q and 10-K XBRL instances of twelve advertising companies", "url": "https://www.sec.gov/edgar/search/"},
    {"name": "SEC EDGAR: Meta and Alphabet 10-Q text (ad impressions, price per ad, paid clicks, cost per click)",
     "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001652044&type=10-Q"},
]
