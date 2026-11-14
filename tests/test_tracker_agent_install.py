from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tests import test_tracker_plugin
from tests.test_tracker_mcp import BOOT

package = test_tracker_plugin.package


@pytest.mark.parametrize("scope", [[], ["--user"], ["--scope", "user"]])
@pytest.mark.parametrize("git_repository", [False, True])
def test_user_install_ships_vault_and_mcp_guidance_without_repository_files(
    package: Path, tmp_path: Path, scope: list[str], git_repository: bool
) -> None:
    home = tmp_path / "home"
    work = tmp_path / "working"
    work.mkdir()
    if git_repository:
        subprocess.run(["git", "init", "-q", str(work)], check=True)
    before = {
        path.relative_to(work): path.read_bytes() for path in work.rglob("*") if path.is_file()
    }
    env = os.environ.copy()
    env.update(HOME=str(home), USERPROFILE=str(home))
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", BOOT, str(package / "__init__.py"), "init", *scope],
        capture_output=True,
        text=True,
        env=env,
        cwd=work,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    for host in (".claude", ".agents"):
        skill = home / host / "skills/basicly-tracker/SKILL.md"
        body = skill.read_text()
        assert ".basicly-tracker.toml" in body
        assert "mcp --repository" in body
        assert "BASICLY_TRACKER_SESSION" in body
        assert "push the ledger at once" not in body
        assert "Only a settled" in body
    assert {
        path.relative_to(work): path.read_bytes() for path in work.rglob("*") if path.is_file()
    } == before


@pytest.mark.parametrize("scope", [[], ["--scope", "project"]])
def test_repository_sandbox_ships_the_same_agent_adapter_and_guidance(
    package: Path, tmp_path: Path, scope: list[str]
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "AGENTS.md").write_text("# Existing project guidance\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    env = os.environ.copy()
    env.update(
        HOME=str(tmp_path / "home"),
        USERPROFILE=str(tmp_path / "home"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
        LOCALAPPDATA=str(tmp_path / "cache"),
    )
    installed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            BOOT,
            str(package / "__init__.py"),
            "init",
            *scope,
            "--sandbox",
            "--into",
            str(root),
            "--with-instructions",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert installed.returncode == 0, installed.stderr
    for host in (".claude", ".agents"):
        body = (root / host / "skills/tracker/SKILL.md").read_text()
        assert ".basicly-tracker.toml" in body
        assert "mcp --repository" in body
    assert ".basicly-tracker.toml" in (root / "AGENTS.md").read_text()
    bundle = root / ".basicly/tracker.pyz"
    checked = subprocess.run(
        [sys.executable, "-I", "-S", str(bundle), "mcp", "--help"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr
    assert "--repository" in checked.stdout
    registration = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(bundle),
            "mcp-config",
            "--client",
            "codex",
            "--scope",
            "project",
            "--repository",
            str(root),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert registration.returncode == 0, registration.stderr
    document = json.loads(registration.stdout)["configuration"]
    server = tomllib.loads(document)["mcp_servers"]["basicly-tracker"]
    assert server["command"] != "basicly-tracker"
    assert server["args"] == [".basicly/tracker.pyz", "mcp", "--repository", "."]
    assert str(root) not in document
    assert not (tmp_path / "home/.agents/skills/basicly-tracker").exists()


@pytest.mark.parametrize("client", ["codex", "claude"])
def test_registration_defaults_to_user_scope_and_forwards_a_dynamic_session(
    package: Path, tmp_path: Path, client: str
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    before = {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    done = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            BOOT,
            str(package / "__init__.py"),
            "mcp-config",
            "--client",
            client,
            "--repository",
            str(root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    assert report["scope"] == "user" and report["written"] is False
    if client == "codex":
        server = tomllib.loads(report["configuration"])["mcp_servers"]["basicly-tracker"]
        assert server["env_vars"] == [
            "BASICLY_TRACKER_SESSION",
            "BASICLY_HOLDER",
            "BASICLY_TRACKER_STATE",
            "XDG_STATE_HOME",
            "LOCALAPPDATA",
        ]
        assert server["env"] == {"AI_AGENT": "codex"}
    else:
        server = json.loads(report["configuration"])["mcpServers"]["basicly-tracker"]
        assert server["env"]["BASICLY_TRACKER_SESSION"] == "${BASICLY_TRACKER_SESSION}"
        assert server["env"]["AI_AGENT"] == "claude"
        for name in ("BASICLY_HOLDER", "BASICLY_TRACKER_STATE", "XDG_STATE_HOME", "LOCALAPPDATA"):
            assert server["env"][name] == "${" + name + ":-}"
    assert server["command"] == "basicly-tracker"
    assert server["args"] == ["mcp", "--repository", str(root)]
    assert {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    } == before


def test_project_registration_has_no_machine_binding_and_kit_mode_needs_no_package(
    package: Path, tmp_path: Path
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    done = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            BOOT,
            str(package / "__init__.py"),
            "mcp-config",
            "--client",
            "codex",
            "--scope",
            "project",
            "--repository",
            str(root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    assert report["destination"] == ".codex/config.toml"
    assert str(root) not in report["configuration"]
    assert tomllib.loads(report["configuration"])["mcp_servers"]["basicly-tracker"]["args"] == [
        "mcp",
        "--repository",
        ".",
    ]
    direct = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(package / "kit/mcp_config.py"),
            "--client",
            "codex",
            "--repository",
            str(root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert direct.returncode == 0, direct.stderr
    server = tomllib.loads(json.loads(direct.stdout)["configuration"])["mcp_servers"][
        "basicly-tracker"
    ]
    assert server["command"] == sys.executable
    assert server["args"][0] == str(package / "kit/mcp_server.py")


@pytest.mark.parametrize("selector", [["--user"], ["--scope", "user"]])
def test_user_install_refuses_repository_options_before_writing_any_scope(
    package: Path, tmp_path: Path, selector: list[str]
) -> None:
    home, repository = tmp_path / "home", tmp_path / "repository"
    repository.mkdir()
    sentinel = repository / "keep.txt"
    sentinel.write_text("Existing project content.")
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            BOOT,
            str(package / "__init__.py"),
            "init",
            *selector,
            "--into",
            str(repository),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "--scope project" in result.stderr
    assert not home.exists()
    assert list(repository.iterdir()) == [sentinel]
    assert sentinel.read_text() == "Existing project content."
