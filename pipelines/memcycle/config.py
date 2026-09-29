"""Registered constants, roster and sources for the memory-chip price-cycle case study.

Every constant here is written out in section G of ``docs/EVALUATION_PLAN.md`` and, before that, in the Chinese
pre-registration ``docs/prereg/memory-cycles/预注册_周期定时与检验规则_20260928.md``. A constant that lives only
in code sits outside the blob hash the page prints, so ``tests/test_plan.py`` fails if one of them is missing
from the plan or disagrees with it. Change a value here only together with an amendment in both documents.
"""

from __future__ import annotations

from dataclasses import dataclass

from pipelines.common.storage import DATA_DIR, FACTS_DIR, MARTS_DIR, RAW_DIR, REPO_ROOT, SNAP_DIR

SLUG = "memcycle"

# ---------------------------------------------------------------------------------------------
# Price series (prereg §1)
# ---------------------------------------------------------------------------------------------

ECOS_TABLE = "402Y016"  # 수출물가지수(품목별): Korean export price index by item, monthly
DRAM_ITEM = "30911201AA"
FLASH_ITEM = "30911202AA"
PRODUCT = {DRAM_ITEM: "DRAM", FLASH_ITEM: "NAND"}  # "NAND" is the research code's key for the flash item
MAIN_BASIS = "C"  # 계약통화기준, contract currency
SENS_BASES = ("D", "W")  # USD and KRW bases, sensitivity only
BASIS_NAME = {"C": "contract currency", "D": "US dollar", "W": "Korean won"}
SERIES_START = {DRAM_ITEM: "1995-01", FLASH_ITEM: "2000-01"}
FLASH_NOR_UNTIL = "2006-01"  # before about this month the flash item is mostly NOR (shown greyed)

# ---------------------------------------------------------------------------------------------
# Turning-point rule (prereg §1): simplified Bry-Boschan on log levels, no smoothing
# ---------------------------------------------------------------------------------------------

HALF_WINDOW = 6  # months either side of a candidate
MIN_PHASE = 6  # months, peak to trough and trough to peak
MIN_CYCLE = 15  # months, peak to peak and trough to trough
AMP_MAIN = 0.20  # every rise at least +20%, every fall at least -20%
AMP_SENS = (0.30, 0.0)  # 0% is the pure duration rule

# ---------------------------------------------------------------------------------------------
# Q3: do stocks peak before prices? (prereg §2 and implementation notes 4-6)
# ---------------------------------------------------------------------------------------------

LEAD_MIN = 1  # a company-cycle "leads" when the stock peak is at least this many months before the price peak
Q3_HOLDS_SHARE = 0.60  # holds: at least this share lead ...
Q3_MEDIAN_BAND = (3, 9)  # ... and the median lead is inside this band, in months
Q3_FALSIFY_SHARE = 0.50  # falsified: fewer than this share lead
INFINEON_LAST_MONTH = "2006-04"  # Infineon counts as a memory company until the Qimonda carve-out
DATA_END = "2026-08"  # last ECOS month at registration; a company whose last close is earlier has delisted
REGISTRATION_ECOS_RUN = "2026-09-28/1316"  # the raw partition the frozen results were computed against

# ---------------------------------------------------------------------------------------------
# Q6 / Q7 / Q8 (prereg §4-6): registered, not yet scored
# ---------------------------------------------------------------------------------------------

Q6_PERCENTILES = tuple(range(50, 91, 5))  # candidate thresholds, percentiles of the training-period ratio
Q6_TRAIN_END = "2015Q4"
Q6_TEST_START = "2016Q1"
Q6_HORIZON_QUARTERS = (4, 8)  # a DRAM price peak 4 to 8 quarters after quarter q
Q6_PRECISION_MARGIN = 0.10  # falsified if out-of-sample precision <= base rate + 10 pp ...
Q6_MIN_RECALL = 0.50  # ... or out-of-sample recall < 50%
Q7_SPLIT_YEAR = 2013  # Micron's acquisition of Elpida
Q8_CONFIRM_MONTHS = 6  # a new peak is confirmed after 6 months below it ...
Q8_CONFIRM_FALL = 0.20  # ... and a cumulative fall of 20%
Q8_WINDOW_MONTHS = 12  # both Q8 measures look at the 12 months after the peak
Q8_DEADLINE = "2028-12-31"

