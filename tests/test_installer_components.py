"""Contract tests for selective installer component behavior."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class InstallerSandbox:
    source: Path
    home: Path
    env: dict[str, str]
    command_log: Path

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.source / "install.sh"), *args],
            cwd=self.source,
            env=self.env,
            capture_output=True,
            check=False,
            text=True,
        )


def _write_fake_command(path: Path, command_name: str) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "{command_name} $*" >> "$INSTALLER_COMMAND_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


@pytest.fixture
def installer_sandbox(tmp_path: Path) -> InstallerSandbox:
    source = tmp_path / "source"
    home = tmp_path / "home"
    fake_bin = tmp_path / "fake-bin"
    command_log = tmp_path / "commands.log"

    source.mkdir()
    home.mkdir()
    fake_bin.mkdir()

    shutil.copy2(REPO_ROOT / "install.sh", source / "install.sh")
    shutil.copy2(REPO_ROOT / ".env.example", source / ".env.example")
    shutil.copytree(REPO_ROOT / "hooks", source / "hooks")
    shutil.copytree(REPO_ROOT / "skills", source / "skills")
    (source / "multillm").mkdir()
    (source / "multillm" / "gateway.py").write_text("", encoding="utf-8")
    (source / "pyproject.toml").write_text(
        "[build-system]\n"
        'requires = ["setuptools"]\n'
        'build-backend = "setuptools.build_meta"\n',
        encoding="utf-8",
    )
    venv_bin = source / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    _write_fake_command(venv_bin / "python", "venv-python")

    for command_name in ("pip", "pip3", "codex"):
        _write_fake_command(fake_bin / command_name, command_name)

    env = {
        **os.environ,
        "HOME": str(home),
        "MULTILLM_INSTALL_DIR": str(source),
        "INSTALLER_COMMAND_LOG": str(command_log),
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
    }
    return InstallerSandbox(source, home, env, command_log)


def _combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return f"{result.stdout}\n{result.stderr}"


def _assert_no_mutation(sandbox: InstallerSandbox) -> None:
    assert list(sandbox.home.iterdir()) == []
    assert not (sandbox.source / ".env").exists()
    assert not sandbox.command_log.exists()


def test_list_components_is_complete_and_mutation_free(
    installer_sandbox: InstallerSandbox,
) -> None:
    result = installer_sandbox.run("--list-components")

    assert result.returncode == 0, _combined_output(result)
    output = _combined_output(result)
    for component in ("gateway", "codex-mcp", "codex-skills", "claude", "all"):
        assert component in output
    assert "gateway" in next(
        line for line in output.splitlines() if "codex-mcp" in line
    )
    assert "gateway" in next(line for line in output.splitlines() if "claude" in line)
    _assert_no_mutation(installer_sandbox)


@pytest.mark.parametrize(
    ("component", "included", "excluded"),
    [
        ("codex-mcp", ("gateway", "codex-mcp"), ("codex-skills", "claude")),
        ("claude", ("gateway", "claude"), ("codex-mcp", "codex-skills")),
        ("codex-skills", ("codex-skills",), ("gateway", "codex-mcp", "claude")),
    ],
)
def test_dry_run_expands_only_explicit_dependencies(
    installer_sandbox: InstallerSandbox,
    component: str,
    included: tuple[str, ...],
    excluded: tuple[str, ...],
) -> None:
    result = installer_sandbox.run("--dry-run", "--component", component)

    assert result.returncode == 0, _combined_output(result)
    resolved_line = next(
        line
        for line in _combined_output(result).splitlines()
        if line.startswith("Resolved components:")
    )
    for expected in included:
        assert expected in resolved_line
    for unexpected in excluded:
        assert unexpected not in resolved_line
    _assert_no_mutation(installer_sandbox)


def test_multiple_component_flags_are_repeatable(
    installer_sandbox: InstallerSandbox,
) -> None:
    result = installer_sandbox.run(
        "--component",
        "codex-skills",
        "--component",
        "codex-mcp",
        "--dry-run",
    )

    assert result.returncode == 0, _combined_output(result)
    resolved_line = next(
        line
        for line in _combined_output(result).splitlines()
        if line.startswith("Resolved components:")
    )
    assert "gateway" in resolved_line
    assert "codex-mcp" in resolved_line
    assert "codex-skills" in resolved_line
    assert "claude" not in resolved_line
    _assert_no_mutation(installer_sandbox)


def test_unknown_component_fails_before_any_mutation(
    installer_sandbox: InstallerSandbox,
) -> None:
    result = installer_sandbox.run("--component", "not-a-component")

    assert result.returncode != 0
    assert "Unknown component" in _combined_output(result)
    _assert_no_mutation(installer_sandbox)


def test_no_component_selection_defaults_to_all(
    installer_sandbox: InstallerSandbox,
) -> None:
    result = installer_sandbox.run("--dry-run")

    assert result.returncode == 0, _combined_output(result)
    output = _combined_output(result)
    assert "Selected components: all" in output
    resolved_line = next(
        line for line in output.splitlines() if line.startswith("Resolved components:")
    )
    for component in ("gateway", "codex-mcp", "codex-skills", "claude"):
        assert component in resolved_line
    _assert_no_mutation(installer_sandbox)


def test_codex_skills_can_install_alone_idempotently(
    installer_sandbox: InstallerSandbox,
) -> None:
    first = installer_sandbox.run("--component", "codex-skills")
    installed_skills = installer_sandbox.home / ".codex" / "skills"
    first_snapshot = sorted(
        path.relative_to(installed_skills)
        for path in installed_skills.rglob("*")
        if path.is_file()
    )

    second = installer_sandbox.run("--component", "codex-skills")
    second_snapshot = sorted(
        path.relative_to(installed_skills)
        for path in installed_skills.rglob("*")
        if path.is_file()
    )

    assert first.returncode == 0, _combined_output(first)
    assert second.returncode == 0, _combined_output(second)
    assert first_snapshot
    assert first_snapshot == second_snapshot
    assert not (installer_sandbox.home / ".multillm").exists()
    assert not (installer_sandbox.home / ".claude").exists()
    assert not (installer_sandbox.home / ".local").exists()
    assert not (installer_sandbox.source / ".env").exists()
    assert not installer_sandbox.command_log.exists()


def test_codex_mcp_installs_dependency_without_other_integrations(
    installer_sandbox: InstallerSandbox,
) -> None:
    result = installer_sandbox.run("--component", "codex-mcp")

    assert result.returncode == 0, _combined_output(result)
    assert (installer_sandbox.home / ".multillm").is_dir()
    installed_bin = installer_sandbox.home / ".local" / "bin"
    assert (installed_bin / "multillm-gateway").is_file()
    assert (installed_bin / "multillm-mcp").is_file()
    assert (installed_bin / "codex-multillm").is_file()
    assert not (installed_bin / "claude-multillm").exists()
    assert not (installer_sandbox.home / ".claude").exists()
    assert not (installer_sandbox.home / ".codex").exists()
    command_log = installer_sandbox.command_log.read_text(encoding="utf-8")
    assert "venv-python -m pip install" in command_log
    assert "codex mcp add multillm" in command_log


def test_no_arguments_retains_complete_installation(
    installer_sandbox: InstallerSandbox,
) -> None:
    result = installer_sandbox.run()

    assert result.returncode == 0, _combined_output(result)
    installed_bin = installer_sandbox.home / ".local" / "bin"
    for launcher in (
        "multillm-gateway",
        "multillm-mcp",
        "codex-multillm",
        "claude-multillm",
    ):
        assert (installed_bin / launcher).is_file()
    assert (installer_sandbox.home / ".codex" / "skills").is_dir()
    assert (installer_sandbox.home / ".claude" / "hooks.json").is_file()
    assert (installer_sandbox.home / ".claude" / ".mcp.json").is_file()
