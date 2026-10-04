"""SaaS benchmark universe and XBRL tag map.

Tag fallbacks are tried in order per metric; the first tag with enough duration facts wins per
company. Coverage is reported in data/marts/sec/saas_coverage.json so gaps are visible on the page.

Two universes share this code (see UNIVERSES at the bottom): the SaaS benchmark (US-GAAP 10-K/10-Q filers
only, the original scope) and the payments landscape (project H), which also admits foreign private issuers
filing 20-F/6-K in either us-gaap or ifrs-full, and falls back to the XBRL `frames` API when companyfacts
lags. Each universe has its own snapshot directory, so neither build can read the other's facts.
"""

from __future__ import annotations

from dataclasses import dataclass

# US-listed, US-GAAP filers (10-K/10-Q). Foreign private issuers (20-F/IFRS) are excluded.
TICKERS: tuple[str, ...] = (
    "CRM",
    "NOW",
    "ADBE",
    "INTU",
    "WDAY",
    "SNOW",
    "DDOG",
    "CRWD",
    "ZS",
    "NET",
    "MDB",
    "HUBS",
    "TEAM",
    "PANW",
    "PLTR",
    "OKTA",
    "TWLO",
    "GTLB",
    "DOCU",
    "ZM",
    "S",
    "PATH",
    "BILL",
    "ESTC",
    "DT",
    "APPF",
    "VEEV",
    "TTD",
    "SHOP",
)

# metric -> (candidate tags in priority order, unit)
TAG_MAP: dict[str, tuple[tuple[str, ...], str]] = {
    "revenue": (
        (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueNet",
        ),
        "USD",
    ),
    "cogs": (("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfServices"), "USD"),
    "gross_profit": (("GrossProfit",), "USD"),
    "op_income": (("OperatingIncomeLoss",), "USD"),
    "net_income": (("NetIncomeLoss", "ProfitLoss"), "USD"),
    "ocf": (
        (
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ),
        "USD",
    ),
    "capex": (("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"), "USD"),
    "cap_software": (("PaymentsToDevelopSoftware", "PaymentsForSoftware"), "USD"),
    "sbc": (("ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"), "USD"),
    "rnd": (("ResearchAndDevelopmentExpense",), "USD"),
    "snm": (("SellingAndMarketingExpense",), "USD"),
    "gna": (("GeneralAndAdministrativeExpense",), "USD"),
    "diluted_shares": (("WeightedAverageNumberOfDilutedSharesOutstanding",), "shares"),
}

# Metrics that are flows (sum over quarters). Everything above is a flow.
FLOW_METRICS: tuple[str, ...] = tuple(TAG_MAP)


# --------------------------------------------------------------------------------------------------
# Universes. The SaaS benchmark and the payments landscape share the SEC code but never each other's
# facts: each universe has its own snapshot directory and transform.build() reads only its own tickers.
# BILL and SHOP are in both, which is exactly why a shared directory would not do.
# --------------------------------------------------------------------------------------------------

US_GAAP_FORMS: tuple[str, ...] = ("10-K", "10-Q", "10-K/A", "10-Q/A")
# Foreign private issuers: 20-F annual, 6-K interim (half-year or quarterly results furnished with XBRL),
# 40-F for Canadian MJDS filers. Kept to the payments universe only.
FPI_FORMS: tuple[str, ...] = ("20-F", "20-F/A", "6-K", "6-K/A", "40-F", "40-F/A", "10-KT", "10-KT/A")

# A unit of "CURRENCY" means: any ISO-4217 code, one reporting currency chosen per filer (USD preferred).
CURRENCY = "CURRENCY"

# Payments tag map: entries are "<taxonomy>:<Tag>" so US-GAAP and IFRS tags can share one priority list
# (a filer uses one taxonomy, so in practice only one half of each list ever matches).
PAYMENTS_TAG_MAP: dict[str, tuple[tuple[str, ...], str]] = {
    "revenue": (
        (
            "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
            "us-gaap:Revenues",
            "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
            "us-gaap:RevenuesNetOfInterestExpense",
            "ifrs-full:Revenue",
            "ifrs-full:RevenueFromContractsWithCustomers",
        ),
        CURRENCY,
    ),
    "gross_profit": (("us-gaap:GrossProfit", "ifrs-full:GrossProfit"), CURRENCY),
    "op_income": (("us-gaap:OperatingIncomeLoss", "ifrs-full:ProfitLossFromOperatingActivities"), CURRENCY),
    "net_income": (
        (
            "us-gaap:NetIncomeLoss",
            "us-gaap:ProfitLoss",
            "ifrs-full:ProfitLoss",
            "ifrs-full:ProfitLossAttributableToOwnersOfParent",
        ),
        CURRENCY,
    ),
    "ocf": (
        (
            "us-gaap:NetCashProvidedByUsedInOperatingActivities",
            "us-gaap:NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
            "ifrs-full:CashFlowsFromUsedInOperatingActivities",
        ),
        CURRENCY,
    ),
    "capex": (
        (
            "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
            "us-gaap:PaymentsToAcquireProductiveAssets",
            "ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
        ),
        CURRENCY,
    ),
    "cap_software": (
        (
            "us-gaap:PaymentsToDevelopSoftware",
            "us-gaap:PaymentsForSoftware",
            "ifrs-full:PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities",
        ),
        CURRENCY,
    ),
    "sbc": (
        (
            "us-gaap:ShareBasedCompensation",
            "us-gaap:AllocatedShareBasedCompensationExpense",
            "ifrs-full:AdjustmentsForSharebasedPayments",
        ),
        CURRENCY,
    ),
}

