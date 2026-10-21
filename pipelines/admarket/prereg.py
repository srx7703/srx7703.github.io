"""Read the registered numbers out of ``docs/prereg/ad-market/`` (append-only files; never edited here).

The forecast file's table is the source of truth for scoring: the page scores the intervals as registered, to the
dollar million, not a recomputation. ``publish`` recomputes rule A from the panel as a check that the registered
numbers are what the rule gives, and says so on the page.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from pipelines.admarket import config as cfg

_ROW = re.compile(r"^\|\s*([A-Za-z][A-Za-z ]*?)\s*\|\s*([\d,]+)\s*\|\s*([\d,]+) to ([\d,]+)\s*\|\s*([\d,]+)\s*\|"
                  r"\s*([\d,]+) to ([\d,]+)\s*\|\s*$")
_BAND = re.compile(r"one quarter ahead ([+\-−]?\d+\.\d)% to ([+\-−]?\d+\.\d)% \(n=(\d+)\), "
                   r"two quarters ahead ([+\-−]?\d+\.\d)% to ([+\-−]?\d+\.\d)% \(n=(\d+)\)")
_CALL = re.compile(r"^(B\d)\. (.+)$")
_SHA = re.compile(r"SHA-256 `([0-9a-f]{64})`")


def _n(s: str) -> int:
    return int(s.replace(",", ""))


def _pct(s: str) -> float:
    return float(s.replace("−", "-")) / 100


def parse_forecasts(text: str) -> dict:
    """{forecasts: {name: {quarter: {point, low, high}}} in $ millions, band: {1: (lo, hi, n), 2: ...},
    calls: [{id, text}], snapshot_sha256}. Raises if the table or the band sentence is missing."""
    fc: dict[str, dict] = {}
    for line in text.splitlines():
        m = _ROW.match(line.strip())
        if m:
            name = m.group(1).strip()
            fc[name] = {
                cfg.FORECAST_TARGETS[0]: {"point": _n(m.group(2)), "low": _n(m.group(3)), "high": _n(m.group(4))},
                cfg.FORECAST_TARGETS[1]: {"point": _n(m.group(5)), "low": _n(m.group(6)), "high": _n(m.group(7))},
            }
    b = _BAND.search(text)
    if not fc or not b:
        raise ValueError("forecast table or band sentence not found in the registered file")
    calls = [{"id": m.group(1), "text": m.group(2).strip()} for line in text.splitlines()
             if (m := _CALL.match(line.strip()))]
    sha = _SHA.search(text)
    return {
        "forecasts": fc,
        "band": {1: (_pct(b.group(1)), _pct(b.group(2)), int(b.group(3))),
                 2: (_pct(b.group(4)), _pct(b.group(5)), int(b.group(6)))},
        "calls": calls,
        "snapshot_sha256": sha.group(1) if sha else None,
    }


def amendments(text: str) -> list[str]:
    """Lines under '## Amendments' other than '(none)'."""
    part = text.split("## Amendments", 1)
    if len(part) < 2:
        return []
    return [ln.strip() for ln in part[1].splitlines() if ln.strip() and ln.strip() != "(none)"]


def git_blob(path: Path) -> str:
    """The git blob SHA-1 of a file (what ``git hash-object`` prints), so the page can cite the registered text."""
    buf = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(buf) + buf).hexdigest()


def load() -> dict:
    ftext = cfg.PREREG_FORECASTS.read_text(encoding="utf-8")
    rtext = cfg.PREREG_RULES.read_text(encoding="utf-8")
    out = parse_forecasts(ftext)
    out["files"] = [
        {"path": p.relative_to(cfg.REPO_ROOT).as_posix(), "blob": git_blob(p), "amendments": amendments(t)}
        for p, t in ((cfg.PREREG_FORECASTS, ftext), (cfg.PREREG_RULES, rtext))
    ]
    return out
