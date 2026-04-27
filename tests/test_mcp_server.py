"""Tests for MCP tool input contracts."""

import pytest
from pydantic import ValidationError

from multillm.mcp_server import UsageInput


def test_usage_input_accepts_multi_year_windows():
    params = UsageInput(hours=17520)

    assert params.hours == 17520


def test_usage_input_rejects_windows_beyond_dashboard_cap():
    with pytest.raises(ValidationError):
        UsageInput(hours=43801)