# Income-statement metrics are reported as discrete quarters, so a calendar-quarter `frames` cell can fill a
# missing latest quarter. Cash-flow items are year-to-date only and never appear in a quarterly frame.
FRAMES_METRICS: tuple[str, ...] = ("revenue", "op_income", "net_income")


# Payments filers. `company` is the name used in pipelines/payments/config.UNIVERSE; `tickers` are tried in
# order against SEC company_tickers.json (old tickers kept as aliases); `cik` is pinned where it was read on
# the filing itself [V], so a ticker change cannot silently drop a company. Adyen (Euronext, no SEC
# registration) and private companies are not here.
PAYMENTS_FILERS: tuple[dict, ...] = (
    {"ticker": "PYPL", "company": "PayPal", "tickers": ("PYPL",), "cik": None},
    {"ticker": "XYZ", "company": "Block/Square", "tickers": ("XYZ", "SQ"), "cik": None},  # SQ -> XYZ 2025-01
    {"ticker": "TOST", "company": "Toast", "tickers": ("TOST",), "cik": None},
    # Fiserv moved NYSE:FI -> Nasdaq:FISV on 2025-11-11 (8-K 2025-10-29) [V]
    {"ticker": "FISV", "company": "Fiserv Clover", "tickers": ("FISV", "FI"), "cik": None},
    {"ticker": "GPN", "company": "Global Payments", "tickers": ("GPN",), "cik": None},
    {"ticker": "FOUR", "company": "Shift4", "tickers": ("FOUR",), "cik": None},
    {"ticker": "SHOP", "company": "Shopify Payments", "tickers": ("SHOP",), "cik": None},
    {"ticker": "AFRM", "company": "Affirm", "tickers": ("AFRM",), "cik": None},
    # Klarna Group plc, 20-F FY2025 (IFRS), NYSE:KLAR [V]
    {"ticker": "KLAR", "company": "Klarna", "tickers": ("KLAR",), "cik": "0002003292"},
    {"ticker": "SEZL", "company": "Sezzle", "tickers": ("SEZL",), "cik": None},
    {"ticker": "BILL", "company": "BILL", "tickers": ("BILL",), "cik": None},
    # Navan, Inc. S-1/A: Nasdaq "NAVN", listed 2025-10-30 [V]
    {"ticker": "NAVN", "company": "Navan", "tickers": ("NAVN",), "cik": "0001639723"},
    {"ticker": "CPAY", "company": "Corpay", "tickers": ("CPAY", "FLT"), "cik": None},
    # Wise Group plc: primary listing moved to Nasdaq:WSE on 2026-05-11, files 20-F (FY ends March) [V]
    {"ticker": "WSE", "company": "Wise", "tickers": ("WSE",), "cik": "0002099039"},
    {"ticker": "PAYO", "company": "Payoneer", "tickers": ("PAYO",), "cik": None},
    {"ticker": "DLO", "company": "dLocal", "tickers": ("DLO",), "cik": None},  # 20-F, IFRS
    {"ticker": "FLYW", "company": "Flywire", "tickers": ("FLYW",), "cik": None},
    {"ticker": "MQ", "company": "Marqeta", "tickers": ("MQ",), "cik": None},
    {"ticker": "CRCL", "company": "Circle", "tickers": ("CRCL",), "cik": None},
)


@dataclass(frozen=True)
class Universe:
    """One benchmark universe: who is in it, which tags and forms count, and where its facts live."""

    name: str
    tickers: tuple[str, ...]
    snap_subdir: str  # data/snapshots/<snap_subdir>/{facts/<T>.parquet, companies.json, refresh_log.jsonl}
    tag_map: dict[str, tuple[tuple[str, ...], str]]
    forms: tuple[str, ...]
    default_taxonomy: str = "us-gaap"  # for tags written without a "<taxonomy>:" prefix
    frames_fallback: bool = False
    require_user_agent: bool = False  # never fall back to the default UA (or a personal address)


UNIVERSES: dict[str, Universe] = {
    "saas": Universe("saas", TICKERS, "sec", TAG_MAP, US_GAAP_FORMS),
    "payments": Universe(
        "payments",
        tuple(f["ticker"] for f in PAYMENTS_FILERS),
        "sec_payments",
        PAYMENTS_TAG_MAP,
        US_GAAP_FORMS + FPI_FORMS,
        frames_fallback=True,
        require_user_agent=True,
    ),
}
