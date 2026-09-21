"""Company pool and per-company data-source routing for the valuation project.

One row per *listing*, not per issuer: a dual-listed company (CATL A + H) appears twice, because the
whole point of the page is that the same earnings get priced differently. Dual listings point their
fundamentals at the primary line via ``fundamentals_from`` so the TTM numbers stay identical.

Purity labels decide how a row is drawn and what it may be used for:
  high    the track is the company's main business
  main    a large business in the track, but the track-specific product is ~0% of revenue today
          (battery makers with a solid-state roadmap)
  partial minor or indirect exposure (materials, equipment, diversified giants)

``segment_line`` records whether the company publicly discloses a revenue line specific to the
track. It is filled from docs-backed research, never guessed — see ``data/reference/share_*.json``
for the evidence.

``share_basis`` decides how a company enters the computed market-share pool, and it is a *separate*
question from ``purity``:

  total    the company's whole revenue is revenue for this track, so total revenue is used. An
           optical specialist, or a cell maker on the battery page.
  segment  the track is one business inside a larger company, so a disclosed segment figure from
           ``data/reference/share_*.json`` is required. Without one the company is excluded and
           listed as excluded, because a diversified group's total revenue would put a network
           equipment vendor above every module specialist and say nothing about modules.
  none     the company is not a competitor in this market at all, and ``share_note`` says why. This
           covers two cases. One layer up or down: chip and materials suppliers sell *into* the
           vendors, so their revenue is already inside their customers' revenue and counting both
           double-counts the value chain. And contract manufacturers: Fabrinet builds modules that
           its customers then book as their own revenue.

Purity answers "how much of this company is the story"; share_basis answers "whose revenue competes
with whose". A cell maker with a solid-state roadmap is ``purity="main"`` (solid-state is ~0% of it)
but ``share_basis="total"`` (it is a battery company competing with other battery companies).
"""

from __future__ import annotations

from dataclasses import dataclass

# markets → how fundamentals are sourced
MARKET_FUNDAMENTALS = {
    "cn": "eastmoney",  # A-share: East Money quarterly report table (cumulative, differenced)
    "us": "sec",  # SEC XBRL companyfacts
    "hk": "yahoo",
    "jp": "yahoo",
    "kr": "yahoo",
    "tw": "yahoo",
    "uk": "yahoo",
    "de": "yahoo",
    "fr": "yahoo",
    "ch": "yahoo",
}


@dataclass(frozen=True)
class Company:
    ticker: str  # canonical id = Yahoo symbol
    name: str  # English display name
    track: str  # "optical" | "ssb"
    purity: str  # "high" | "main" | "partial"
    market: str  # key of MARKET_FUNDAMENTALS
    fy_end_month: int  # fiscal year end month (12 = December)
    name_cn: str = ""
    em_code: str = ""  # East Money code, e.g. "SZ300308" (A-shares only)
    estimates: str = "yahoo"  # "eastmoney" | "yahoo"
    fundamentals_from: str = ""  # ticker of the listing that owns the fundamentals (dual listings)
    segment_line: str = "unknown"  # "yes" | "bundled" | "no" | "unknown"
    share_basis: str = "segment"  # "total" | "segment" | "none" — see the module docstring
    share_note: str = ""  # why a "none" company is not a competitor in this market
    path: str = ""  # supply path code for the power track (see PATHS); empty for other tracks
    note: str = ""

    @property
    def fundamentals_source(self) -> str:
        return MARKET_FUNDAMENTALS[self.market]

    @property
    def primary(self) -> str:
        return self.fundamentals_from or self.ticker


def _cn(ticker: str, name: str, name_cn: str, track: str, purity: str, **kw) -> Company:
    em_prefix = "SZ" if ticker.endswith(".SZ") else "SH"
    return Company(
        ticker=ticker,
        name=name,
        name_cn=name_cn,
        track=track,
        purity=purity,
        market="cn",
        fy_end_month=12,
        em_code=f"{em_prefix}{ticker.split('.')[0]}",
        estimates="eastmoney",
        **kw,
    )


