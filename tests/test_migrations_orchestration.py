# SPDX-License-Identifier: Apache-2.0

import sqlite3


def test_adaptive_orchestration_migration_creates_tenant_scoped_tables(
    tmp_path, monkeypatch
):
    database = tmp_path / "multillm.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE system (id INTEGER PRIMARY KEY, key TEXT, value TEXT)"
        )
    monkeypatch.setenv("MULTILLM_DB_PATH", str(database))

    from multillm.migrations.runner import migrate_up

    assert migrate_up() == "0006_evaluation_metric_attempts"
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(orchestration_runs)")
        }

    assert {
        "orchestration_runs",
        "orchestration_calls",
        "orchestration_feedback",
        "model_scorecards",
    }.issubset(tables)
    assert "tenant_id" in columns
    assert "prompt_hash" in columns
    assert "prompt" not in columns
