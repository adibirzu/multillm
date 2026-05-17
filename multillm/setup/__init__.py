# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""First-run setup wizard package (Plan 01-07).

Public surface:

- ``multillm.setup.passwords`` — Argon2id hash/verify helpers
- ``multillm.setup.state`` — setup state machine + DB CRUD
- ``multillm.setup.middleware`` — ``SetupRedirectMiddleware``
- ``multillm.setup.routes`` — ``router`` mounted under ``/setup``

Phase 2b inherits this package's password module, admin_users table, and
state machine without re-implementing them.
"""