OPTICAL: list[Company] = [
    # --- China A-share, optical modules / components as the main business ---
    _cn("300308.SZ", "Innolight", "中际旭创", "optical", "high", share_basis="total"),
    _cn("300502.SZ", "Eoptolink", "新易盛", "optical", "high", share_basis="total"),
    _cn("300394.SZ", "TFC Optical Communication", "天孚通信", "optical", "high", share_basis="total"),
    _cn("002281.SZ", "Accelink", "光迅科技", "optical", "high", share_basis="total"),
    _cn("688498.SS", "Yuanjie Semiconductor", "源杰科技", "optical", "high", note="optical chips (DFB/EML)",
        share_basis="none", share_note="sells laser chips into modules, one layer down"),
    _cn("603083.SS", "CIG Shanghai", "剑桥科技", "optical", "high", share_basis="total"),
    _cn("301205.SZ", "Linktel Technologies", "联特科技", "optical", "high", share_basis="total"),
    _cn("300570.SZ", "T&S Communications", "太辰光", "optical", "high", share_basis="total"),
    _cn("688205.SS", "Taclink Optoelectronics", "德科立", "optical", "high", share_basis="total"),
    _cn("688313.SS", "Shijia Photons", "仕佳光子", "optical", "high", share_basis="total"),
    # --- China A-share, partial exposure ---
    _cn("000988.SZ", "HGTech", "华工科技", "optical", "partial", note="laser equipment + optical modules"),
    _cn("688048.SS", "Everbright Photonics", "长光华芯", "optical", "partial", note="laser chips",
        share_basis="none", share_note="sells laser chips, one layer down from modules"),
    _cn("300620.SZ", "Advanced Fiber Resources", "光库科技", "optical", "partial",
         note="fibre devices, thin-film lithium niobate"),
    _cn("600060.SS", "Hisense Visual", "海信视像", "optical", "partial", note="TVs; Hisense Broadband is an affiliate"),
    _cn("601869.SS", "YOFC", "长飞光纤", "optical", "partial", note="fibre and cable"),
    Company(
        "6869.HK", "YOFC (H)", "optical", "partial", "hk", 12,
        name_cn="长飞光纤H", fundamentals_from="601869.SS", note="H line of 601869.SS",
    ),
    # --- United States ---
    Company("COHR", "Coherent", "optical", "high", "us", 6, share_basis="segment", segment_line="yes",
            note="transceivers sit inside Datacenter & Communications, beside Lasers and Materials"),
    Company("LITE", "Lumentum", "optical", "high", "us", 6, share_basis="segment", segment_line="yes",
            note="Cloud & Networking sits beside the Industrial Tech business"),
    Company("AAOI", "Applied Optoelectronics", "optical", "high", "us", 12, share_basis="total"),
    Company("FN", "Fabrinet", "optical", "high", "us", 6, note="contract manufacturer",
            share_basis="none", share_note="builds modules its customers book as their own revenue"),
    Company("MTSI", "MACOM", "optical", "partial", "us", 9, note="fiscal year ends early October",
            share_basis="none",
            share_note="sells lasers, drivers and TIAs into module vendors, one layer down"),
    Company("SMTC", "Semtech", "optical", "high", "us", 1, note="fiscal year ends late January",
            share_basis="none", share_note="sells signal-conditioning chips into modules, one layer down"),
    Company("CRDO", "Credo", "optical", "high", "us", 4, note="fiscal year ends end-April",
            share_basis="none", share_note="sells DSPs and active cables, one layer down from modules"),
    Company("AVGO", "Broadcom", "optical", "partial", "us", 10, note="fiscal year ends early November",
        segment_line="no"),
    Company("MRVL", "Marvell", "optical", "partial", "us", 1),
    Company("CIEN", "Ciena", "optical", "partial", "us", 10, segment_line="yes"),
    Company("CSCO", "Cisco", "optical", "partial", "us", 7),
    Company("JBL", "Jabil", "optical", "partial", "us", 8),
    # --- Japan ---
    Company("5802.T", "Sumitomo Electric", "optical", "partial", "jp", 3),
    Company("5801.T", "Furukawa Electric", "optical", "partial", "jp", 3),
    Company("5803.T", "Fujikura", "optical", "partial", "jp", 3, segment_line="yes"),
    # --- Taiwan ---
    Company("4979.TWO", "Luxnet", "optical", "high", "tw", 12, name_cn="光环科技",
            note="TPEx listing, not TWSE", share_basis="total"),
    Company("3081.TWO", "LandMark Optoelectronics", "optical", "high", "tw", 12, name_cn="联亚光电",
            share_basis="total"),
    Company("4977.TW", "PCL Technologies", "optical", "high", "tw", 12, name_cn="眾達光電",
            share_basis="total"),
]

