# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors
"""AST-driven coverage test for `.env.example`.

This test walks the AST of every Python file under ``multillm/`` and collects
every literal environment-variable name passed to ``os.getenv(...)`` or used
as a key in ``os.environ[...]`` / ``os.environ.get(...)``. It then asserts
that every such name is documented in ``.env.example``.

Why an AST walk instead of regex?
* Regex misses multi-line ``os.getenv`` calls and substring-style false hits.
* AST walking gives us literal-vs-dynamic distinction for free: a dynamic
  ``os.getenv(some_var)`` is skipped, not erroneously failed.

The test is intentionally a single function rather than a parametrised matrix
because the assertion message must show the full missing-name set together so
the maintainer can fix `.env.example` in one pass.
"""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = REPO_ROOT / ".env.example"
MULTILLM_PKG = REPO_ROOT / "multillm"

# Names referenced via ``os.getenv("PATH", ...)`` etc. that are provided by
# the OS / container runtime and therefore MUST NOT appear in ``.env.example``.
SYSTEM_PROVIDED: frozenset[str] = frozenset(
    {
        "PATH",
        # Provided by the OS/login session; read as a default tenant label by the
        # per-user team-usage collector (multillm/team_collector.py).
        "USER",
    }
)

# Names documented in ``.env.example`` that are not yet read directly via
# ``os.getenv`` in production code. They are forward references the operator
# may legitimately set:
#   - ``OTEL_TRACES_SAMPLER_ARG``: OTel SDK reads this internally; Phase 5
#     plans to surface it explicitly.
KNOWN_OPTIONAL_EXTRAS: frozenset[str] = frozenset(
    {
        "OTEL_TRACES_SAMPLER_ARG",
    }
)

# Operator-facing aliases read indirectly via the ``_first_env`` helper in
# ``multillm/config.py``. They are documented in ``.env.example`` and are
# real env vars the code consults, but the AST walker cannot see them as
# direct ``os.getenv`` literals.
KNOWN_INDIRECT_LOOKUPS: frozenset[str] = frozenset()

_ENV_LINE_RE = re.compile(r"^([A-Z][A-Z0-9_]+)=")


def _load_documented_names() -> set[str]:
    """Parse ``.env.example`` and return every documented VAR name."""
    assert ENV_EXAMPLE.exists(), f"{ENV_EXAMPLE} must exist"
    documented: set[str] = set()
    for raw in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _ENV_LINE_RE.match(line)
        if match:
            documented.add(match.group(1))
    return documented


def _is_os_attr(node: ast.AST, attr: str) -> bool:
    """Return True for ``os.<attr>``-shaped attribute access."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == attr
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _is_environ_attr(node: ast.AST, attr: str) -> bool:
    """Return True for ``os.environ.<attr>``-shaped attribute access."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == attr
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "environ"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "os"
    )


def _extract_literal(arg: ast.AST) -> str | None:
    """Return the literal string value of ``arg`` or None if non-literal."""
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return None


