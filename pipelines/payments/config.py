"""Pinned universe and measurement choices for project H.

Every constant here is registered in docs/EVALUATION_PLAN.md, section "New payments companies (project H)";
tests/test_plan.py fails if one drifts from the registered text. Change both together, with an amendment.
"""
from __future__ import annotations

# Layer of each company = its largest revenue source. 'core' rows are charted and enter share; the rest are
# classification-table only.
UNIVERSE: dict[str, dict[str, list[str]]] = {
    "A": {"core": ["Stripe", "Adyen", "Checkout.com", "PayPal", "Block/Square", "Shopify Payments", "Toast",
                   "Fiserv Clover", "Global Payments", "Shift4"],
          "table": ["Rapyd", "Highnote"]},
    "B": {"core": ["Affirm", "Klarna", "Afterpay", "PayPal Pay Later", "Zip", "Sezzle"], "table": []},
    "C": {"core": ["Ramp", "Brex", "BILL", "Navan", "Mercury", "Corpay"],
          "table": ["Rippling", "Expensify", "AvidXchange"]},
    "D": {"core": ["Airwallex", "Wise", "Payoneer", "dLocal", "Flywire"], "table": ["Ebury", "Nium"]},
    "E": {"core": ["Marqeta"], "table": ["Lithic", "Unit"]},
    "F": {"core": ["Circle"], "table": ["Bridge", "Tempo", "BVNK"]},
}

# Q1: client-side SDKs only; framework wrappers install the core package and would double count.
NPM_CLIENT_SDKS: dict[str, list[str]] = {
    "Stripe": ["@stripe/stripe-js"],
    "Adyen": ["@adyen/adyen-web"],
    "PayPal": ["@paypal/paypal-js", "braintree-web"],
    "Checkout.com": ["@checkout.com/checkout-web-components"],
    "Airwallex": ["@airwallex/components-sdk"],
}
NPM_EXCLUDED_WRAPPERS = ["@stripe/react-stripe-js", "@paypal/react-paypal-js"]
NPM_SERVER_SDKS = ["stripe", "@adyen/api-library", "braintree"]  # descriptive only
NOT_COMPANY_PACKAGES = {"npm": ["affirm"], "pypi": ["affirm", "airwallex"]}

# Public job boards: counts only, never job text. Ownership re-verified in W2; failures become amendments.
ATS_BOARDS: dict[str, list[str]] = {
    "ashby": ["ramp", "airwallex", "plaid"],
    "greenhouse": ["stripe", "brex", "mercury", "affirm", "adyen", "chime", "block", "toast", "sezzle", "billcom"],
}
ATS_EXCLUDED_TOKENS = {"greenhouse": ["wise"]}  # a different company

METRIC_KINDS = [
    "filed_revenue", "filed_volume", "stated_run_rate", "stated_volume", "primary_round_valuation",
    "tender_valuation", "reported_talks", "acquisition_price", "counterparty_filing", "statutory_accounts",
    "third_party_estimate", "derived",
]
NEVER_CHARTED_KINDS = ["reported_talks", "third_party_estimate"]

STATUSES = ["collecting", "holds", "falsified", "undecidable"]

# Registered thresholds, as written in section H.
Q1 = {"ratio_holds": "2.0x", "ratio_refuted": "1.5", "consecutive_refreshes": "3"}
Q2 = {"rolling": "3-month rolling mean", "crawls": "6 monthly crawls", "graded": "2027-03"}
Q3 = {"gap_pp": "5 percentage points", "graded": ["2026-11", "2027-02"]}
Q4 = {"band": ["1.0%", "1.6%"], "refute_high": "2.0%", "refute_low": "0.8%"}
Q5 = {"credit_share": "70%"}
Q7 = {"wise_cut_bps": "3 bps", "fsb_floor": "1.5%", "fsb_refute": "1.4%"}
Q8 = {"floor": "2.60% + $0.10", "cut_bps": "10 bps", "current": "2.70% + $0.10"}
Q9 = {"deadline": "2027-09-30", "valuation_cap": "$5B", "min_deals": "2"}