SSB: list[Company] = [
    # --- pure solid-state / semi-solid plays: all pre-profit, PE is not meaningful ---
    Company("QS", "QuantumScape", "ssb", "high", "us", 12, share_basis="total"),
    Company("SLDP", "Solid Power", "ssb", "high", "us", 12, share_basis="total"),
    Company("SES", "SES AI", "ssb", "high", "us", 12, share_basis="total"),
    Company("IKA.L", "Ilika", "ssb", "high", "uk", 4, note="price quoted in pence, financials in pounds",
            share_basis="total"),
    Company("MVST", "Microvast", "ssb", "high", "us", 12, note="semi-solid", share_basis="total"),
    # --- battery makers with a solid-state roadmap; solid-state revenue ~0 today ---
    _cn("300750.SZ", "CATL", "宁德时代", "ssb", "main", share_basis="total"),
    Company(
        "3750.HK", "CATL (H)", "ssb", "main", "hk", 12,
        name_cn="宁德时代H", fundamentals_from="300750.SZ", note="H line of 300750.SZ",
    ),
    _cn("002594.SZ", "BYD", "比亚迪", "ssb", "main", note="autos + batteries"),
    Company(
        "1211.HK", "BYD (H)", "ssb", "main", "hk", 12,
        name_cn="比亚迪H", fundamentals_from="002594.SZ", note="H line of 002594.SZ",
    ),
    _cn("300014.SZ", "EVE Energy", "亿纬锂能", "ssb", "main", share_basis="total"),
    _cn("002074.SZ", "Gotion High-tech", "国轩高科", "ssb", "main", share_basis="total"),
    _cn("300207.SZ", "Sunwoda", "欣旺达", "ssb", "main", share_basis="total"),
    Company("3931.HK", "CALB", "ssb", "main", "hk", 12, name_cn="中创新航", share_basis="total"),
    _cn("688567.SS", "Farasis Energy", "孚能科技", "ssb", "main", share_basis="total"),
    _cn("300438.SZ", "Great Power Energy", "鹏辉能源", "ssb", "main", share_basis="total"),
    Company("006400.KS", "Samsung SDI", "ssb", "main", "kr", 12),
    Company("373220.KS", "LG Energy Solution", "ssb", "main", "kr", 12, share_basis="total"),
    Company("6752.T", "Panasonic Holdings", "ssb", "main", "jp", 3),
    Company("7203.T", "Toyota Motor", "ssb", "partial", "jp", 3, note="solid-state developer, not a cell vendor",
            share_basis="none", share_note="buys cells rather than selling them"),
    # --- materials, electrolytes and equipment ---
    _cn("002460.SZ", "Ganfeng Lithium", "赣锋锂业", "ssb", "partial", share_basis="none",
        share_note="sells lithium chemicals into cell makers, one layer up the chain"),
    _cn("300073.SZ", "Easpring Material", "当升科技", "ssb", "partial", share_basis="none",
        share_note="sells cathode material into cell makers"),
    _cn("688005.SS", "Ronbay Technology", "容百科技", "ssb", "partial", share_basis="none",
        share_note="sells cathode material into cell makers"),
    _cn("603200.SS", "Shanghai Xibao", "上海洗霸", "ssb", "partial", note="solid electrolyte venture",
        share_basis="none",
        share_note="sells solid electrolyte material, not cells"),
    _cn("603663.SS", "Sanxiang Advanced Materials", "三祥新材", "ssb", "partial", share_basis="none",
        share_note="sells zirconium materials into the chain, not cells"),
    _cn("002709.SZ", "Tinci Materials", "天赐材料", "ssb", "partial", share_basis="none",
        share_note="sells electrolyte and additives into cell makers"),
    _cn("600206.SS", "Grinm Advanced Materials", "有研新材", "ssb", "partial", share_basis="none",
        share_note="sells target and electrolyte materials, not cells"),
    _cn("300450.SZ", "Wuxi Lead Intelligent", "先导智能", "ssb", "partial", note="battery equipment",
        share_basis="none",
        share_note="sells production equipment to cell makers"),
    _cn("688499.SS", "Lyric Robot", "利元亨", "ssb", "partial", note="battery equipment", share_basis="none",
        share_note="sells production equipment to cell makers"),
    Company("5019.T", "Idemitsu Kosan", "ssb", "partial", "jp", 3, note="sulfide solid electrolyte", share_basis="none",
        share_note="sells solid electrolyte material, not cells"),
    Company("6762.T", "TDK", "ssb", "partial", "jp", 3, note="small solid-state cells (CeraCharge)"),
    Company("6981.T", "Murata Manufacturing", "ssb", "partial", "jp", 3),
    Company("6810.T", "Maxell", "ssb", "partial", "jp", 3, note="ceramic solid-state cells"),
    Company("003670.KS", "POSCO Future M", "ssb", "partial", "kr", 12, share_basis="none",
        share_note="sells cathode and anode material into cell makers"),
    Company("247540.KQ", "Ecopro BM", "ssb", "partial", "kr", 12, share_basis="none",
        share_note="sells cathode material into cell makers"),
]


