# SPDX-License-Identifier: Apache-2.0

"""Both supported coding clients must receive the same Fusion MCP tool."""

import json
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_codex_and_claude_share_the_multillm_mcp_server():
    codex = tomllib.loads((ROOT / ".codex" / "config.toml").read_text())
    claude = json.loads((ROOT / ".mcp.json").read_text())

    assert codex["mcp_servers"]["multillm"]["args"] == [
        "-m",
        "multillm.mcp_server",
    ]
    assert claude["mcpServers"]["multillm"]["args"] == [
        "-m",
        "multillm.mcp_server",
    ]
