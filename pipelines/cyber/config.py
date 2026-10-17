"""Pinned layer tree, vocabularies and measurement choices for project I (cybersecurity).

Every constant here is registered in docs/EVALUATION_PLAN.md, section "Cybersecurity (project I)";
pipelines/cyber/tests/test_plan.py fails if one drifts from the registered text. Change both together,
with an amendment. The pool of listed companies lives in ``pool.py``, which is generated from the same
registered section and reconciled by the same test.
"""

from __future__ import annotations

# --- the layer tree --------------------------------------------------------------------------------------
# One tree only. A company sits in the layer of its largest cybersecurity revenue line and appears once on
# the layer map; its other lines go in `also_in`. Twelve L1 layers, with the L2 sub-sectors that define them.
LAYERS: dict[str, dict] = {
    "network_edge": {
        "code": 1,
        "name": "Network and edge security",
        "l2": ["ngfw_appliance", "sase_sse", "ddos_waf_adc", "ndr_visibility", "carrier_security"],
    },
    "endpoint": {
        "code": 2,
        "name": "Endpoint and XDR",
        "l2": ["epp_edr_xdr", "consumer_security", "uem"],
    },
    "identity": {
        "code": 3,
        "name": "Identity and access",
        "l2": ["workforce_iam", "iga_nhi", "pam", "idv_biometrics", "keys_pki"],
    },
    "cloud_appsec": {
        "code": 4,
        "name": "Cloud and application security",
        "l2": ["cnapp", "appsec_supply_chain", "api_edge"],
    },
    "data_privacy": {
        "code": 5,
        "name": "Data security and privacy",
        "l2": ["dspm_dag", "backup_resilience", "encryption_keys", "privacy_compliance"],
    },
    "secops": {
        "code": 6,
        "name": "Security operations",
        "l2": ["siem_analytics", "soar_ai_soc", "exposure_mgmt", "threat_intel", "mdr", "forensics"],
    },
    "email_human": {
        "code": 7,
        "name": "Email, collaboration and human risk",
        "l2": ["email_security", "awareness_training", "browser_web"],
    },
    "ot_iot": {
        "code": 8,
        "name": "OT, IoT and ICS",
        "l2": ["ot_ics", "iot_medical"],
    },
    "grc_insurance": {
        "code": 9,
        "name": "GRC, compliance and cyber insurance",
        "l2": ["grc_irm", "third_party_risk", "cyber_insurance"],
    },
    "services": {
        "code": 10,
        "name": "Services, MSSP, consulting and distribution",
        "l2": ["gov_defense_services", "consulting_mssp", "distribution", "cyber_range_training"],
    },
    "ai_security": {
        "code": 11,
        "name": "AI security and AI-native security operations",
        "l2": ["ai_app_model", "agent_security", "ai_native_secops"],
    },
    "infra_hw": {
        "code": 12,
        "name": "Infrastructure, hardware and post-quantum",
        "l2": ["secure_chips", "hsm_commercial_crypto", "post_quantum", "high_assurance_crypto"],
    },
}

# L2 sub-sectors that exist only in China's market, mapped under the nearest layer and labelled as such.
CHINA_ONLY_L2 = ["forensics", "cyber_range_training", "hsm_commercial_crypto"]

# The three scouting notes used three different layer schemes. These map each source label onto the one tree.
# "platform" is not a layer: a company with three or more layers is placed by its largest line and carries
# `platform = True`. China's "综合平台" therefore has no entry here; each is placed by hand from the annual
# report's segment table, and until that is read it sits under `anchor_product` and is marked [U].
CROSSWALK: dict[str, dict[str, str]] = {
    "overseas_listed": {
        "网络与边缘安全": "network_edge",
        "终端与XDR": "endpoint",
        "身份与访问": "identity",
        "云与应用安全": "cloud_appsec",
        "数据安全与隐私": "data_privacy",
        "安全运营": "secops",
        "邮件/协作与人因风险": "email_human",
        "GRC/合规/网络保险": "grc_insurance",
        "服务/MSSP/咨询/分销": "services",
        "基础设施/硬件/后量子": "infra_hw",
    },
    "overseas_private": {
        "network/SASE": "network_edge",
        "endpoint/XDR": "endpoint",
        "identity": "identity",
        "cloud & AppSec": "cloud_appsec",
        "data security": "data_privacy",
        "SecOps/SIEM/MDR": "secops",
        "email & human risk": "email_human",
        "OT/IoT": "ot_iot",
        "GRC/compliance & cyber insurance": "grc_insurance",
        "services": "services",
        "AI security & AI-native SecOps": "ai_security",
    },
    "china": {
        "网络与边界安全": "network_edge",
        "终端与工作负载": "endpoint",
        "身份与访问": "identity",
        "数据与应用安全": "data_privacy",
        "安全运营与情报": "secops",
        "取证与监管": "secops",
        "攻防/靶场/测评": "services",
        "工业/OT安全": "ot_iot",
        "AI安全": "ai_security",
        "密码与可信计算": "infra_hw",
    },
}