# Supply paths for the data-center power track. A company's path is where its data-center revenue
# comes from; a diversified group is placed by its relevant segment, never by its whole business.
PATHS = {
    "A1": "Grid gas: new combined- and simple-cycle plants",
    "A2": "Grid solar and storage",
    "A3": "Grid nuclear: restarts, uprates, SMRs",
    "A5": "Transmission and distribution equipment",
    "B1": "On-site gas: turbines and engines behind the meter",
    "B2": "On-site fuel cells",
    "B3": "On-site storage and backup power",
    "C1": "Existing-nuclear power purchase agreements",
    "U": "Utilities holding the data-center load pipeline",
}

_NO_SHARE = "the supply paths are different products; a revenue share across them would not be a market share"


def _pw(ticker, name, path, purity, market="us", fy=12, **kw) -> Company:
    return Company(ticker, name, "power", purity, market, fy, path=path, share_basis="none",
                   share_note=_NO_SHARE, **kw)


POWER: list[Company] = [
    # --- gas turbines and engines (grid and on-site) ---
    _pw("GEV", "GE Vernova", "A1", "main", note="Power segment: gas turbines and services; also wind and grid"),
    _pw("ENR.DE", "Siemens Energy", "A1", "main", market="de", fy=9, note="Gas Services segment; FY ends 30 Sept"),
    _pw("7011.T", "Mitsubishi Heavy Industries", "A1", "partial", market="jp", fy=3, note="Energy Systems: GTCC"),
    _pw("CAT", "Caterpillar", "B1", "partial", note="Power generation within Energy & Transportation; Solar Turbines"),
    _pw("CMI", "Cummins", "B1", "partial", note="Power Systems segment: gensets and engines"),
    _pw("BKR", "Baker Hughes", "B1", "partial", note="Gas Technology Equipment: aeroderivative turbines"),
    # --- fuel cells ---
    _pw("BE", "Bloom Energy", "B2", "high"),
    # --- nuclear: existing fleet, restarts, SMRs, supply chain ---
    _pw("CEG", "Constellation Energy", "C1", "main", note="largest US nuclear fleet; Crane restart"),
    _pw("VST", "Vistra", "C1", "main", note="nuclear plus gas fleet; Meta PPA"),
    _pw("TLN", "Talen Energy", "C1", "main", note="Susquehanna; Amazon PPA"),
    _pw("OKLO", "Oklo", "A3", "high", note="pre-revenue advanced reactor developer"),
    _pw("SMR", "NuScale Power", "A3", "high", note="SMR designer; pre-commercial"),
    _pw("BWXT", "BWX Technologies", "A3", "partial", note="reactor components and fuel"),
    # --- renewables and storage ---
    _pw("NEE", "NextEra Energy", "A2", "main", note="largest US renewables developer plus FPL"),
    _pw("FSLR", "First Solar", "A2", "high"),
    _pw("FLNC", "Fluence Energy", "B3", "high", fy=9, note="grid storage; FY ends 30 Sept"),
    _pw("TSLA", "Tesla", "B3", "partial", note="Energy Generation and Storage segment only"),
    _pw("EOSE", "Eos Energy", "B3", "high", note="zinc long-duration storage; pre-profit"),
    # --- transmission, distribution and data-center electrical ---
    _pw("ETN", "Eaton", "A5", "main", note="Electrical Americas"),
    _pw("VRT", "Vertiv", "A5", "high", note="data-center power and cooling"),
    _pw("SU.PA", "Schneider Electric", "A5", "main", market="fr", note="Energy Management; data-center segment"),
    _pw("ABBN.SW", "ABB", "A5", "partial", market="ch", note="Electrification"),
    _pw("HUBB", "Hubbell", "A5", "partial", note="Utility Solutions"),
    _pw("PWR", "Quanta Services", "A5", "main", note="grid construction"),
    _pw("267260.KS", "HD Hyundai Electric", "A5", "main", market="kr", note="transformers; US export share"),
    # --- backup power ---
    _pw("GNRC", "Generac", "B3", "partial", note="commercial and industrial gensets"),
    # --- utilities holding the load pipeline ---
    _pw("D", "Dominion Energy", "U", "main", note="Virginia; largest disclosed data-center pipeline"),
    _pw("AEP", "American Electric Power", "U", "main", note="Ohio, Texas; contracted large load"),
    _pw("SO", "Southern Company", "U", "main", note="Georgia Power"),
    _pw("ETR", "Entergy", "U", "main", note="Louisiana; Meta Hyperion"),
]

