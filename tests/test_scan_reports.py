# SPDX-License-Identifier: Apache-2.0

import csv
import io

from multillm.orchestration_store import OrchestrationStore


def _report_payload():
    return {
        "source": "dependency-scanner",
        "project": "payments-api",
        "title": "June dependency scan",
        "findings": [
            {
                "externalId": "CVE-2026-0001",
                "severity": "critical",
                "category": "vulnerability",
                "title": "Remote code execution",
                "resource": "requests==2.0",
                "status": "open",
            },
            {
                "externalId": "POL-7",
                "severity": "medium",
                "category": "policy",
                "title": "Encryption policy gap",
                "resource": "s3://audit-logs",
                "status": "accepted",
            },
        ],
    }


def test_scan_report_is_tenant_scoped_and_produces_a_management_summary(tmp_path):
    store = OrchestrationStore(tmp_path / "reports.db")
    report_id = store.create_scan_report("tenant-a", _report_payload())
    store.create_scan_report("tenant-b", {**_report_payload(), "project": "other"})

    report = store.get_scan_report("tenant-a", report_id)
    assert report is not None
    assert report["project"] == "payments-api"
    assert report["summary"] == {"critical": 1, "medium": 1, "total": 2}
    assert store.get_scan_report("tenant-b", report_id) is None

    summary = store.get_scan_summary("tenant-a")
    assert summary["reports"] == 1
    assert summary["findingsBySeverity"]["critical"] == 1
    assert summary["findingsByStatus"]["accepted"] == 1


def test_scan_report_export_is_audit_ready_and_excludes_other_tenants(tmp_path):
    store = OrchestrationStore(tmp_path / "reports.db")
    store.create_scan_report("tenant-a", _report_payload())
    store.create_scan_report("tenant-b", _report_payload())

    exported = store.export_scan_findings("tenant-a")
    assert len(exported) == 2
    assert {row["severity"] for row in exported} == {"critical", "medium"}
    assert {row["project"] for row in exported} == {"payments-api"}

    csv_rows = list(csv.DictReader(io.StringIO(store.scan_findings_csv("tenant-a"))))
    assert len(csv_rows) == 2
    assert csv_rows[0]["externalId"] == "CVE-2026-0001"
