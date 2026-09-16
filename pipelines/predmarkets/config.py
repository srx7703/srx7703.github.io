"""Market-set definitions for the prediction-market snapshotter.

A *universe* is every market we record a quote for (cheap: list endpoints only).
A *tier-1* subset additionally gets an order-book snapshot every run (one call per market),
because order books are not recoverable from either platform's history endpoints.

Tag ids and series tickers were discovered on 2026-09-15; see docs/DATA_MODEL.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class KalshiUniverse:
    """Either an explicit list of series, or a scan of all open events filtered by
    category + close-time window (used for the midterms, where state races live in
    hundreds of small series). Note: Kalshi closes 2026 race markets on 2027-11-03,
    a year after election day, so the midterms window runs to 2027-12-01."""

    series: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    close_from: str | None = None  # ISO date, inclusive
    close_to: str | None = None  # ISO date, exclusive


@dataclass(frozen=True)
class MarketSet:
    name: str
    # Polymarket
    polymarket_universe_tags: tuple[int, ...]
    polymarket_tier1_tags: tuple[int, ...] = ()
    polymarket_tier1_slug_regex: str | None = None  # applied to events from tier1 tags
    polymarket_tier1_slugs: tuple[str, ...] = ()  # headline events, always included
    # Kalshi
    kalshi_universe: KalshiUniverse = field(default_factory=KalshiUniverse)
    kalshi_tier1_series: tuple[str, ...] = ()
    kalshi_tier1_series_regex: str | None = None  # applied to series_ticker of universe events
    # Only fetch books for markets whose yes price is inside these bounds (skip dead markets)
    book_price_bounds: tuple[float, float] = (0.02, 0.98)


MIDTERMS = MarketSet(
    name="midterms",
    polymarket_universe_tags=(102289,),  # "Midterms"
    polymarket_tier1_tags=(104093, 104094),  # "Senate midterms", "Governor midterms"
    polymarket_tier1_slug_regex=r"(senate-election-winner|governor-winner-2026|governor-election-winner)",
    polymarket_tier1_slugs=(
        "balance-of-power-2026-midterms",
        "which-party-will-win-the-house-in-2026",
        "which-party-will-win-the-senate-in-2026",
        "republican-senate-seats-after-the-2026-midterm-elections-927",
        "how-many-senate-and-house-seats-will-republicans-have-after-the-midterms-20260625152833634",
        "how-many-republican-governors-after-the-2026-midterm-elections",
    ),
    kalshi_universe=KalshiUniverse(categories=("Elections",), close_from="2026-11-01", close_to="2027-12-01"),
    kalshi_tier1_series=(
        "CONTROLH",
        "CONTROLS",
        "KXRGOVCOUNT",
        "KXGOVWINS",
        "KXSAMEPARTYCONGRESS",
        "KXLOSEMAJORITY",
        "KXBALANCEPOWERCOMBO",
        "KXRHOUSESEATS",
        "KXDHOUSESEATS",
        "RHOUSESEATS",
        "RSENATESEATS",
        "KXDSENATESEATS",
        "KXDSENATESEATSH",
        "KXGOPMIDTERMSSEATS",
        "KXDEMMIDTERMSSEATS",
        "KXAPCALLSENATE",
        "KXAPCALLHOUSE",
    ),
    # general-election state races: SENATEAK, SENATEPARTYTN, GOVPARTYNV, KXSENATEOK, HOUSECA47 ...
    kalshi_tier1_series_regex=r"^(SENATE|SENATEPARTY-?|GOVPARTY|KXSENATE|KXGOV)[A-Z]{2}$|^HOUSE[A-Z]{2}\d{1,2}$",
)

FOMC = MarketSet(
    name="fomc",
    polymarket_universe_tags=(100478, 100196),  # "fomc", "Fed Rates"
    polymarket_tier1_tags=(100478, 100196),
    kalshi_universe=KalshiUniverse(
        series=(
            "KXFEDDECISION",
            "KXFED",
            "KXFEDHIKE",
            "KXRATEHIKE",
            "KXRATECUTS",
            "KXFEDFUNDSYEAR",
            "KXEFFR",
            "KXDOTPLOT",
            "KXFOMCGUIDE",
            "KXFEDDISSENT",
            "FEDRATEMIN",
            "TERMINALRATE",
            "KXZERORATE",
            "KXEMERCUTS",
        )
    ),
    kalshi_tier1_series=("KXFEDDECISION", "KXFED", "KXDOTPLOT", "KXRATEHIKE", "KXFEDHIKE"),
)

MARKET_SETS: dict[str, MarketSet] = {ms.name: ms for ms in (MIDTERMS, FOMC)}
