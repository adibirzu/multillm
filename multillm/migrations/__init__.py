# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Alembic migration framework for MultiLLM.

The package ships an Alembic env wired to SQLite at ``$MULTILLM_HOME/multillm.db``,
a backup helper that snapshots the DB before any upgrade, an FTS5 rebuild helper,
and a runner that wraps Alembic's CLI primitives in a programmatic API consumed
by ``multillm/cli.py``.
"""