# ---------------------------------------------------------------------------------------------
# Pre-registration file and its five blob ids (the history the page prints)
# ---------------------------------------------------------------------------------------------

PREREG_PATH = REPO_ROOT / "docs" / "prereg" / "memory-cycles" / "预注册_周期定时与检验规则_20260928.md"
PREREG_BLOBS = (
    ("9f90587a", "2026-09-28 13:11 -07:00", "rules and Q3/Q6/Q7/Q8 criteria"),
    ("4b4a9e94", "2026-09-28 14:59 -07:00", "implementation notes 1-3"),
    ("77fdbb45", "2026-09-28 15:00 -07:00", "implementation notes 4-6, Q3 sample boundaries"),
    ("e4a15ecd", "2026-09-28 17:33 -07:00", "corrections after the independent rule review"),
    ("bedd0de4", "2026-09-28 18:02 -07:00", "Q3 roster as registered"),
)

# ---------------------------------------------------------------------------------------------
# Companies (prereg §2, implementation notes 4-6, correction 4)
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Company:
    key: str
    label: str
    local_file: str  # relative to MEMCYCLE_INPUTS; the closes never enter this repo
    market: str
    currency: str
    group: str  # pure | diversified | unregistered
    dram_maker: bool  # enters the "DRAM makers only" sensitivity
    nand_panel: bool  # scored against the flash calendar, shown only


COMPANIES: tuple[Company, ...] = (
    Company("MU", "Micron", "us_eu_jp/MU_monthly.csv", "US", "USD", "pure", True, True),
    Company("000660", "SK hynix / Hynix / Hyundai Elec.", "kr/000660_monthly_official.csv", "KR", "KRW", "pure",
            True, True),
    Company("2408", "Nanya Technology", "tw/2408_monthly.csv", "TW", "TWD", "pure", True, False),
    Company("2344", "Winbond", "tw/2344_monthly.csv", "TW", "TWD", "pure", True, False),
    Company("5346", "Powerchip (PSC)", "tw/5346_monthly.csv", "TW", "TWD", "pure", True, False),
    Company("6770", "Powerchip (PSMC)", "tw/6770_monthly.csv", "TW", "TWD", "pure", True, False),
    Company("5387", "ProMOS (not on the registered list; shown only)", "tw/5387_monthly.csv", "TW", "TWD",
            "unregistered", False, False),
    Company("3474", "Inotera", "tw/3474_monthly.csv", "TW", "TWD", "pure", True, False),
    Company("ELPIDA", "Elpida", "delisted/elpida_full_monthly.csv", "JP", "JPY", "pure", True, False),
    Company("QI", "Qimonda", "delisted/qimonda_merged_monthly.csv", "DE", "USD", "pure", True, False),
    Company("SNDK_OLD", "SanDisk (old, to 2016)", "delisted/sandisk_old_monthly.csv", "US", "USD", "pure", False, True),
    Company("SNDK", "SanDisk (new, 2025-)", "us_eu_jp/SNDK_monthly.csv", "US", "USD", "pure", False, True),
    Company("285A", "Kioxia", "us_eu_jp/285A_T_monthly.csv", "JP", "JPY", "pure", False, True),
    Company("005930", "Samsung Electronics", "kr/005930_monthly_official.csv", "KR", "KRW", "diversified", False, True),
    Company("IFX", "Infineon (to 2006 spin-off)", "us_eu_jp/IFX_DE_monthly.csv", "DE", "EUR", "diversified", False,
            False),
    Company("WDC", "Western Digital", "us_eu_jp/WDC_monthly_spinoff_adjusted.csv", "US", "USD", "diversified", False,
            False),
    # registered (diversified + NAND panel) but no clean history: Yahoo dropped 6502.T after the Dec-2023
    # take-private. Kept so it is reported as "registered, no data" instead of vanishing.
    Company("6502", "Toshiba", "us_eu_jp/6502_T_monthly.csv", "JP", "JPY", "diversified", False, True),
    Company("STX", "Seagate", "us_eu_jp/STX_monthly.csv", "US", "USD", "diversified", False, False),
)
LAST_USE = {"IFX": INFINEON_LAST_MONTH}
NO_DATA = ("6502",)  # registered companies with no clean history at registration

