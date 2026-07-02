import sqlite3


def test_evaluation_migration_creates_audit_tables(tmp_path, monkeypatch):
    database = tmp_path / "multillm.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE system (id INTEGER PRIMARY KEY, key TEXT, value TEXT)")
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
        output_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(evaluation_outputs)")
        }
        metric_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(evaluation_metrics)")
        }

    assert {
        "evaluation_suites",
        "evaluation_cases",
        "evaluation_runs",
        "evaluation_outputs",
        "evaluation_metrics",
        "evaluation_comparisons",
        "evaluation_judgments",
        "evaluation_reviews",
    }.issubset(tables)
    assert "content_encrypted" in output_columns
    assert "output_text" not in output_columns
    assert "attempt" in metric_columns
