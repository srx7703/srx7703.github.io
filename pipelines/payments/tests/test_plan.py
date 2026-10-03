"""The pinned constants in config.py must match the registered text of section H."""
from pathlib import Path

from pipelines.payments import config

PLAN = Path(__file__).resolve().parents[3] / "docs" / "EVALUATION_PLAN.md"


def section_h() -> str:
    text = PLAN.read_text(encoding="utf-8")
    start = text.index("## New payments companies (project H)")
    nxt = text.find("\n## ", start + 10)
    return text[start: nxt if nxt != -1 else None]


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


def test_section_exists_with_amendments_heading():
    h = section_h()
    assert "### Amendments to this section" in h
    assert "### Items" in h


def test_every_pinned_constant_is_registered():
    h = section_h()
    pinned = [config.UNIVERSE, config.NPM_CLIENT_SDKS, config.NPM_EXCLUDED_WRAPPERS, config.NPM_SERVER_SDKS,
              config.NOT_COMPANY_PACKAGES, config.ATS_BOARDS, config.ATS_EXCLUDED_TOKENS, config.METRIC_KINDS,
              config.NEVER_CHARTED_KINDS, config.STATUSES, config.Q1, config.Q2, config.Q3, config.Q4, config.Q5,
              config.Q7, config.Q8, config.Q9]
    missing = sorted({s for obj in pinned for s in _strings(obj) if s not in h})
    assert not missing, f"constants not in section H: {missing}"


def test_all_nine_questions_registered():
    h = section_h()
    for i in range(1, 10):
        assert f"**Q{i} " in h
