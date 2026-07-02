"""Versioned owned suites and import adapters for external golden cases."""

from __future__ import annotations

import json
from pathlib import Path

from .contracts import EvaluationCase


_DATA_DIR = Path(__file__).resolve().parent / "data"


def load_finops_suite() -> tuple[EvaluationCase, ...]:
    payload = json.loads((_DATA_DIR / "finops_v1.json").read_text(encoding="utf-8"))
    return tuple(EvaluationCase.model_validate(item) for item in payload)


def load_finops_agent_cases(path: str | Path) -> tuple[EvaluationCase, ...]:
    source = Path(path)
    if source.stat().st_size > 5_000_000:
        raise ValueError("FinOps golden-case file is too large")
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or len(payload) > 10_000:
        raise ValueError("FinOps golden cases must be an array of at most 10000 items")
    cases: list[EvaluationCase] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("each FinOps golden case must be an object")
        capabilities = item.get("capabilities") or []
        scenarios = item.get("scenario_tags") or []
        cases.append(
            EvaluationCase(
                id=item.get("id", ""),
                prompt=item.get("question", ""),
                category=str(capabilities[0] if capabilities else "general"),
                expected_tools=tuple(item.get("expected_tools") or ()),
                required_terms=tuple(item.get("required_answer_terms") or ()),
                forbidden_terms=tuple(item.get("forbidden_answer_terms") or ()),
                tags=tuple(dict.fromkeys(("finops", *map(str, scenarios)))),
                metadata={"capabilities": capabilities},
            )
        )
    return tuple(cases)
