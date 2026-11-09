"""Configuration for the AI / data-center server makers page.

The pool, its four groups, and every series definition that a pre-registered rule in
``docs/prereg/ai-servers/`` depends on live here. A change to a group or a series changes a published result, so
the rules file is the authority and the tests pin this module to it.
"""

from __future__ import annotations

from dataclasses import dataclass

from pipelines.common.storage import DATA_DIR, FACTS_DIR, MARTS_DIR, RAW_DIR, REPO_ROOT, SNAP_DIR

# ---- paths ------------------------------------------------------------------------------------------------
RAW = RAW_DIR / "servers"
RAW_TW_MONTHLY = RAW / "mops_monthly"     # one cp950 HTML page per month and fetch: the whole TWSE-listed table
RAW_TW_QUARTERLY = RAW / "mops_quarterly"  # MOPS t163sb04 (income) / t163sb05 (balance sheet) per quarter
RAW_XBRL = RAW / "xbrl"                   # one json.gz per 10-Q/10-K: the facts this page reads from its instance
RAW_EM = RAW / "eastmoney"                # East Money F10 income statement / balance sheet per A-share
XBRL_INDEX = RAW / "filings.json"
REFRESH_LOG = RAW / "refresh_log.jsonl"
SNAP = SNAP_DIR / "servers"
MART_DIR = MARTS_DIR / "servers"
FACTS_PATH = FACTS_DIR / "servers.json"
REFERENCE_DIR = DATA_DIR / "reference" / "servers"
PREREG_DIR = REPO_ROOT / "docs" / "prereg" / "ai-servers"
PREREG_RULES = PREREG_DIR / "2026-11-09_test_rules.md"
PREREG_FORECASTS = PREREG_DIR / "2026-11-09_forecasts.md"

# ---- pool -------------------------------------------------------------------------------------------------
GROUPS = {
    "oem": "Brand makers (OEM)",
    "odm": "Taiwan makers building to the customer's design (ODM)",
    "ems": "Contract manufacturers (EMS)",
    "cn": "Chinese server makers",
}


@dataclass(frozen=True)
class Maker:
    key: str            # TWSE code, US ticker, or A-share code with exchange suffix
    name: str
    name_cn: str
    group: str          # key of GROUPS
    market: str         # "us" | "tw" | "cn" | "hk"
    valuation: str      # ticker in pipelines/valuation/config (Yahoo symbol)
    fy_end_month: int = 12
    comparator: bool = False  # shown for context; never in a group total or a graded test
    purity: str = ""    # how much of reported revenue is servers, in words, filled from the curated layer
    cik: int | None = None


