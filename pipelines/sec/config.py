"""SaaS benchmark universe and XBRL tag map.

Tag fallbacks are tried in order per metric; the first tag with enough duration facts wins per
company. Coverage is reported in data/marts/sec/saas_coverage.json so gaps are visible on the page.
"""

from __future__ import annotations

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
