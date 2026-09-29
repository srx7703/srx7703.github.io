"""The registered verdicts: Q3 is scored; Q6, Q7 and Q8 return ``not_yet`` with the reason.

Q3 (prereg §2, implementation notes 4-6): across pure-play company-cycles,

* **holds** if at least ``Q3_HOLDS_SHARE`` lead by ``LEAD_MIN`` month or more *and* the median lead is inside
  ``Q3_MEDIAN_BAND``;
* **falsified** if fewer than ``Q3_FALSIFY_SHARE`` lead by ``LEAD_MIN`` month or more;
* **inconclusive** otherwise.

A company-cycle is eligible when its price peak is confirmed, it has a preceding price trough, and its window
is complete. The same verdict is reported, never pooled, for three other samples: DRAM makers only, the
diversified group, and the NAND panel against the flash calendar.

The one post-hoc number this module computes is :func:`flips_to_inconclusive`: how many non-leading pairs
would have to become leading for the main verdict to stop being "falsified". It is a fragility measure chosen
after the result was seen, and the page shows it only under the robustness heading.
"""

from __future__ import annotations

import statistics

from pipelines.memcycle.config import (
    LEAD_MIN,
    Q3_FALSIFY_SHARE,
    Q3_HOLDS_SHARE,
    Q3_MEDIAN_BAND,
    Q6_TEST_START,
    Q8_DEADLINE,
)

GROUPS = {
    "main_pure": "Pure plays (registered verdict)",
    "sens_dram_makers_only": "DRAM makers only (sensitivity)",
    "diversified_dram": "Diversified group (shown only)",
    "nand_panel": "NAND panel on the flash calendar (shown only)",
}


def verdict_of(share: float, median: float) -> str:
    lo, hi = Q3_MEDIAN_BAND
    if share >= Q3_HOLDS_SHARE and lo <= median <= hi:
        return "holds"
    if share < Q3_FALSIFY_SHARE:
        return "falsified"
    return "inconclusive"


def verdict(leads: list[int]) -> dict:
    if not leads:
        return {"n": 0}
    share = sum(1 for x in leads if x >= LEAD_MIN) / len(leads)
    med = statistics.median(leads)
    lo, hi = Q3_MEDIAN_BAND
    return {
        "n": len(leads),
        "share_lead_ge1": round(share, 3),
        "median_lead": med,
        "share_lead_3_to_9": round(sum(1 for x in leads if lo <= x <= hi) / len(leads), 3),
        "leads": leads,
        "verdict": verdict_of(share, med),
    }


def eligible(r: dict, product: str) -> bool:
    return (r["product"] == product and r.get("status") == "ok" and bool(r.get("complete"))
            and bool(r["price_peak_confirmed"]))


def samples(rows: list[dict]) -> dict[str, list[dict]]:
    return {
        "main_pure": [r for r in rows if eligible(r, "DRAM") and r["group"] == "pure"],
        "sens_dram_makers_only": [r for r in rows if eligible(r, "DRAM") and r["dram_maker"]],
        "diversified_dram": [r for r in rows if eligible(r, "DRAM") and r["group"] == "diversified"],
        "nand_panel": [r for r in rows if eligible(r, "NAND")],
    }


def q3(rows: list[dict]) -> dict:
    return {k: verdict([r["lead_months"] for r in v]) for k, v in samples(rows).items()}


def flips_to_inconclusive(leads: list[int]) -> int | None:
    """Fewest non-leading pairs that must become leading for a 'falsified' verdict to stop being one.

    Flipping a pair can only raise the leading share, so the answer is the smallest k with
    (leading + k) / n >= Q3_FALSIFY_SHARE. None if the verdict is not 'falsified' to begin with.
    """
    n = len(leads)
    lead = sum(1 for x in leads if x >= LEAD_MIN)
    if not n or lead / n >= Q3_FALSIFY_SHARE:
        return None
    k = 0
    while (lead + k) / n < Q3_FALSIFY_SHARE:
        k += 1
    return k


def not_yet(q3_main: dict, calendar_last: str, current_peak_confirmed: bool) -> list[dict]:
    """The registered items that cannot be scored yet, each with its reason in words."""
    return [
        {"id": "Q6", "status": "not_yet",
         "why": "needs quarterly capex and memory revenue for the pool (OpenDART for Samsung and SK hynix); "
                f"the out-of-sample window starts {Q6_TEST_START} and nothing has been computed."},
        {"id": "Q7", "status": "not_yet",
         "why": "needs industry revenue and pooled margins by cycle; the stock-drawdown measure also needs the "
                "local closes. Not blind: the price data had been seen before registration."},
        {"id": "Q8", "status": "not_yet",
         "why": ("the next DRAM price peak is not confirmed: the index is still at its high in "
                 f"{calendar_last}, the last month published" if not current_peak_confirmed else
                 "a new peak is confirmed; the 12-month window after it has not elapsed")
                + f". Deadline {Q8_DEADLINE}."},
    ]
