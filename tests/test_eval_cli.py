import json

from click.testing import CliRunner

from multillm import cli
from multillm.evaluation.artifacts import ArtifactCipher
from multillm.evaluation.store import EvaluationStore


def test_eval_preflight_prints_receipt_and_live_proof(monkeypatch):
    calls = []

    def request(method, gateway, path, payload=None):
        calls.append((method, path, payload))
        return {
            "success": True,
            "data": {
                "receipt": "evalpf_1234567890abcdef",
                "executionMode": "live_host",
                "sandboxFallback": False,
                "targets": [{"alias": "codex/gpt-5-5", "executionVerified": True}],
            },
        }

    monkeypatch.setattr(cli, "_eval_http_json", request)
    result = CliRunner().invoke(
        cli.app,
        ["eval", "preflight", "--target", "codex/gpt-5-5"],
    )

    assert result.exit_code == 0
    assert "evalpf_1234567890abcdef" in result.output
    assert "live_host" in result.output
    assert calls[0][1] == "/api/evaluations/preflight"


def test_eval_run_live_preflights_candidates_judges_and_moa_before_create(monkeypatch):
    calls = []

    def request(method, gateway, path, payload=None):
        calls.append((method, path, payload))
        if path == "/api/evaluations/preflight":
            return {"success": True, "data": {"receipt": "evalpf_1234567890abcdef"}}
        return {
            "success": True,
            "data": {"id": "eval_1234567890abcdef1234", "status": "queued"},
        }

    monkeypatch.setattr(cli, "_eval_http_json", request)
    result = CliRunner().invoke(
        cli.app,
        [
            "eval",
            "run",
            "--suite",
            "finops-v1",
            "--target",
            "codex/gpt-5-5",
            "--target",
            "claude-cli/sonnet",
            "--moa",
            "moa/quality",
            "--judge",
            "gemini-cli/pro",
            "--judge",
            "claude-cli/opus",
            "--live",
        ],
    )

    assert result.exit_code == 0
    assert "eval_1234567890abcdef1234" in result.output
    assert calls[0][1] == "/api/evaluations/preflight"
    assert calls[0][2]["targets"] == [
        "codex/gpt-5-5",
        "claude-cli/sonnet",
        "gemini-cli/pro",
        "claude-cli/opus",
    ]
    created = calls[1][2]
    assert created["execution_mode"] == "live_host"
    assert created["preflight_receipt"] == "evalpf_1234567890abcdef"
    assert created["judge_pool"] == ["gemini-cli/pro", "claude-cli/opus"]


def test_eval_run_all_live_discovers_deduplicates_and_then_execution_probes(
    monkeypatch,
):
    calls = []

    def request(method, gateway, path, payload=None):
        calls.append((method, path, payload))
        if path == "/api/evaluations/live-targets":
            return {
                "success": True,
                "data": {
                    "targets": [
                        {"alias": "codex/gpt-5-5"},
                        {"alias": "claude-cli/sonnet"},
                        {"alias": "gemini-cli/flash"},
                    ]
                },
            }
        if path == "/api/evaluations/preflight":
            return {"success": True, "data": {"receipt": "evalpf_1234567890abcdef"}}
        return {
            "success": True,
            "data": {"id": "eval_1234567890abcdef1234", "status": "queued"},
        }

    monkeypatch.setattr(cli, "_eval_http_json", request)
    result = CliRunner().invoke(
        cli.app,
        [
            "eval",
            "run",
            "--all-live",
            "--live",
            "--judge",
            "gemini-cli/flash",
            "--judge",
            "claude-cli/sonnet",
        ],
    )

    assert result.exit_code == 0, result.output
    assert [call[1] for call in calls] == [
        "/api/evaluations/live-targets",
        "/api/evaluations/preflight",
        "/api/evaluations/runs",
    ]
    assert calls[1][2]["targets"] == [
        "codex/gpt-5-5",
        "gemini-cli/flash",
        "claude-cli/sonnet",
    ]
    assert calls[2][2]["candidates"] == ["codex/gpt-5-5"]
    assert calls[2][2]["candidate_scope"] == "live"


def test_eval_status_can_emit_machine_readable_json(monkeypatch):
    monkeypatch.setattr(
        cli,
        "_eval_http_json",
        lambda *args, **kwargs: {
            "success": True,
            "data": {"id": "eval_1234567890abcdef1234", "status": "completed"},
        },
    )
    result = CliRunner().invoke(
        cli.app,
        ["eval", "status", "eval_1234567890abcdef1234", "--json-output"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["status"] == "completed"


def test_eval_suite_import_accepts_finops_agent_golden_cases(monkeypatch, tmp_path):
    source = tmp_path / "golden.json"
    source.write_text(
        json.dumps(
            [
                {
                    "id": "case-one",
                    "question": "What changed?",
                    "capabilities": ["anomaly_detection"],
                    "scenario_tags": ["live"],
                }
            ]
        ),
        encoding="utf-8",
    )
    store = EvaluationStore(
        tmp_path / "eval.db", artifact_cipher=ArtifactCipher(bytes(range(32)))
    )
    monkeypatch.setattr(cli, "get_evaluation_store", lambda: store)

    result = CliRunner().invoke(
        cli.app,
        [
            "eval",
            "suite-import",
            str(source),
            "--suite-id",
            "finops-agent-v1",
            "--tenant",
            "tenant-a",
        ],
    )

    assert result.exit_code == 0
    assert "1 cases" in result.output
    assert store.get_suite("tenant-a", "finops-agent-v1")["caseCount"] == 1


def test_eval_export_writes_the_requested_audit_artifact(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_eval_http_bytes", lambda *args, **kwargs: b"audit-data")
    output = tmp_path / "run.csv"
    result = CliRunner().invoke(
        cli.app,
        [
            "eval",
            "export",
            "eval_1234567890abcdef1234",
            "--format",
            "csv",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    assert output.read_bytes() == b"audit-data"
    assert str(output) in result.output