_NO_SURGICAL_SHARE = (
    "a revenue share would rank this pool differently from an installed-base share, and differently again from "
    "a procedure share; the page shows all three separately rather than picking one and calling it the market"
)

#: The soft-tissue surgical robot pool: only the LISTED makers, because this list feeds price and consensus
#: fetching. The full pool including private makers lives in pipelines/surgical/config.MAKERS, and the two are
#: reconciled by a test so a company cannot be priced here and missing there, or the reverse.
#:
#: `purity` carries unusual weight on this page. Intuitive is a single-segment company, so its multiple IS a
#: surgical-robot multiple; Medtronic and Johnson & Johnson sell one among thousands of products and their
#: multiples price something else entirely. Drawing them on one axis without saying that would be the page's
#: easiest lie.
def _surg(ticker, name, purity, market, **kw) -> Company:
    return Company(ticker, name, "surgical", purity, market, kw.pop("fy", 12),
                   share_basis="none", share_note=_NO_SURGICAL_SHARE, **kw)


SURGICAL: list[Company] = [
    # Pure plays: the multiple genuinely prices a surgical robot business.
    _surg("ISRG", "Intuitive Surgical", "high", "us",
          note="single reportable segment; the only profitable pure play, and the only company here with a PE"),
    _surg("PRCT", "Procept BioRobotics", "high", "us", note="loss-making; EV/Sales and EV per system only"),
    _surg("2675.HK", "Edge Medical", "high", "hk", name_cn="精锋医疗",
          note="HKEX Chapter 18A '-B' pre-profit listing, so the absence of a PE is a listing-rule fact"),
    _surg("2252.HK", "MicroPort MedBot", "high", "hk", name_cn="微创医疗机器人",
          note="suspended from trading since 2026-09-01 with 2026 interim results unpublished; price and "
               "enterprise value are frozen at 2026-08-31 and the page says so on the chart, not in a footnote"),
    _surg("058110.KQ", "meerecompany", "main", "kr", note="Revo-i; KOSDAQ-listed operating company"),
    # Diversified: they sell a soft-tissue robot and disclose nothing about it. Priced on everything else.
    _surg("0853.HK", "MicroPort Scientific", "partial", "hk", name_cn="微创医疗",
          note="parent of MedBot; suspended alongside it"),
    _surg("MDT", "Medtronic", "partial", "us", fy=4,
          note="Hugo sits inside Medical Surgical with no unit or segment disclosure; an April fiscal year, so "
               "its estimates need calendar restatement before any comparison"),
    _surg("JNJ", "Johnson & Johnson", "partial", "us",
          note="Ottava and Monarch sit inside MedTech with no unit disclosure"),
]


