# SPDX-License-Identifier: Apache-2.0

from fastapi.testclient import TestClient

from multillm import gateway
from multillm.orchestration_store import OrchestrationStore


def _payload():
    return {
        "source": "security-scanner",
        "project": "ledger-api",
        "title": "Weekly scan",
        "findings": [{
            "externalId": "VULN-1", "severity": "high", "category": "dependency",
            "title": "Known vulnerable package", "resource": "package-a", "status": "open",
        }],
    }


def test_scan_report_api_ingests_lists_and_exports_only_current_tenant(tmp_path, monkeypatch):
    store = OrchestrationStore(tmp_path / "scan-api.db")
    monkeypatch.setattr(gateway, "_orchestration_store", lambda: store)
    client = TestClient(gateway.app)

    created = client.post("/api/scan-reports", json=_payload(), headers={"X-MultiLLM-Tenant": "tenant-a"})
    assert created.status_code == 201
    report_id = created.json()["data"]["id"]
    assert client.post("/api/scan-reports", json={**_payload(), "project": "other"}, headers={"X-MultiLLM-Tenant": "tenant-b"}).status_code == 201

    listing = client.get("/api/scan-reports", headers={"X-MultiLLM-Tenant": "tenant-a"})
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()["data"]] == [report_id]
    assert client.get(f"/api/scan-reports/{report_id}", headers={"X-MultiLLM-Tenant": "tenant-b"}).status_code == 404

    summary = client.get("/api/scan-reports/summary", headers={"X-MultiLLM-Tenant": "tenant-a"})
    assert summary.json()["data"]["findingsBySeverity"] == {"high": 1}
    exported = client.get("/api/scan-reports/export?format=csv", headers={"X-MultiLLM-Tenant": "tenant-a"})
    assert exported.status_code == 200
    assert "VULN-1" in exported.text
    assert "other" not in exported.text
