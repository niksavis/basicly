from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import SimpleNamespace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".basicly" / "core" / "hooks" / "markdownlint.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("markdownlint_hook", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = _load_hook()

WINDOWS_NODE = "/mnt" + "/c/Program Files/nodejs/node"


def test_a_windows_interop_path_is_rejected_on_linux(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setattr(hook.sys, "platform", "linux")
    assert hook._is_windows_interop(Path(WINDOWS_NODE)) is True


def test_a_windows_path_is_accepted_on_a_real_windows_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hook.sys, "platform", "win32")
    assert hook._is_windows_interop(Path(WINDOWS_NODE)) is False


def test_the_interop_rule_survives_the_other_path_flavour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    monkeypatch.setattr(hook.sys, "platform", "linux")
    assert hook._is_windows_interop(PureWindowsPath(WINDOWS_NODE)) is True
    assert hook._is_windows_interop(PurePosixPath(WINDOWS_NODE)) is True
    assert hook._is_windows_interop(PureWindowsPath("/usr/bin/node")) is False
    assert hook._is_windows_interop(PureWindowsPath(r"C:\Program Files\nodejs\node")) is False


def test_an_ordinary_linux_path_is_not_interop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hook.sys, "platform", "linux")
    assert hook._is_windows_interop(Path("/usr/bin/node")) is False
    assert hook._is_windows_interop(Path.home() / ".nvm/versions/node/v20.0.0/bin/node") is False


def test_find_node_skips_a_windows_node_on_path_for_an_nvm_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    nvm = tmp_path / ".nvm" / "versions" / "node" / "v20.1.0" / "bin"
    nvm.mkdir(parents=True)
    (nvm / "node").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(hook.sys, "platform", "linux")
    monkeypatch.setattr(hook.shutil, "which", lambda _cmd: WINDOWS_NODE)
    monkeypatch.setenv("NVM_DIR", str(tmp_path / ".nvm"))

    assert hook.find_node() == nvm / "node"


def test_find_node_prefers_an_explicit_path_node(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hook.sys, "platform", "linux")
    monkeypatch.setattr(hook.shutil, "which", lambda _cmd: "/opt/node/bin/node")
    assert hook.find_node() == Path("/opt/node/bin/node")


def test_find_node_returns_none_when_the_machine_has_no_usable_node(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(hook.sys, "platform", "linux")
    monkeypatch.setattr(hook.shutil, "which", lambda _cmd: None)
    monkeypatch.setenv("NVM_DIR", str(tmp_path / "absent"))
    monkeypatch.setattr(hook, "_SYSTEM_NODES", (tmp_path / "no-node",))

    assert hook.find_node() is None


def test_nvm_versions_sort_numerically_not_lexically(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / ".nvm" / "versions" / "node"
    for version in ("v9.11.2", "v10.0.0", "v20.3.1"):
        binary = root / version / "bin"
        binary.mkdir(parents=True)
        (binary / "node").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("NVM_DIR", str(tmp_path / ".nvm"))

    assert [p.parent.parent.name for p in hook._nvm_nodes()] == ["v20.3.1", "v10.0.0", "v9.11.2"]


def test_a_missing_cli_says_npm_install_and_does_not_block(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    monkeypatch.chdir(tmp_path)
    assert hook.main([]) == 0
    err = capsys.readouterr().err
    assert "npm install" in err
    assert "skipped" in err


def test_no_usable_node_says_what_to_install_and_does_not_block(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = tmp_path / hook._CLI_ENTRY
    cli.parent.mkdir(parents=True)
    cli.write_text("// entry\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(hook, "find_node", lambda: None)

    assert hook.main([]) == 0
    err = capsys.readouterr().err
    assert "nvm install" in err
    assert "/mnt" in err


def test_a_violation_the_linter_reports_still_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cli = tmp_path / hook._CLI_ENTRY
    cli.parent.mkdir(parents=True)
    cli.write_text("// entry\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(hook, "find_node", lambda: Path("node"))
    monkeypatch.setattr(hook.subprocess, "run", lambda *_a, **_k: SimpleNamespace(returncode=1))

    assert hook.main([]) == 1


def test_the_launcher_never_invokes_npx(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:

    cli = tmp_path / hook._CLI_ENTRY
    cli.parent.mkdir(parents=True)
    cli.write_text("// entry\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    node = Path("/opt/node/bin/node")
    monkeypatch.setattr(hook, "find_node", lambda: node)
    seen: list[list[str]] = []

    class _Proc:
        returncode = 0

    monkeypatch.setattr(hook.subprocess, "run", lambda cmd, **_kw: seen.append(cmd) or _Proc())

    assert hook.main(["--fix"]) == 0
    assert seen == [[str(node), str(hook._CLI_ENTRY), "--fix"]]
    assert not any("npx" in part for part in seen[0])


def test_the_cli_exit_code_is_passed_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cli = tmp_path / hook._CLI_ENTRY
    cli.parent.mkdir(parents=True)
    cli.write_text("// entry\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(hook, "find_node", lambda: Path("/opt/node/bin/node"))

    class _Proc:
        returncode = 3

    monkeypatch.setattr(hook.subprocess, "run", lambda _cmd, **_kw: _Proc())
    assert hook.main([]) == 3


def test_the_pre_commit_config_launches_the_script_and_not_npx() -> None:
    config = (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "entry: uv run --no-project python .basicly/core/hooks/markdownlint.py" in config
    assert "npx --no-install markdownlint-cli2" not in config


def test_the_script_is_stdlib_only() -> None:

    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    assert imported, "no imports parsed — the check would pass vacuously"
    assert imported <= set(sys.stdlib_module_names), (
        f"non-stdlib: {imported - set(sys.stdlib_module_names)}"
    )
