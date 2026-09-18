"""Sources and taxonomies for the data-center power project.

The question this project answers is where the electricity for US data centers comes from, which has
two different answers that the page keeps apart:

* the **physical** answer — what the grid actually built, from EIA's generator inventory;
* the **contract** answer — what a data-center operator actually signed, from company announcements.

Both are needed because neither is the whole truth. A data center on the grid consumes electrons that
came from whatever was on the margin, so "this data center runs on nuclear" is a statement about a
contract, not about physics. Conversely the contracts skew heavily to nuclear and renewables because
those are what a buyer wants to be seen signing, while the plant that actually got built for the load
is usually a gas turbine or a solar-plus-storage block. Reporting only one of them would be wrong in a
predictable direction, so the page reports both and shows the gap.

Everything here is a public, free source. The EIA endpoints are plain workbooks with no key; the PJM
ones are the workbooks behind the annual load forecast report.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------------------------

EIA_860M_CURRENT = "https://www.eia.gov/electricity/data/eia860m/xls/{month}_generator{year}.xlsx"
EIA_860M_ARCHIVE = "https://www.eia.gov/electricity/data/eia860m/archive/xls/{month}_generator{year}.xlsx"
EIA_861M_SALES = "https://www.eia.gov/electricity/data/eia861m/xls/sales_revenue.xlsx"
EIA_STEO = "https://www.eia.gov/outlooks/steo/xls/STEO_m.xlsx"

PJM_LOAD_WORKBOOK = "https://www.pjm.com/-/media/DotCom/library/reports-notices/load-forecast/{year}-load-report-data.xlsx"
PJM_LOAD_ADJUSTMENTS = "https://www.pjm.com/-/media/DotCom/planning/res-adeq/load-forecast/total-load-adjustments-breakdown.xlsx"
PJM_LOAD_REPORT_PDF = "https://www.pjm.com/-/media/DotCom/library/reports-notices/load-forecast/{year}-load-report.pdf"

MONTHS = ("january", "february", "march", "april", "may", "june",
          "july", "august", "september", "october", "november", "december")

# EIA publishes the newest 860M under /xls/ and moves it to /archive/xls/ a couple of months later, so
# a fetch walks back month by month and tries both. A month that is not published yet answers 200 with
# the section's HTML index page rather than 404, which is why the fetcher checks the payload is a zip.
EIA_860M_LOOKBACK_MONTHS = 6

# Two vintages a year apart are differenced to find retirements that were pushed back. That is the
# physical footprint of "the load kept a plant alive", and it does not appear in any single file.
DEFERRAL_COMPARISON_MONTHS = 12

# ---------------------------------------------------------------------------------------------
# Taxonomies
# ---------------------------------------------------------------------------------------------

#: EIA technology string -> the family the page groups it under. Exhaustive against the July 2026
#: workbook (29 distinct values across all four sheets); an unmapped value raises rather than being
#: silently bucketed as "other", because a new technology appearing is news, not noise.
TECHNOLOGY_FAMILY: dict[str, str] = {
    "Natural Gas Fired Combined Cycle": "gas_cc",
    "Natural Gas Fired Combustion Turbine": "gas_ct",
    "Natural Gas Internal Combustion Engine": "gas_engine",
    "Natural Gas Steam Turbine": "gas_steam",
    "Other Natural Gas": "gas_other",
    "Natural Gas with Compressed Air Storage": "storage",
    "Batteries": "storage",
    "Flywheels": "storage",
    "Hydroelectric Pumped Storage": "storage",
    "Solar Photovoltaic": "solar",
    "Solar Thermal with Energy Storage": "solar",
    "Solar Thermal without Energy Storage": "solar",
    "Onshore Wind Turbine": "wind",
    "Offshore Wind Turbine": "wind",
    "Nuclear": "nuclear",
    "Conventional Steam Coal": "coal",
    "Coal Integrated Gasification Combined Cycle": "coal",
    "Petroleum Coke": "oil",
    "Petroleum Liquids": "oil",
    "Conventional Hydroelectric": "hydro",
    "Geothermal": "geothermal",
    "Landfill Gas": "biomass",
    "Municipal Solid Waste": "biomass",
    "Other Waste Biomass": "biomass",
    "Wood/Wood Waste Biomass": "biomass",
    "Hydrokinetic": "other",
    "Other Gases": "other",
    "All Other": "other",
}

#: The order families appear in on a chart, so a stacked bar does not reshuffle between runs.
FAMILY_ORDER = ("solar", "storage", "gas_cc", "gas_ct", "gas_engine", "gas_steam", "gas_other",
                "wind", "nuclear", "hydro", "geothermal", "biomass", "coal", "oil", "other")

FAMILY_LABEL = {
    "solar": "Solar", "storage": "Storage", "gas_cc": "Gas, combined cycle",
    "gas_ct": "Gas, combustion turbine", "gas_engine": "Gas engines", "gas_steam": "Gas steam",
    "gas_other": "Gas, other", "wind": "Wind", "nuclear": "Nuclear", "hydro": "Hydro",
    "geothermal": "Geothermal", "biomass": "Biomass", "coal": "Coal", "oil": "Oil", "other": "Other",
}

#: EIA status code -> the stage vocabulary the whole page uses. The same four words label the contract
#: table, so a reader can compare "announced" gas turbines with "announced" nuclear agreements without
#: translating between two scales.
STATUS_STAGE: dict[str, str] = {
    "P": "announced",        # planned, regulatory approvals not initiated
    "L": "announced",        # regulatory approvals pending
    "T": "permitted",        # regulatory approvals received, not yet building
    "U": "under_construction",
    "V": "under_construction",
    "TS": "commissioning",   # construction complete, not yet commercial
    "OP": "in_service",
    "SB": "in_service",
    "OA": "in_service",
    "OS": "retired",
}

STAGE_ORDER = ("announced", "permitted", "under_construction", "commissioning", "in_service")

#: Supply paths, shared with the valuation track's company labels (pipelines/valuation/config.PATHS).
#: A row in the contract table and a company in the pool carry the same code, which is what lets the
#: page put "who signed what" next to "who sells it".
PATHS = {
    "A1": "Grid gas: new combined- and simple-cycle plants",
    "A2": "Grid solar and storage",
    "A3": "Grid nuclear: restarts, uprates, SMRs",
    "A4": "Deferred retirements",
    "A5": "Transmission and distribution equipment",
    "B1": "On-site gas: turbines and engines behind the meter",
    "B2": "On-site fuel cells",
    "B3": "On-site storage and backup power",
    "C1": "Existing-nuclear power purchase agreements",
    "C2": "Renewable power purchase agreements",
    "U": "Utilities holding the data-center load pipeline",
}

# ---------------------------------------------------------------------------------------------
# Regions
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Region:
    key: str
    label: str
    iso: str
    states: tuple[str, ...]  # EIA state codes, for the 861M sales series
    zones: tuple[str, ...] = ()  # PJM zone sheet names, where the ISO publishes per-zone data
    note: str = ""


REGIONS = (
    Region("pjm", "PJM", "PJM", ("VA", "OH", "PA", "MD", "NJ", "IL", "WV", "DE", "DC", "IN", "KY", "MI", "NC", "TN"),
           zones=("PJM_RTO", "DOM", "AEP", "COMED", "PL", "PS", "PECO", "BGE", "APS", "ATSI", "DAY", "DEOK"),
           note="Dominion's Virginia zone is the largest single data-center load in the country"),
    Region("ercot", "ERCOT", "ERCOT", ("TX",),
           note="Texas paused new data-center interconnection in 2026 pending a regulatory audit"),
    Region("southeast", "Southeast", "", ("GA", "LA", "MS", "AL", "SC"),
           note="Georgia Power and Entergy Louisiana; the largest new-build gas for data centers"),
)

#: PJM writes a zone's name differently in the two workbooks the page joins: the per-zone load sheets
#: are named "PJM_RTO" and "DAY", while the large-load adjustment sheet spells the same zones
#: "PJM RTO" and "DAYTON". Joining without this map silently drops the RTO total and the Dayton zone
#: from every comparison of peak growth against large-load growth — and the RTO row is the headline.
PJM_ZONE_ALIASES = {
    "PJM RTO": "PJM_RTO",
    "DAYTON": "DAY",
    "JCPL": "JCPL_FE_EAST",
    "METED": "METED_FE_EAST",
    "PENLC": "PN_FE_EAST",
}


#: The whole country, for the denominator.
NATIONAL = "US"

# ---------------------------------------------------------------------------------------------
# Thresholds used by the checks
# ---------------------------------------------------------------------------------------------

#: A 860M vintage older than this many days makes the "physical" half of the page stale.
MAX_INVENTORY_AGE_DAYS = 75
#: A curated reference row unchecked for longer than this is greyed on the page rather than dropped.
REFERENCE_STALE_DAYS = 90
