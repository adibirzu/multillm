# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Grep-invariant test: every Protocol method has tenant_id as first non-self arg, no default.

D-2a-03 locks the tenant_id-first signature shape. These tests are the
machine-readable form of the grep invariant — they will fail in CI if a
later phase reorders args or introduces a default value.
"""

import inspect

import pytest

from multillm.db.repo import MemoryRepo, SessionRepo, TrackingRepo


@pytest.fixture(params=[SessionRepo, TrackingRepo, MemoryRepo])
def repo_protocol(request):
    return request.param


def _public_methods(protocol_cls):
    """Yield (name, method) for every non-dunder method declared on a Protocol class."""
    for name, member in vars(protocol_cls).items():
        if name.startswith("_"):
            continue
        if not callable(member):
            continue
        yield name, member


def test_every_method_takes_tenant_id_first(repo_protocol):
    """First non-self positional arg on every Protocol method must be `tenant_id`."""
    for method_name, method in _public_methods(repo_protocol):
        sig = inspect.signature(method)
        params = list(sig.parameters.values())
        # Skip the self parameter
        if params and params[0].name == "self":
            params = params[1:]
        assert params, f"{repo_protocol.__name__}.{method_name} has no non-self params"
        first = params[0]
        assert first.name == "tenant_id", (
            f"{repo_protocol.__name__}.{method_name}: first non-self arg is "
            f"{first.name!r}, expected 'tenant_id'"
        )
        assert first.annotation is str, (
            f"{repo_protocol.__name__}.{method_name}: tenant_id annotation is "
            f"{first.annotation!r}, expected str"
        )
        assert first.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD, (
            f"{repo_protocol.__name__}.{method_name}: tenant_id kind is "
            f"{first.kind}, expected POSITIONAL_OR_KEYWORD"
        )
        assert first.default is inspect.Parameter.empty, (
            f"{repo_protocol.__name__}.{method_name}: tenant_id has a default "
            f"({first.default!r}); D-2a-03 requires no default"
        )


def test_method_counts():
    """Lock in the expected method counts so phase-2b changes are conspicuous."""
    assert len(list(_public_methods(SessionRepo))) == 4
    assert len(list(_public_methods(TrackingRepo))) == 3
    assert len(list(_public_methods(MemoryRepo))) == 5
