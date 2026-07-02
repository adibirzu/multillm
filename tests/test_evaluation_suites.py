from __future__ import annotations

import json
from pathlib import Path

from multillm.evaluation.suites import load_finops_suite, load_finops_agent_cases


def test_builtin_finops_suite_has_forty_stratified_owned_cases():
    cases = load_finops_suite()

    assert len(cases) == 40
    assert len({case.id for case in cases}) == 40
    categories = {case.category for case in cases}
    assert {
        "focus_nlp",
        "anomaly_detection",
        "management_reporting",
        "security",
    } <= categories
    assert all("finops" in case.tags for case in cases)


def test_finops_agent_import_normalizes_existing_golden_format(tmp_path: Path):
    source = tmp_path / "golden.json"
    source.write_text(
        json.dumps(
            [
                {
                    "id": "top-service",
                    "question": "What is the top service?",
                    "expected_tools": ["get_cost_data"],
                    "required_answer_terms": ["Compute"],
                    "forbidden_answer_terms": ["guess"],
                    "capabilities": ["reporting_analytics"],
                    "scenario_tags": ["live"],
                }
            ]
        ),
        encoding="utf-8",
    )

    cases = load_finops_agent_cases(source)

    assert cases[0].prompt == "What is the top service?"
    assert cases[0].expected_tools == ("get_cost_data",)
    assert cases[0].required_terms == ("Compute",)
    assert cases[0].category == "reporting_analytics"
    assert cases[0].tags == ("finops", "live")
