"""The two pools must agree.

`pipelines/valuation/config.SURGICAL` is the list the price and consensus fetchers walk, so it holds listed
makers only. `pipelines/surgical/config.MAKERS` is the full pool including the private ones, and it is what the
page's tables are built from. Nothing keeps them in step except this file, and the failure it prevents is quiet:
a company priced but absent from the pool renders a multiple with no company attached to it, and a company in
the pool but unpriced silently drops out of the valuation section while still appearing in the unit tables.
"""

from __future__ import annotations

from pipelines.surgical import config as surg
from pipelines.valuation import config as val


def test_every_priced_listing_is_in_the_pool():
    priced = {c.ticker for c in val.SURGICAL}
    known = {m.ticker for m in surg.MAKERS}
    assert priced <= known, f"priced but not in the maker pool: {sorted(priced - known)}"


def test_every_listed_maker_is_priced():
    listed = {m.ticker for m in surg.MAKERS if m.listed and not m.ticker.startswith("private:")}
    priced = {c.ticker for c in val.SURGICAL}
    assert listed == priced, (
        f"in the pool but never priced: {sorted(listed - priced)}; "
        f"priced but not marked listed: {sorted(priced - listed)}"
    )


def test_private_makers_are_never_priced():
    """A private company has no ticker, and inventing one would create a row with a blank multiple."""
    private = {m.ticker for m in surg.MAKERS if not m.listed}
    assert all(t.startswith("private:") for t in private)
    assert not (private & {c.ticker for c in val.SURGICAL})


def test_names_agree_between_the_two_pools():
    by_ticker = {m.ticker: m.name for m in surg.MAKERS}
    mismatched = [(c.ticker, c.name, by_ticker[c.ticker])
                  for c in val.SURGICAL if by_ticker.get(c.ticker) not in (None, c.name)]
    assert not mismatched, f"the same listing is named two ways: {mismatched}"


def test_a_suspended_company_is_flagged_in_the_pool_not_dropped():
    """MicroPort MedBot has no price at all right now. It stays, flagged, because dropping the most important
    Chinese pure-play would quietly improve every share the page computes."""
    medbot = next(m for m in surg.MAKERS if m.ticker == "2252.HK")
    assert medbot.status == "suspended"
    assert "2252.HK" in {c.ticker for c in val.SURGICAL}


def test_every_excluded_company_states_a_reason():
    for row in surg.EXCLUDED:
        assert row.get("why"), f"{row.get('name')} was excluded with no reason given"
        assert len(row["why"]) > 10


def test_no_excluded_company_is_also_in_the_pool():
    excluded = {r.get("ticker") for r in surg.EXCLUDED if r.get("ticker")}
    assert not (excluded & {m.ticker for m in surg.MAKERS})
    assert not (excluded & {c.ticker for c in val.SURGICAL})


def test_only_disclosing_tiers_are_chartable():
    """A company that publishes nothing must not be drawn at zero, which reads as 'sells none'."""
    for tier, chartable in surg.TIER_CHARTABLE.items():
        assert tier in surg.TIER_ORDER
        if tier in ("T4", "T5"):
            assert chartable is False, f"{tier} discloses no units and must not be chartable"


def test_every_maker_has_a_known_tier_and_region():
    for m in surg.MAKERS:
        assert m.tier in surg.TIER_ORDER, f"{m.name} has tier {m.tier!r}"
        assert m.region, f"{m.name} has no region"


def test_the_scope_exclusions_are_documented():
    assert surg.SCOPE_INCLUDES
    for key, why in surg.SCOPE_EXCLUDES.items():
        assert len(why) > 20, f"{key} is excluded without saying why"
