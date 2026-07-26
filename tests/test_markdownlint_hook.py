"""Tests for the markdownlint launcher hook (basicly-jr0l.14).

The hook exists because ``npx`` is not a dependable way to reach node from a hook
shell: with nvm off PATH, WSL interop resolves it to a Windows install under
``/mnt/c`` that cannot express the worktree's UNC path. These tests pin the
resolution rules rather than the linting, which markdownlint-cli2 owns.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

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

# Assembled rather than written literally so the string is not mistaken for a real
# path by a reader or a path-scanning gate.
WINDOWS_NODE = "/mnt" + "/c/Program Files/nodejs/node"


# --- Rejecting the Windows interop resolution --------------------------------


def test_a_windows_interop_path_is_rejected_on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    """The defect itself: /mnt is the Windows drive mount, and CMD.EXE cannot see the worktree.

    Asserted on every OS leg, including the windows one, where ``Path`` renders
    this string as a rootless ``WindowsPath``. That is the point rather than an
    accident: the rule is a fact about the path text, so a host whose ``Path``
    flavour spells it differently must still reach the same verdict
    (basicly-jr0l.23).
    """
    monkeypatch.setattr(hook.sys, "platform", "linux")
    assert hook._is_windows_interop(Path(WINDOWS_NODE)) is True


def test_a_windows_path_is_accepted_on_a_real_windows_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On win32 a Windows node is simply where node lives, so the rule must not fire."""
    monkeypatch.setattr(hook.sys, "platform", "win32")
    assert hook._is_windows_interop(Path(WINDOWS_NODE)) is False


def test_the_interop_rule_survives_the_other_path_flavour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression gate, and it runs on every host (basicly-jr0l.23).

    The windows leg of CI went red here while Linux and macOS stayed green, because
    the rule was written against ``Path.parts`` — whose spelling of ``/mnt/c/...``
    depends on the interpreter, not on the path. Handing it ``PureWindowsPath``
    deliberately reproduces that flavour anywhere, so this class of defect no longer
    needs a Windows runner to be caught. Prefer this shape over a platform ``skipif``
    whenever the behaviour under test is a fact about a string.
    """
    monkeypatch.setattr(hook.sys, "platform", "linux")
    assert hook._is_windows_interop(PureWindowsPath(WINDOWS_NODE)) is True
    assert hook._is_windows_interop(PurePosixPath(WINDOWS_NODE)) is True
    assert hook._is_windows_interop(PureWindowsPath("/usr/bin/node")) is False
    assert hook._is_windows_interop(PureWindowsPath(r"C:\Program Files\nodejs\node")) is False


def test_an_ordinary_linux_path_is_not_interop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A distro or nvm node must never be mistaken for the Windows one."""
    monkeypatch.setattr(hook.sys, "platform", "linux")
    assert hook._is_windows_interop(Path("/usr/bin/node")) is False
    assert hook._is_windows_interop(Path.home() / ".nvm/versions/node/v20.0.0/bin/node") is False


def test_find_node_skips_a_windows_node_on_path_for_an_nvm_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PATH normally wins, but an interop hit is the defect and is stepped over."""
    nvm = tmp_path / ".nvm" / "versions" / "node" / "v20.1.0" / "bin"
    nvm.mkdir(parents=True)
    (nvm / "node").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(hook.sys, "platform", "linux")
    monkeypatch.setattr(hook.shutil, "which", lambda _cmd: WINDOWS_NODE)
    monkeypatch.setenv("NVM_DIR", str(tmp_path / ".nvm"))

    assert hook.find_node() == nvm / "node"


def test_find_node_prefers_an_explicit_path_node(monkeypatch: pytest.MonkeyPatch) -> None:
    """A node the caller chose is honoured; only interop is overridden."""
    monkeypatch.setattr(hook.sys, "platform", "linux")
    monkeypatch.setattr(hook.shutil, "which", lambda _cmd: "/opt/node/bin/node")
    assert hook.find_node() == Path("/opt/node/bin/node")


def test_find_node_returns_none_when_the_machine_has_no_usable_node(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A consumer with no node is told so, rather than left to a timeout."""
    monkeypatch.setattr(hook.sys, "platform", "linux")
    monkeypatch.setattr(hook.shutil, "which", lambda _cmd: None)
    monkeypatch.setenv("NVM_DIR", str(tmp_path / "absent"))
    monkeypatch.setattr(hook, "_SYSTEM_NODES", (tmp_path / "no-node",))

    assert hook.find_node() is None


def test_nvm_versions_sort_numerically_not_lexically(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """v10 beats v9. The reference doc this replaces left that to a human to remember."""
    root = tmp_path / ".nvm" / "versions" / "node"
    for version in ("v9.11.2", "v10.0.0", "v20.3.1"):
        binary = root / version / "bin"
        binary.mkdir(parents=True)
        (binary / "node").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("NVM_DIR", str(tmp_path / ".nvm"))

    assert [p.parent.parent.name for p in hook._nvm_nodes()] == ["v20.3.1", "v10.0.0", "v9.11.2"]


# --- Failing loudly rather than slowly ---------------------------------------


def test_a_missing_cli_fails_with_the_install_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No markdownlint-cli2 means npm install, said in one line."""
    monkeypatch.chdir(tmp_path)
    assert hook.main([]) == 1
    err = capsys.readouterr().err
    assert "npm install" in err


def test_no_usable_node_fails_with_one_actionable_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other loud failure: what to install, and why a Windows node was not used."""
    cli = tmp_path / hook._CLI_ENTRY
    cli.parent.mkdir(parents=True)
    cli.write_text("// entry\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(hook, "find_node", lambda: None)

    assert hook.main([]) == 1
    err = capsys.readouterr().err
    assert "nvm install" in err
    assert "/mnt" in err  # names why the Windows node was refused


def test_the_launcher_never_invokes_npx(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The regression pin: npx is the whole defect, so it must not appear in the argv.

    The expected argv is rendered from the same ``Path`` the launcher was handed
    rather than written out as POSIX text: ``str()`` on a rootless path yields
    backslashes under a Windows interpreter, so the literal form failed the windows
    leg while the launcher was behaving correctly (basicly-jr0l.23).
    """
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
    """A lint failure must still fail the commit — the launcher is not a verdict."""
    cli = tmp_path / hook._CLI_ENTRY
    cli.parent.mkdir(parents=True)
    cli.write_text("// entry\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(hook, "find_node", lambda: Path("/opt/node/bin/node"))

    class _Proc:
        returncode = 3

    monkeypatch.setattr(hook.subprocess, "run", lambda _cmd, **_kw: _Proc())
    assert hook.main([]) == 3


# --- The projected config actually uses it -----------------------------------


def test_the_pre_commit_config_launches_the_script_and_not_npx() -> None:
    """The wiring, so the fix cannot sit in the tree unreferenced."""
    config = (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "entry: uv run python .basicly/core/hooks/markdownlint.py" in config
    assert "npx --no-install markdownlint-cli2" not in config


def test_the_script_is_stdlib_only() -> None:
    """Hooks ship to consumers, so a third-party import would break a fresh install.

    Parsed rather than grepped: a prose line like "from a script" in the module
    docstring is not an import, and matching on a text prefix said it was.
    """
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
