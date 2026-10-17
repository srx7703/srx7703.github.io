"""Departures of the live pool from the pool registered in section I.

``data/reference/cyber/pool_registered.json`` is the pool as registered and never changes. ``pool.py`` is live: a
company that is acquired moves from ring A to ring F, and a layer can be corrected once a segment table has been read.
Each such change is an amendment to section I. ``tests/test_plan.py`` fails until the ticker is listed here with the
amendment's date and appears in the section's Amendments list. Nothing is listed yet.
"""

from __future__ import annotations

MOVED: dict[str, str] = {}  # ticker -> date of the amendment that changed its layer, purity or name
ADDED: dict[str, str] = {}  # ticker -> date of the amendment that added it to the pool
