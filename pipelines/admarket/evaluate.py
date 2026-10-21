"""The registered items and their status, for the page's evaluation table (``data/marts/admarket/evaluation.json``).

Q2 and Q3 were computed once, exactly as registered, on data that existed but had not been computed when the rules
were written. Q1 and Q5 look forward and stay ``collecting`` until 2027Q2 is in. Rule A and rule B are scored as each
company reports 2026Q3 and 2026Q4. Q4 is a method check set in the research plan, not a registered test; Q6 is
exploratory and not built here.
"""

from __future__ import annotations

from typing import Any


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%".replace("-", "−")


def items(q1: dict, q2: dict, q3: dict, q4: dict, q5: dict, a: dict, b: list[dict]) -> list[dict[str, Any]]:
    out = [
        {"id": "Q1", "question": "Is Google's share of the video subset still falling?", "kind": "registered, forward",
         "status": q1["status"] if q1["status"] != "resolved" else f"resolved: {q1['verdict']}",
         "detail": f"{q1['quarters_in']} of {q1['quarters_needed']} quarters (2026Q3 to 2027Q2) in"},
        {"id": "Q2", "question": "Did Apple's ATT hurt cross-app-tracking platforms more than search?",
         "kind": "registered, data existed", "status": f"resolved: {q2['verdict']}",
         "detail": f"affected group's drop exceeds the control group's by {q2['gap'] * 100:.1f} points (threshold "
                   f"{q2['threshold'] * 100:.0f})"},
        {"id": "Q3", "question": "Is the open web losing ground to the walled gardens?",
         "kind": "registered, data existed", "status": f"resolved: {q3['verdict']}",
         "detail": f"open web {_pct(q3['open'])} a year against walled gardens {_pct(q3['walled'])} (threshold "
                   f"{q3['threshold'] * 100:.0f} points)"},
        {"id": "Q4", "question": "Does volume x price reproduce reported growth?",
         "kind": "method check (research plan)",
         "status": "resolved",
         "detail": "; ".join(f"{k}: max gap {v['max_abs_gap_pp']} points, "
                             f"{'usable' if v['usable'] else 'fails the strict rule'}"
                             for k, v in q4["verdict"].items())},
        {"id": "Q5", "question": "Is the AppLovin / Trade Desk growth gap structural?", "kind": "registered, forward",
         "status": q5["status"] if q5["status"] != "resolved" else f"resolved: {q5['verdict']}",
         "detail": f"{q5['quarters_in']} of {q5['quarters_needed']} quarters in"},
        {"id": "Q6", "question": "Does compute spending go with advertising growth?", "kind": "exploratory",
         "status": "not built", "detail": "registered as a description only; not part of this page yet"},
        {"id": "A", "question": "Rule A: 80% intervals for 2026Q3 and 2026Q4", "kind": "registered forecast",
         "status": a["status"] if a["status"] != "resolved" else f"resolved: {a['verdict']}",
         "detail": f"{a['n_scored']} of {a['n']} intervals scored, {a['hits']} inside"},
    ]
    for c in b:
        out.append({"id": c["id"], "question": c["call"], "kind": "registered call (rule B)", "status": c["status"],
                    "detail": "" if c["status"] == "pending"
                    else "first reported 2026Q3 figure against the 2025Q3 panel"})
    return out
