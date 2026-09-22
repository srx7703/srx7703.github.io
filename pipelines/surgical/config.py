"""Scope, pool and taxonomies for the soft-tissue surgical robot project.

The page answers one question — how many of these machines exist, how much work they do, and what the market pays
per machine — and it is built around a finding about the *sources* rather than about the market:

**Almost nobody discloses units.** Two listed companies publish an auditable unit series on a schedule: Intuitive
Surgical and Procept BioRobotics. Every diversified medtech that sells a soft-tissue robot — Medtronic with Hugo,
Johnson & Johnson with Ottava and Monarch — discloses nothing, and none of them reports surgical robotics as a
segment. Meanwhile Hong Kong and Shanghai listing rules force the Chinese entrants to publish unit tables that no
American filer produces. A reader who only reads US filings sees a market with one company in it.

That asymmetry is why :data:`DISCLOSURE_TIER` exists and why the pool is organised by it. A company's tier decides
which charts it may appear on, and the page says so rather than quietly leaving companies out of a pie.

**Scope is soft tissue only**, decided 2026-09-21. Orthopaedic robots (Mako, ROSA, CORI, ExcelsiusGPS, 天玑),
radiotherapy (CyberKnife), dental (Yomi) and vascular/EP (Stereotaxis, Corindus) are out. The cost of that line is
real and worth naming: it drops TINAVI 688277.SH, which was one of only four Chinese companies publishing a
mandatory annual production-and-sales table. Ion stays, labelled as endoluminal rather than laparoscopic, because
Intuitive reports it beside da Vinci and excluding it would understate the installed base of the one company whose
numbers everything else is divided by.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------------------------

#: What a robot has to do to be in this pool. Recorded here because the boundary is a judgement and the page
#: prints it: a reader who disagrees can see exactly what was excluded and why.
SCOPE_INCLUDES = (
    "laparoscopic multi-port",
    "laparoscopic single-port",
    "endoluminal and transluminal",
    "microsurgery",
    "soft-tissue resection",
)
SCOPE_EXCLUDES = {
    "orthopaedic": "operates on bone; a separate market with separate buyers, and outside China's licence regime",
    "radiotherapy": "delivers radiation rather than operating",
    "dental": "implant drilling, sold to dental practices",
    "vascular_ep": "catheter navigation; different specialty, different procedure codes",
    "neurosurgery_only": "stereotactic frames on bone landmarks",
}

# ---------------------------------------------------------------------------------------------
# Disclosure tiers — the page's organising idea
# ---------------------------------------------------------------------------------------------

DISCLOSURE_TIER = {
    "T1": "Publishes an auditable unit series every quarter",
    "T2": "Listing rules compel an annual or interim unit figure",
    "T3": "Has filed a prospectus containing a multi-year unit table",
    "T4": "Listed, sells a soft-tissue robot, discloses no units at all",
    "T5": "Private or dark: no unit data exists in any free source",
}
TIER_ORDER = ("T1", "T2", "T3", "T4", "T5")

#: Charts a tier is allowed on. A T4 company has no number to plot, and drawing it at zero would read as "sells
#: nothing" rather than "tells nobody"; it appears in the tables and in the disclosure scorecard instead.
TIER_CHARTABLE = {"T1": True, "T2": True, "T3": True, "T4": False, "T5": False}

# ---------------------------------------------------------------------------------------------
# Unit bases — the trap this project has to survive
# ---------------------------------------------------------------------------------------------

#: The companies in this pool count five different things and each calls its number "units". Intuitive *places*
#: systems, including under lease; Edge Medical reports systems "installed or delivered"; MedBot reports orders
#: separately from installs; Surgerii's prospectus reports production and sales as two different rows; TINAVI
#: reports production with inventory. Merging these into one column would be the single easiest way to publish a
#: false ranking, so every stored row carries its basis and the page never sums across bases.
UNIT_BASIS = {
    "installed_base": "systems in the field at period end, as the company counts them",
    "placements": "systems placed in the period, including leases and usage-based arrangements",
    "units_sold": "systems whose title transferred in the period",
    "installed_or_delivered": "the company's own combined wording; neither pure installs nor pure shipments",
    "orders": "contracted but not yet installed",
    "production": "systems manufactured in the period, from a mandatory A-share production-and-sales table",
    "procedures": "operations performed, not machines",
    "patients": "people treated; cumulative since inception unless stated",
}

#: Bases that may share a y-axis. Anything else goes on its own chart with its own label.
COMPARABLE_BASES = ("installed_base",)

#: The quarter Intuitive redefined its headline procedure metric. Through 2025-Q2 the release says
#: "Worldwide da Vinci procedures grew approximately N%"; from 2025-Q3 it says "Worldwide procedures
#: (da Vinci and Ion combined) grew approximately N%". A growth series stitched across this boundary compares
#: two different populations, so the page breaks the line here rather than drawing through it.
PROCEDURE_DEFINITION_BREAK = "2025-Q3"
PROCEDURE_BREAK_NOTE = (
    "From 2025-Q3 Intuitive's headline procedure growth combines da Vinci and Ion; before that it was da Vinci "
    "alone. The two are different populations and the series is not continuous across that quarter."
)

#: Placements and the change in installed base are different quantities and neither can be derived from the
#: other. Over 2023-2026 roughly a fifth to a third of each quarter's placements were absorbed by retirements
#: and trade-ins that no filing discloses: in 2026-Q1 the base grew 289 while 431 systems were placed.
PLACEMENTS_NOT_DELTA_NOTE = (
    "A quarter's placements exceed the change in installed base, because retirements and trade-ins are netted "
    "out and never disclosed. Neither series may be derived from the other."
)

#: Stock metrics are a level at an instant; flow metrics are an amount over a span. The distinction decides
#: whether two differently-labelled periods describe the same fact: for an installed base, "2024" and
#: "2024-Q4" and "2024-12-31" are all end-2024 and are directly comparable, while for procedures a year and a
#: quarter are different quantities and comparing them would understate the year by about four.
STOCK_METRICS = ("installed_base", "orders")
FLOW_METRICS = ("placements", "procedures", "production", "units_sold", "installed_or_delivered", "patients")


def period_instant(metric: str, period: str) -> str:
    """The comparison key for one period. Stocks collapse to their year; flows keep their exact span."""
    if metric in STOCK_METRICS:
        return period[:4]
    return period

# ---------------------------------------------------------------------------------------------
# Placement model — why a "shipment" is ambiguous
# ---------------------------------------------------------------------------------------------

PLACEMENT_MODEL = {
    "sale": "outright purchase, title transfers",
    "lease": "operating lease, the vendor keeps the asset",
    "usage": "per-procedure or subscription pricing",
    "mixed": "the company places under more than one model and does not split them",
    "unknown": "not disclosed",
}


@dataclass(frozen=True)
class Robot:
    """One product, because the unit data is per product and the company data is per issuer."""

    product: str
    company: str
    ticker: str = ""          # empty for private companies
    category: str = "laparoscopic_multiport"
    region: str = "us"        # home market of the maker
    approvals: tuple[str, ...] = ()   # regulator codes seen; filled by registry.py, not by hand


@dataclass(frozen=True)
class Maker:
    ticker: str               # Yahoo symbol, or a slug prefixed "private:" for unlisted makers
    name: str
    name_cn: str = ""
    tier: str = "T5"
    region: str = "us"        # home market of the maker, which is also where its disclosure regime comes from
    listed: bool = True
    pure_play: bool = False   # is soft-tissue robotics substantially the whole business?
    products: tuple[str, ...] = ()
    note: str = ""
    status: str = ""          # "suspended", "delisted", "dissolved" — printed on the page, never silently dropped
    #: The world a maker's own unit figures cover. This is a property of its disclosure regime, not of any one
    #: row, and it is declared rather than sniffed out of wording: Intuitive reports worldwide and Procept
    #: reports the United States only, every quarter, and a row that silently inherited the wrong one would set
    #: a US installed base against a worldwide procedure count and invent a utilisation figure.
    reporting_geography: str = "global"
    unit_bases: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------------------------
# The pool
# ---------------------------------------------------------------------------------------------

MAKERS: tuple[Maker, ...] = (
    # --- T1: an auditable series, quarterly ---------------------------------------------------
    Maker("ISRG", "Intuitive Surgical", tier="T1", pure_play=True,
          products=("da Vinci", "Ion"),
          unit_bases=("installed_base", "placements", "procedures"),
          note="single reportable segment, so revenue is entirely robotics; the only vendor whose installed base "
               "is audited. Places systems under lease and usage-based terms as well as sale and does not split "
               "them, so 'placed' is not 'sold'."),
    Maker("PRCT", "Procept BioRobotics", tier="T1", pure_play=True,
          products=("HYDROS", "AquaBeam"), reporting_geography="us",
          unit_bases=("installed_base", "placements", "procedures"),
          note="soft-tissue resection in urology. Quarterly figures are US-only; a global count circulates that "
               "is not in the release."),

    # --- T2: compelled by listing rules -------------------------------------------------------
    Maker("2675.HK", "Edge Medical", region="cn", name_cn="精锋医疗", tier="T2", pure_play=True,
          products=("MP1000", "MP2000"),
          unit_bases=("installed_or_delivered",),
          note="listed 2026-01-08 under HKEX Chapter 18A as a pre-profit '-B' stock, which is itself a declaration "
               "that it fails the profit test, so no PE exists by construction."),
    Maker("2252.HK", "MicroPort MedBot", region="cn", name_cn="微创医疗机器人", tier="T2", pure_play=True,
          products=("Toumai",), unit_bases=("installed_base", "orders"),
          status="suspended",
          note="suspended from trading since 2026-09-01 over an unconfirmed counterparty relationship in a lease; "
               "2026 interim results unpublished. Price, market cap and enterprise value are frozen at 2026-08-31 "
               "and there are no current financials. Shown on the page as suspended, never silently omitted."),
    Maker("0853.HK", "MicroPort Scientific", region="cn", name_cn="微创医疗", tier="T4", listed=True,
          products=("Toumai (via 2252.HK)",), status="suspended",
          note="parent of MedBot; suspended alongside it on 2026-09-01. Carries no robot units of its own."),

    # --- T3: a prospectus with a multi-year unit table ----------------------------------------
    Maker("private:surgerii", "Surgerii", region="cn", name_cn="术锐", tier="T3", listed=False, pure_play=True,
          products=("Surgerii single-port",), unit_bases=("production", "units_sold"),
          note="STAR Market application accepted. Its prospectus prints a production-and-sales table, which is the "
               "most granular unit disclosure by any company in this pool."),
    Maker("private:sizhirui", "Sizhirui", region="cn", name_cn="思哲睿", tier="T3", listed=False, pure_play=True,
          products=("Kangduo",),
          note="prospectus located but its industry chapter was not obtained as a free primary document."),

    # --- T4: listed, sells one, says nothing --------------------------------------------------
    Maker("MDT", "Medtronic", tier="T4", products=("Hugo",),
          note="Hugo sits inside Medical Surgical with non-robot products; no installed base, placements or "
               "procedures in any filing. Circulating unit targets trace to call summaries, not to primary sources."),
    Maker("JNJ", "Johnson & Johnson", tier="T4", products=("Ottava", "Monarch"),
          note="Ottava and Monarch sit inside MedTech; no unit disclosure of any kind."),

    # --- T5: private or dark ------------------------------------------------------------------
    Maker("private:cmr", "CMR Surgical", region="uk", tier="T5", listed=False, pure_play=True, products=("Versius",),
          note="the only private maker ever to have published an installed-system count, and that figure is from "
               "November 2022. Reports 'patients treated', which is not an installed base."),
    Maker("private:distalmotion", "Distalmotion", region="ch", tier="T5", listed=False, pure_play=True,
          products=("Dexter",)),
    Maker("private:moon", "Moon Surgical", region="fr", tier="T5", listed=False, pure_play=True,
          products=("Maestro",)),
    Maker("private:mmi", "Medical Microinstruments", region="it", tier="T5", listed=False, pure_play=True,
          products=("Symani",),
          note="microsurgery rather than laparoscopy: it anastomoses vessels under a microscope, so its procedure "
               "counts share no denominator with the laparoscopic pool."),
    Maker("private:momentis", "Momentis Surgical", tier="T5", listed=False, pure_play=True,
          products=("Anovo",),
          note="transvaginal; surfaced only through its FDA clearance and named in no market commentary found."),
    Maker("private:virtualincision", "Virtual Incision", tier="T5", listed=False, pure_play=True,
          products=("MIRA",)),
    Maker("private:medicaroid", "Medicaroid", region="jp", tier="T5", listed=False, pure_play=True,
          products=("hinotori",),
          note="joint venture of Kawasaki Heavy Industries and Sysmex; neither parent breaks it out."),
    Maker("private:karlstorz", "Karl Storz", region="de", tier="T5", listed=False, products=("Senhance", "LUNA"),
          note="acquired Asensus Surgical on 2024-08-22 at USD 0.35 a share. Privately held in Germany, so the "
               "Senhance series went dark at that date and cannot be tracked further."),
    Maker("private:cornerstone", "Cornerstone Robotics", region="cn", name_cn="康诺思腾", tier="T5",
          listed=False, pure_play=True),
    Maker("private:ronovo", "Ronovo Surgical", region="cn", name_cn="瑞龙外科", tier="T5",
          listed=False, pure_play=True),
    Maker("private:toodo", "Toodo Medical", region="cn", name_cn="佗道医疗", tier="T5",
          listed=False, pure_play=True),

    # --- Listed elsewhere, kept because they ship a soft-tissue system ------------------------
    Maker("058110.KQ", "meerecompany", tier="T4", region="kr", products=("Revo-i",),
          note="KOSDAQ-listed operating company, not a private venture as it is often described."),
)

#: Companies removed from the pool, with the reason, so a reader can see the boundary rather than guess at it.
EXCLUDED = (
    {"name": "TINAVI", "name_cn": "天智航", "ticker": "688277.SH", "why": "orthopaedic only; out of scope. Its "
     "mandatory A-share production-and-sales table was one of the richest unit disclosures available, so this "
     "exclusion has a real cost and is recorded rather than hidden."},
    {"name": "Stryker", "ticker": "SYK", "why": "Mako is orthopaedic"},
    {"name": "Zimmer Biomet", "ticker": "ZBH", "why": "ROSA is orthopaedic"},
    {"name": "Smith+Nephew", "ticker": "SNN", "why": "CORI is orthopaedic"},
    {"name": "Globus Medical", "ticker": "GMED", "why": "ExcelsiusGPS is spine"},
    {"name": "Accuray", "ticker": "ARAY", "why": "radiotherapy, not surgery"},
    {"name": "Stereotaxis", "ticker": "STXS", "why": "cardiac electrophysiology catheter navigation"},
    {"name": "Neocis", "why": "dental implant drilling"},
    {"name": "Asensus Surgical", "ticker": "ASXC", "why": "delisted 2024-08-22 into Karl Storz; tracked there"},
    {"name": "Monogram Technologies", "ticker": "MGRM", "why": "orthopaedic, and delisted 2025-10-07 into Zimmer"},
    {"name": "Vicarious Surgical", "ticker": "RBOT", "why": "assigned its assets for the benefit of creditors and "
     "filed for dissolution on 2026-07-21, with stockholders expected to receive nothing. Not a going concern."},
)

# ---------------------------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------------------------

#: openFDA needs no key. The interesting structural fact is that the FDA created four new regulations for the
#: modern soft-tissue entrants between 2024 and 2026 while Intuitive stayed in the legacy code, so the competitive
#: set splits cleanly across old and new product codes.
OPENFDA_BASE = "https://api.fda.gov/device"
FDA_PRODUCT_CODES = {
    "NAY": "legacy endoscopic surgical instrumentation; almost entirely Intuitive",
    "SAQ": "new regulation created 2024-2026 for a modern soft-tissue entrant",
    "SCV": "new regulation created 2024-2026 for a modern soft-tissue entrant",
    "SDD": "new regulation created 2024-2026 for a modern soft-tissue entrant",
    "SIR": "new regulation created 2024-2026 for a modern soft-tissue entrant",
    "EOQ": "bronchoscope-based navigation; Ion, Monarch and Galaxy",
}
OPENFDA_RATE_PER_MIN = 240
OPENFDA_RATE_PER_DAY = 1000

#: China's licence regime bounds the installed base of laparoscopic systems and nothing else. It carries no brand,
#: model or price field, so it can state a denominator and can never state a share.
NHC_QUOTA_DOC = "国卫财务发〔2023〕18号"
NHC_QUOTA_TOTAL = 819          # nationally permitted by end-2025
NHC_QUOTA_NEW = 559            # of which newly added during the 14th Five-Year Plan
NHC_QUOTA_EXPIRES = "2025-12-31"
NHC_QUOTA_SUCCESSOR = None     # no 15th Five-Year Plan equivalent published as of 2026-09-21

#: Tender awards. Title-only search, no API, an aggressive rate limiter and a WAF that refuses non-browser agents,
#: so this is a hand-curated price panel and never a shipment series. The terms below are a union because no
#: single one finds much: procurement officers name these machines inconsistently.
CCGP_SEARCH = "https://search.ccgp.gov.cn/bxsearch"
CCGP_TERMS = ("手术机器人", "腔镜手术机器人", "内窥镜手术器械控制系统", "腹腔内窥镜手术系统",
              "单孔手术机器人", "达芬奇", "图迈", "精锋", "术锐", "康诺思腾")

#: A curated row unchecked for longer than this is greyed on the page rather than dropped.
REFERENCE_STALE_DAYS = 90
