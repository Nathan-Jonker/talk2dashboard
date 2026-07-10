from __future__ import annotations

import json

from talk2dashboard.config import ROOT


def test_evaluation_suite_covers_twelve_distinct_scenarios() -> None:
    cases = json.loads((ROOT / "data/evaluation/cases.json").read_text())
    assert len(cases) == 12
    assert len({case["id"] for case in cases}) == 12
    assert {"websearch-disabled", "websearch-enabled", "screenshot-qa"} <= {
        case["id"] for case in cases
    }
