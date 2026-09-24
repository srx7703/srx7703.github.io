"""What the FOMC macro overlay and the release event study read, and the parameters they are held to.

Everything here is fixed before the numbers are looked at in the page's sense: changing a window or a
series changes what the page claims, so each change belongs in the page changelog.
"""

from __future__ import annotations

from pathlib import Path

#: First date on the shared time axis. The rolling expectation series can be rebuilt from here: the
#: January 2025 meeting is the first one the archive keeps.
WINDOW_START = "2025-01-01"

#: Daily market series from FRED's keyless graph CSV. Market prices and the policy target are not
#: revised after publication, so the latest download is also what was known on the day.
DAILY_SERIES: dict[str, str] = {
    "DFEDTARU": "Federal funds target range, upper bound",
    "DGS2": "2-year Treasury yield",
    "T5YIE": "5-year breakeven inflation",
    "T5YIFR": "5-year, 5-year forward inflation expectation",
    "DCOILWTICO": "WTI crude oil, Cushing",
    "DCOILBRENTEU": "Brent crude oil, Europe",
}

#: Initial jobless claims, 4-week moving average. This one *is* revised, so it is read from ALFRED
#: vintages: each week's value as first published, dated by the day it was published.
CLAIMS_SERIES = "IC4WSA"

# --- release event study ----------------------------------------------------------------------------

#: Official release times (BLS and BEA calendars), one row per release. BLS refuses scripted downloads
#: of its calendar, so this file is maintained by hand from the two published schedules each December.
CALENDAR_PATH = Path(__file__).with_name("release_calendar.csv")

#: The expectation is read at T0 - PRE_MIN, the reaction at T0 + POST_MIN (the window Gurkaynak, Sack and
#: Swanson use for policy news), and persistence at CLOSE_ET New York time on the release day.
PRE_MIN = 10
POST_MIN = 20
CLOSE_ET = "16:00"
#: How far back to look for the last quote of a market that did not trade inside the window.
CARRY_HOURS = 12
#: Meetings summed into the expectation: the next two decisions after the release.
NEXT_K = 2
#: The platform whose minute prices carry the statistics (Kalshi is shown beside it). Chosen on quote
#: coverage, before the summary statistics were looked at: Kalshi listed each 2025 meeting only once the
#: previous one had passed, so its next-two-meeting set was incomplete for most of 2025, and its thin
#: outcomes often went hours without a quote. Polymarket's minute series covers every outcome of both
#: meetings at every release.
REACTION_PLATFORM = "polymarket"

#: A release ladder is matched to an official release only if the ladder was still open at T0 - PRE_MIN
#: and closed no later than this after T0. Ladders that closed at the originally scheduled time of a
#: release the 2025 shutdown postponed fail the first test and are dropped.
LADDER_CLOSE_AFTER_T0_MAX_H = 3

#: The three release types studied, the Kalshi ladder that supplies each one's consensus, and the sign
#: that makes a surprise hawkish (a higher unemployment rate is dovish).
RELEASES: dict[str, dict] = {
    "cpi": {"label": "CPI", "ladders": ["KXCPI", "KXCPICORE"]},
    "jobs": {"label": "Jobs report", "ladders": ["KXPAYROLLS", "KXU3"]},
    "pce": {"label": "PCE", "ladders": ["KXPCECORE"]},
}
LADDERS: dict[str, dict] = {
    "KXCPI": {"label": "CPI, month on month", "unit": "pp", "resolution": 0.1, "hawkish": 1},
    "KXCPICORE": {"label": "Core CPI, month on month", "unit": "pp", "resolution": 0.1, "hawkish": 1},
    "KXPAYROLLS": {"label": "Nonfarm payrolls, monthly change", "unit": "k", "resolution": 1000.0, "hawkish": 1},
    "KXU3": {"label": "Unemployment rate", "unit": "pp", "resolution": 0.1, "hawkish": -1},
    "KXPCECORE": {"label": "Core PCE prices, month on month", "unit": "pp", "resolution": 0.1, "hawkish": 1},
}
#: The ladder whose surprise is the release's headline number (decided before looking at reactions).
PRIMARY = {"cpi": "KXCPI", "jobs": "KXPAYROLLS", "pce": "KXPCECORE"}

#: Placebo windows: the same clock window on weekdays with none of these releases, no FOMC decision that
#: day or the day before, and not a Thursday (weekly jobless claims come out at 08:30 every Thursday).
#: Both platforms are read, so a release-window move is compared with quiet days on the same platform.
#: Other 08:30 releases the two calendars do not cover (retail sales, for one) can still fall on a
#: placebo day, which if anything widens the noise band.
PLACEBO_EXCLUDE = ("cpi", "jobs", "pce", "ppi", "gdp", "eci")
PLACEBO_CLOCK_ET = "08:30"