# market -> (label, local file); the first entry is the primary benchmark (prereg §3)
BENCH: dict[str, tuple[tuple[str, str], ...]] = {
    "US": (("SOX", "us_eu_jp/IDX_SOX_monthly.csv"), ("Nasdaq", "us_eu_jp/IDX_IXIC_monthly.csv")),
    "KR": (("KOSPI", "kr/KOSPI_monthly.csv"),),
    "TW": (("TAIEX", "tw/TAIEX_monthly.csv"),),
    "JP": (("Nikkei 225", "us_eu_jp/IDX_N225_monthly.csv"),),
    "DE": (("SOX", "us_eu_jp/IDX_SOX_monthly.csv"), ("DAX", "us_eu_jp/IDX_GDAXI_monthly.csv")),
}
# currency -> (FRED file, quoted as USD per unit)
FX: dict[str, tuple[str, bool]] = {
    "KRW": ("fred_DEXKOUS.csv", False),
    "TWD": ("fred_DEXTAUS.csv", False),
    "JPY": ("fred_DEXJPUS.csv", False),
    "EUR": ("fred_DEXUSEU.csv", True),
}
# Local-only inputs read by the input fixes in freeze.py (listed in the manifest as well)
FIX_INPUTS = (
    "delisted/elpida_quarterly_hl.csv",
    "delisted/qimonda_quarterly_hl.csv",
    "delisted/elpida_monthend_close_partial.csv",
    "delisted/qimonda_monthend_close_partial.csv",
    "delisted_verify/elpida_monthly_from_kabudragon.csv",
    "kr/000660_monthly.csv",
    "kr/005930_monthly.csv",
    "kr/005935_monthly.csv",
    "kr/000660_yahoo_monthly.csv",
    "kr/005930_yahoo_monthly.csv",
    "kr/005935_yahoo_monthly.csv",
)
KRX_OFFICIAL_FROM = "2026-09"  # KRX after-market launched 2026-09-14; from this month take Yahoo's official close

# ---------------------------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------------------------

RAW_ECOS_DIR = RAW_DIR / SLUG / "ecos"
SNAP_PATH = SNAP_DIR / SLUG / "ecos_402Y016.parquet"
CASE_DIR = DATA_DIR / "case_studies" / SLUG
FROZEN_PATH = CASE_DIR / "frozen" / "company_cycles_derived.csv"
MANIFEST_PATH = CASE_DIR / "inputs_manifest.json"
Q3_VERSIONS_PATH = CASE_DIR / "q3_versions.json"
MART_DIR = MARTS_DIR / SLUG
FACTS_PATH = FACTS_DIR / "memcycle.json"
# stock-side columns that are close levels; the frozen export drops them (vendor terms, public repo)
CLOSE_COLUMNS = ("stock_peak_close", "stock_trough_close", "upleg_low_close")

SOURCES = [
    {"name": "Bank of Korea ECOS, table 402Y016 (export price index by item). Source: Bank of Korea ECOS",
     "url": "https://ecos.bok.or.kr/"},
    {"name": "Stock-side results derived locally from exchange and vendor month-end closes (not redistributed)",
     "url": "https://github.com/srx7703/srx7703.github.io/blob/main/data/case_studies/memcycle/inputs_manifest.json"},
]
VIDEO = {
    "channel": "美投讲美股",
    "title": "存储还能涨吗？历史上三轮暴涨…",
    "date": "2026-09-26",
    "url": "https://www.youtube.com/watch?v=viQjQ3mgeOc",
}