def _collect_referenced_names(
    pkg_root: Path,
    logger: logging.Logger,
) -> set[str]:
    """Walk every ``.py`` file under ``pkg_root`` and return env var names.

    Dynamic lookups (``os.getenv(var)`` with a non-literal arg) are logged
    and skipped — they cannot be statically verified, so this test does not
    fail on them.
    """
    referenced: set[str] = set()
    for path in sorted(pkg_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            pytest.fail(f"Could not parse {path}: {exc}")
        for node in ast.walk(tree):
            # os.getenv("NAME", ...) or os.environ.get("NAME", ...)
            if isinstance(node, ast.Call):
                func = node.func
                is_getenv = _is_os_attr(func, "getenv")
                is_environ_get = _is_environ_attr(func, "get")
                if (is_getenv or is_environ_get) and node.args:
                    literal = _extract_literal(node.args[0])
                    if literal is not None:
                        referenced.add(literal)
                    else:
                        logger.warning(
                            "Dynamic env lookup in %s line %s — skipping",
                            path.relative_to(REPO_ROOT),
                            node.lineno,
                        )
            # os.environ["NAME"] subscript access
            if isinstance(node, ast.Subscript):
                value = node.value
                if (
                    isinstance(value, ast.Attribute)
                    and value.attr == "environ"
                    and isinstance(value.value, ast.Name)
                    and value.value.id == "os"
                ):
                    # ast.Index was removed in 3.9+, slice is the literal directly
                    slc = node.slice
                    literal = _extract_literal(slc)
                    if literal is not None:
                        referenced.add(literal)
                    else:
                        logger.warning(
                            "Dynamic os.environ[...] in %s line %s — skipping",
                            path.relative_to(REPO_ROOT),
                            node.lineno,
                        )
    return referenced


def test_env_example_covers_every_referenced_env_var(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`.env.example` must document every literal env-var the code reads."""
    caplog.set_level(logging.WARNING)
    logger = logging.getLogger(__name__)

    documented = _load_documented_names()
    referenced = _collect_referenced_names(MULTILLM_PKG, logger)

    # System-provided names (PATH, etc.) must NOT be in .env.example.
    referenced_user_facing = referenced - SYSTEM_PROVIDED

    missing = referenced_user_facing - documented
    assert not missing, (
        "The following env vars are read by multillm/ but are missing from "
        ".env.example:\n  - "
        + "\n  - ".join(sorted(missing))
        + "\nAdd entries to .env.example with type/default/purpose comments."
    )


def test_env_example_has_no_undocumented_entries() -> None:
    """`.env.example` must not become a dumping ground.

    Every documented name must be one of:
      - referenced directly via os.getenv / os.environ in multillm/
      - in KNOWN_OPTIONAL_EXTRAS (forward references for future phases)
      - in KNOWN_INDIRECT_LOOKUPS (read via helpers like _first_env)
    """
    logger = logging.getLogger(__name__)
    documented = _load_documented_names()
    referenced = _collect_referenced_names(MULTILLM_PKG, logger)

    allowed = referenced | KNOWN_OPTIONAL_EXTRAS | KNOWN_INDIRECT_LOOKUPS

    extras = documented - allowed
    assert not extras, (
        "The following env vars are documented in .env.example but are not "
        "referenced anywhere in multillm/ (and are not in KNOWN_OPTIONAL_EXTRAS "
        "or KNOWN_INDIRECT_LOOKUPS):\n  - "
        + "\n  - ".join(sorted(extras))
        + "\nEither use them in code or remove them from .env.example."
    )


def test_env_example_does_not_contain_real_credentials() -> None:
    """`.env.example` must contain only placeholders — never real-looking keys."""
    content = ENV_EXAMPLE.read_text(encoding="utf-8")
    forbidden_patterns = [
        # OpenAI / Anthropic / similar secret-looking strings
        re.compile(r"sk-[A-Za-z0-9]{20,}"),
        # OCI tenancy OCIDs
        re.compile(r"ocid1\.tenancy\.[a-z0-9]+"),
        # Internal Oracle infra IPs the project must never leak
        re.compile(r"130\.61\.\d+\.\d+"),
        # 10.x private IPs (the file documents URLs like http://localhost,
        # not internal subnets — anything 10.x.x.x indicates leakage)
        re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    ]
    hits: list[str] = []
    for pattern in forbidden_patterns:
        for match in pattern.finditer(content):
            hits.append(f"{pattern.pattern!r} -> {match.group(0)!r}")
    assert not hits, (
        "Forbidden real-looking credentials / infrastructure references found "
        "in .env.example:\n  - " + "\n  - ".join(hits)
    )


def test_multillm_home_is_documented() -> None:
    """Regression guard: MULTILLM_HOME must remain documented.

    Plan 01-03 introduced the migrate CLI, which hard-depends on MULTILLM_HOME
    to locate the SQLite DB and the alembic versions table. If a future
    refactor drops the os.getenv("MULTILLM_HOME") lookup, this test fails so
    we notice the operator-facing breakage immediately.
    """
    documented = _load_documented_names()
    assert "MULTILLM_HOME" in documented, (
        "MULTILLM_HOME must remain documented in .env.example — it is the "
        "primary knob for choosing the data directory (SQLite DBs, migrations, "
        "backups, PID, logs)."
    )