# --- closed vocabularies ---------------------------------------------------------------------------------
PURITY = ["high", "main", "partial"]
PLACEMENT_BASIS = ["segment_disclosed", "company_description", "etf_classification", "anchor_product"]
# One status vocabulary for listed and private companies, so a company can move from listed to acquired.
STATUS = ["independent", "pe_owned", "pending_acquisition", "acquired", "listed", "delisted"]
RINGS = ["A", "B", "C", "F"]
BASIS = ["disclosed", "reported", "undisclosed"]
METRIC_KINDS = [
    "primary_round_valuation",
    "round_amount",
    "tender_valuation",
    "arr_company_stated",
    "acquisition_price_announced",
    "acquisition_price_sec",
    "take_private_price",
    "secondary_mark",
    "reported_talks",
    "third_party_estimate",
    "derived",
]
NEVER_CHARTED_KINDS = ["secondary_mark", "reported_talks", "third_party_estimate"]
QSTATUS = ["collecting", "holds", "falsified", "undecidable"]

# --- measurement choices ---------------------------------------------------------------------------------
# Every date below is a Monday run of the weekly job unless it says otherwise. A weekly snapshot is that run;
# prices in it are the previous session's close for the Americas and Europe and the same day's for Asia.
FIRST_SNAPSHOT = "2026-10-26"
FORMAL_SERIES_FROM = FIRST_SNAPSHOT
BASELINE = (
    "2026-10-16"  # market date of the baseline snapshot, taken after the US close and stored under 2026-10-17 UTC
)
BASELINE_FILE_DATE = "2026-10-17"
WINDOW_WEEKS = 12  # Q1, Q2, Q3, Q7: the Monday snapshots 2026-10-26 to 2027-01-11
WINDOW_LAST = "2027-01-11"
PANEL_MIN_WEEKS = 9  # 75% of the 12 snapshots
PANEL_MIN_SHARE = "75%"  # a company is in the panel if it is computable in this share of the snapshots there are
MIN_SNAPSHOTS = 10  # fewer snapshots in the window and Q1, Q2, Q3 and Q7 are undecidable
MIN_PRICED_SHARE = "80%"  # a Monday on which fewer ring A listings were priced is not a snapshot
BOOTSTRAP_DRAWS = "10,000"
SEED = "20261026"  # stats.SEED; tests/test_stats.py keeps the two equal

EV_SOURCE = "Yahoo enterpriseValue"
# NTM revenue = w * FY0 + (1 - w) * FY+1 consensus. FY0 is the first fiscal year whose annual report Yahoo has not yet
# processed, so it can already have ended; then w is 0.
NTM_WEIGHT = "w = days from the snapshot to the end of fiscal year 0, divided by 365, held between 0 and 1"
FY0_END = "the end of fiscal year 0 is Yahoo nextFiscalYearEnd"
GROWTH = "growth = NTM revenue / trailing-twelve-month revenue - 1"
FCF_MARGIN = "FCF margin = trailing-twelve-month free cash flow / trailing-twelve-month revenue"
RULE_OF_40 = "Rule of 40 = 100 x (growth + FCF margin)"
TRAILING_SOURCE = "trailing figures are Yahoo totalRevenue and freeCashflow as served"
# upside = target mean / price - 1 in the quote currency (pence divided by 100 first).
UPSIDE = "upside = targetMeanPrice / price - 1"
DISPERSION = "dispersion = (targetHighPrice - targetLowPrice) / targetMeanPrice"
# Fewer opinions than this and a company is drawn faded and flagged thin (same constant the other tracks use).
THIN_COVERAGE_BELOW = 3
# A-share company medians use only purity high with at least this many East Money 2026E brokers.
CN_MEDIAN_MIN_BROKERS = 2
CN_MEDIAN_MIN_N = 5
EM_RATING_WINDOW = "6 months"

# Private valuation: headline, discount interval, public-comparable interval (M4).
# The discount by year of the round is a registered assumption, not data; the page labels it an estimate.
DISCOUNT_BY_ROUND_YEAR = {"2021": "-60%", "2022": "-55%", "2023": "-35%", "2024": "-15%", "2025": "-5%", "2026": "0%"}
DISCOUNT_BAND = "plus or minus 15 percentage points"
# Layer code -> the listed companies whose EV/NTM revenue range is the public-comparable interval (lowest to highest).
# A private company in a layer that is not a key here is shown and not scored in Q6.
COMPARABLES_BY_LAYER = {
    "network_edge": ["ZS", "NTSK", "FTNT"],
    "identity": ["OKTA", "SAIL"],
    "cloud_appsec": ["NET", "RBRK", "ZS"],
    "endpoint": ["CRWD", "S"],
}

