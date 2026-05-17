# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Run the MultiLLM CLI: ``python -m multillm``.

Dispatches into the Click app defined in ``multillm.cli``. ``multillm serve``
launches the legacy gateway (preserves the previous behavior of
``python -m multillm`` for users who relied on it); ``multillm migrate ...``
exposes the new Phase 1 migration framework.
"""

from multillm.cli import app

app()
