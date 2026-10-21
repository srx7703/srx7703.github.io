"""What the advertising pipeline commits: small derived extracts, no XBRL instances, no contact address."""

from __future__ import annotations

import gzip
import json
import re

from pipelines.admarket import config as cfg
from pipelines.common.storage import REPO_ROOT

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _files():
    return [p for p in cfg.RAW.rglob("*") if p.is_file()]


def test_no_instance_or_filing_document_is_committed():
    bad = [p.name for p in _files() if p.suffix in (".xml", ".htm", ".html", ".zip")]
    assert not bad, bad
    assert max(p.stat().st_size for p in _files()) < 200_000   # an instance is megabytes; an extract is kilobytes
    assert sum(p.stat().st_size for p in _files()) < 5_000_000


def test_every_extract_holds_revenue_facts_only():
    for p in (cfg.RAW_XBRL).rglob("*.json.gz"):
        with gzip.open(p, "rt") as f:
            rec = json.load(f)
        assert set(rec) <= {"ticker", "form", "period", "filed", "accession", "instance", "source", "extracted_at", "facts"}
        assert {x["concept"] for x in rec["facts"]} <= set(cfg.REV), p.name


def test_no_email_address_in_any_committed_admarket_file():
    """SEC_USER_AGENT carries a contact address; it lives in the environment only."""
    paths = _files() + list((REPO_ROOT / "data" / "marts" / "admarket").glob("*.json")) + [cfg.FACTS_PATH]
    for p in paths:
        text = gzip.open(p, "rt").read() if p.suffix == ".gz" else p.read_text(encoding="utf-8", errors="replace")
        if p.parent.name in ("META", "GOOGL") and "text" in p.parts:
            continue   # filing text windows quote the filings; checked below for the fields we write
        assert not EMAIL.search(text), p


def test_text_records_carry_no_user_agent_field():
    for p in cfg.RAW_TEXT.rglob("*.json.gz"):
        with gzip.open(p, "rt") as f:
            rec = json.load(f)
        assert set(rec) <= {"ticker", "form", "period", "filed", "accession", "source", "snippets", "parsed",
                            "extracted_at"}
        assert not EMAIL.search(rec["source"])


def test_workflow_takes_the_user_agent_from_a_secret():
    wf = (REPO_ROOT / ".github" / "workflows" / "admarket.yml").read_text()
    assert "SEC_USER_AGENT: ${{ secrets.SEC_USER_AGENT }}" in wf
    found = [e for e in EMAIL.findall(wf) if not re.search(r"@v\d", e)]   # action refs look like addresses
    assert found == ["portfolio-bot@users.noreply.github.com"]