MAKERS: tuple[Maker, ...] = (
    # brand makers
    Maker("DELL", "Dell Technologies", "戴尔", "oem", "us", "DELL", 1, cik=1571996),
    Maker("HPE", "Hewlett Packard Enterprise", "慧与", "oem", "us", "HPE", 10, cik=1645590),
    Maker("SMCI", "Super Micro Computer", "超微电脑", "oem", "us", "SMCI", 6, cik=1375365),
    Maker("0992.HK", "Lenovo Group", "联想集团", "oem", "hk", "0992.HK", 3),
    Maker("PENG", "Penguin Solutions", "", "oem", "us", "PENG", 8, cik=1616533),
    Maker("CSCO", "Cisco", "思科", "oem", "us", "CSCO", 7, comparator=True, cik=858877),
    # Taiwan
    Maker("2382", "Quanta Computer", "广达", "odm", "tw", "2382.TW"),
    Maker("6669", "Wiwynn", "纬颖", "odm", "tw", "6669.TW"),
    Maker("3231", "Wistron", "纬创", "odm", "tw", "3231.TW"),
    Maker("2356", "Inventec", "英业达", "odm", "tw", "2356.TW"),
    Maker("2317", "Hon Hai (Foxconn)", "鸿海", "odm", "tw", "2317.TW"),
    Maker("2376", "Gigabyte", "技嘉", "odm", "tw", "2376.TW"),
    Maker("2357", "ASUS", "华硕", "odm", "tw", "2357.TW"),
    Maker("3706", "MiTAC Holdings", "神达", "odm", "tw", "3706.TW"),
    Maker("4938", "Pegatron", "和硕", "odm", "tw", "4938.TW", comparator=True),
    Maker("601138.SS", "Foxconn Industrial Internet", "工业富联", "odm", "cn", "601138.SS"),
    # contract manufacturers
    Maker("CLS", "Celestica", "天弘", "ems", "us", "CLS", cik=1030894),
    Maker("JBL", "Jabil", "捷普", "ems", "us", "JBL", 8, cik=898293),
    Maker("FLEX", "Flex", "伟创力", "ems", "us", "FLEX", 3, cik=866374),
    Maker("SANM", "Sanmina", "新美亚", "ems", "us", "SANM", 9, cik=897723),
    # China
    Maker("000977.SZ", "Inspur Electronic Information", "浪潮信息", "cn", "cn", "000977.SZ"),
    Maker("000938.SZ", "Unisplendour (H3C)", "紫光股份", "cn", "cn", "000938.SZ"),
    Maker("603019.SS", "Sugon", "中科曙光", "cn", "cn", "603019.SS"),
    Maker("603296.SS", "Huaqin Technology", "华勤技术", "cn", "cn", "603296.SS"),
    Maker("000066.SZ", "China Greatwall Technology", "中国长城", "cn", "cn", "000066.SZ", comparator=True),
)
BY_KEY = {m.key: m for m in MAKERS}
TW_CODES = tuple(m.key for m in MAKERS if m.market == "tw")
CN_CODES = tuple(m.key for m in MAKERS if m.market == "cn")

# Hyperscaler capex (Q7 target): cash purchases of property and equipment.
CAPEX_CIKS = {"MSFT": 789019, "GOOGL": 1652044, "AMZN": 1018724, "META": 1326801}
SEC_CIKS = {m.key: m.cik for m in MAKERS if m.cik} | CAPEX_CIKS

# ---- registered series (docs/prereg/ai-servers/2026-11-09_test_rules.md) ----------------------------------
# Wiwynn is inside Wistron's consolidation, so adding it separately would double count
Q1_ODM = ("2382", "3231", "2356")
Q7_SIGNAL = ("2382", "3231", "2356")
Q7_CAPEX = ("MSFT", "GOOGL", "AMZN", "META")
Q7_FIRST, Q7_LAST = "2019Q1", "2026Q3"
Q2_MEMBERS = ("DELL", "HPE", "SMCI", "0992.HK", "CLS", "2382", "6669", "3231", "2356", "2376", "601138.SS",
              "000977.SZ")
Q2_BEFORE_END = "2023-06"                       # trailing four quarters ending here (calendar placement)
Q5_EXCLUDE = tuple(m.key for m in MAKERS if m.comparator)

# ---- SEC extraction ---------------------------------------------------------------------------------------
FORMS = ("10-Q", "10-K")
MIN_REPORT_DATE = "2019-01-01"
SEC_CONCEPTS = (
    "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
    "CostOfRevenue", "CostOfGoodsAndServicesSold", "GrossProfit", "OperatingIncomeLoss",
    "InventoryNet", "AccountsReceivableNetCurrent",
    "PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
    "PaymentsForProceedsFromProductiveAssets", "NetCashProvidedByUsedInOperatingActivities",
)

# ---- Taiwan monthly revenue -------------------------------------------------------------------------------
TW_MONTHLY_FIRST = "2016-01"    # backfill start; the forecast interval is estimated on this history
MOPS_MONTHLY_URL = "https://mopsov.twse.com.tw/nas/t21/sii/t21sc03_{roc}_{m}_0.html"
TWSE_OPENAPI_MONTHLY = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
MOPS_QUARTERLY_URL = "https://mopsov.twse.com.tw/mops/web/{kind}"  # ajax_t163sb04 income, ajax_t163sb05 balance
TW_QUARTERLY_FIRST = "2019Q1"