# --- frozen lists ------------------------------------------------------------------------------------------
# ``frozen.py`` recomputes each from the committed baseline snapshot and ``tests/test_frozen.py`` fails if these
# differ, so a list cannot be edited to suit a result.
# Q1, Q2, Q3, Q7 and Q9 population on BASELINE: ring A, listed on a US exchange, purity high, status listed, and
# FY0 and FY+1 revenue consensus each from at least three analysts. The rule governs later weeks; this is its count.
Q1_Q2_BASELINE = [
    "CHKP", "CRWD", "FTNT", "MITK", "NET", "NTSK", "OKTA", "OSPN", "PANW", "QLYS", "RBRK", "RPD", "S", "SAIL",
    "TENB", "TLS", "ZS",
]  # fmt: skip
# Q4 and Q5: ring A, outside China, status listed, at least three target-price analysts on BASELINE.
FROZEN_Q4_Q5 = [
    "4704.T", "AKAM", "ALLT", "ATEN", "BB", "CGNT", "CHKP", "CRWD", "CSCO", "CVLT", "FFIV", "FSECURE.HE",
    "FTNT", "GBG.L", "GEN", "IDN", "MITK", "MSFT", "NCC.L", "NET", "NTSK", "OKTA", "OSPN", "PANW", "QLYS",
    "RBRK", "RPD", "S", "SAIL", "TENB", "TLS", "YSN.DE", "YUBICO.ST", "ZS",
]  # fmt: skip
# Q7: A-share ring A, purity high, at least CN_MEDIAN_MIN_BROKERS East Money brokers for the current year, on BASELINE.
FROZEN_Q7_CN = ["002212.SZ", "002439.SZ", "300188.SZ", "300369.SZ", "688489.SS", "688561.SS"]
# Q8: held by the CIBR ETF on 2026-10-15, purity high, status listed, enterprise value between $1B and $10B.
FROZEN_Q8 = ["4704.T", "NTSK", "QLYS", "RPD", "S", "TENB"]
Q8_JPY_PER_USD = "157.99"  # Yahoo JPY=X, close of 2026-10-16: the one non-dollar enterprise value among the candidates

# --- registered questions: thresholds exactly as written in section I ---------------------------------------
Q1 = {
    "uplift_holds": "0.10",
    "p_holds": "0.10",
    "boot_level": "90%",
    "min_n": "15",
    "merge_below": "3",
    "first": "2027-01-15",
}
Q2 = {"r2_holds": "0.5", "level": "95%", "min_n": "15", "first": "2027-01-15"}
Q3 = {"ratio_holds": "2", "level": "95%", "min_group_n": "5", "cut": "40", "first": "2027-01-15"}
Q4 = {
    "p": "0.05",
    "upper_bound": "0.35",
    "min_n": "30",
    "min_opinions": "3",
    "first_snapshot": "2026-10-26",
    "window_end": "2027-10-25",
    "graded": "2027-10-26",
}
Q5 = {
    "min_n": "30",
    "windows": [["2026-10-26", "2027-01-22"], ["2027-01-25", "2027-04-23"]],
    "trading_days": "252",
    "interim": "2027-01-29",
    "graded": "2027-04-30",
    "level": "95%",
}
Q6 = {
    "holds": "50%",
    "falsified": "30%",
    "min_scored": "10",
    "ledger_frozen_on": "2026-12-31",
    "first_round_year": "2021",
}
Q7 = {"level": "95%", "min_n": "5", "first": "2027-01-15"}
Q8 = {
    "after": "2026-10-16",
    "deadline": "2027-10-15",
    "min_deals": "2",
    "midterm": "2027-04-16",
    "ev_floor": "$1B",
    "ev_cap": "$10B",
}
Q9 = {
    "widening": "20%",
    "start": ["2026-10-26", "2026-11-02", "2026-11-09", "2026-11-16"],
    "end_last": "2027-10-11",
    "graded": "2027-10-12",
    "min_rest": "8",
    "trio": ["PANW", "CRWD", "NET"],
}
Q10 = {
    "move": "0.5%",
    "follow": "4 weeks",
    "level": "10%",
    "min_pairs": "30",
    "first": "2027-01-15",
    "last": "2027-10-15",
}

REGISTERED = "2026-10-20"  # the day section I is due; the committed date in git is the authority