COMPANIES: list[Company] = OPTICAL + SSB + POWER + SURGICAL
BY_TICKER: dict[str, Company] = {c.ticker: c for c in COMPANIES}

TRACKS = {
    "optical": {
        "slug": "optical-modules-valuation",
        "label": "Optical modules",
        "facts": "valuation_optical",
        "reference": "share_optical.json",
    },
    "ssb": {
        "slug": "solid-state-battery-valuation",
        "label": "Solid-state batteries",
        "facts": "valuation_ssb",
        "reference": "share_battery.json",
    },
    "surgical": {
        "slug": "surgical-robots",
        "label": "Surgical robots",
        "facts": "valuation_surgical",
        "reference": "share_surgical.json",
        # Share here is units, not revenue, and it lives in pipelines/surgical rather than in share.py: an
        # installed-base share and a revenue share rank this pool differently and that gap is the page's point.
        "share": False,
    },
    "power": {
        "slug": "datacenter-power",
        "label": "Data-center power",
        "facts": "valuation_power",
        "reference": "share_power.json",
        "share": False,
    },
}

# Listings that were considered and deliberately left out, with the reason. Shown on the pages so the
# pool is auditable rather than arbitrary.
_PRIVATE = "private; enters the shipment and capacity table only"
_PRIVATE_RANK = "private; enters cited vendor rankings only"

EXCLUDED = [
    {"ticker": "0877.HK", "name": "O-Net Technologies", "track": "optical",
     "reason": "no longer quoted on Yahoo Finance; treated as unavailable rather than guessed"},
    {"ticker": "unlisted-welion", "name": "WeLion New Energy 卫蓝新能源", "track": "ssb", "reason": _PRIVATE},
    {"ticker": "unlisted-qingtao", "name": "QingTao Energy 清陶能源", "track": "ssb", "reason": _PRIVATE},
    {"ticker": "unlisted-tailan", "name": "TaiLan New Energy 太蓝新能源", "track": "ssb", "reason": _PRIVATE},
    {"ticker": "unlisted-prologium", "name": "ProLogium 辉能科技", "track": "ssb", "reason": _PRIVATE},
    {"ticker": "unlisted-factorial", "name": "Factorial Energy", "track": "ssb", "reason": _PRIVATE},
    {"ticker": "unlisted-huawei", "name": "Huawei optical", "track": "optical", "reason": _PRIVATE_RANK},
]

# FRED daily FX series, quoted as units of the foreign currency per USD unless noted.
FX_SERIES = {
    "CNY": ("DEXCHUS", "per_usd"),
    "JPY": ("DEXJPUS", "per_usd"),
    "KRW": ("DEXKOUS", "per_usd"),
    "TWD": ("DEXTAUS", "per_usd"),
    "HKD": ("DEXHKUS", "per_usd"),
    "GBP": ("DEXUSUK", "usd_per"),  # this one is USD per GBP
    "EUR": ("DEXUSEU", "usd_per"),  # USD per EUR
    "CHF": ("DEXSZUS", "per_usd"),
    "USD": (None, "identity"),
}

# Minimum number of contributing analysts before a consensus is treated as a consensus.
THIN_COVERAGE_BELOW = 3
