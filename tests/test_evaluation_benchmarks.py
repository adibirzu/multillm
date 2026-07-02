import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from multillm.evaluation.api import router
from multillm.evaluation.benchmarks import BENCHMARKS, load_benchmark_jsonl


def test_registry_distinguishes_original_moa_benchmarks_from_arena_hard():
    assert set(BENCHMARKS) == {"alpacaeval-2", "mt-bench", "flask", "arena-hard"}
    assert {key for key, item in BENCHMARKS.items() if item.original_moa} == {
        "alpacaeval-2",
        "mt-bench",
        "flask",
    }
    assert BENCHMARKS["alpacaeval-2"].official_metric == "length_controlled_win_rate"
    assert BENCHMARKS["mt-bench"].official_metric == "turn_score_1_to_10"
    assert BENCHMARKS["arena-hard"].supplemental is True
    assert BENCHMARKS["alpacaeval-2"].customer_redistributable is False


def test_generic_jsonl_adapter_creates_versioned_cases_and_source_hash(tmp_path):
    source = tmp_path / "questions.jsonl"
    source.write_text(
        "\n".join(
            [
                json.dumps(
                    {"question_id": 1, "text": "First prompt", "category": "reasoning"}
                ),
                json.dumps(
                    {"question_id": 2, "text": "Second prompt", "category": "coding"}
                ),
            ]
        ),
        encoding="utf-8",
    )

    imported = load_benchmark_jsonl(
        source,
        benchmark_id="mt-bench",
        prompt_field="text",
        id_field="question_id",
        category_field="category",
    )

    assert len(imported.cases) == 2
    assert imported.cases[0].id == "mt-bench:1"
    assert len(imported.source_sha256) == 64
    assert imported.source_path == str(source.resolve())


def test_benchmark_manifest_is_exposed_without_downloading_restricted_data():
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).get("/api/evaluations/benchmarks")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    alpaca = next(item for item in payload["data"] if item["id"] == "alpacaeval-2")
    assert alpaca["downloadMode"] == "on_demand"
    assert alpaca["customerRedistributable"] is False
