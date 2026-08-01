"""Tests for the portable kit's install entry point (basicly-wbsz.3).

The installer writes into a file it does not own, so most of what matters here
is what it does **not** do: it must not duplicate on a re-run, must not touch a
hook the consumer wrote, and must not overwrite a settings file it cannot parse.
Each of those is checked with a positive control in the same test — an installer
that did nothing at all would satisfy every "leaves it alone" assertion on its
own.

The CLI is driven as a subprocess under ``-S -I`` and an environment built from
empty, so the kit's no-basicly constraint is carried by the same harness that
checks the behaviour rather than asserted separately.

Paths are compared as ``Path`` objects or through ``as_posix()`` on both sides,
never against a literal POSIX string, so the suite means the same thing on the
two platforms CI runs that this one is not.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).parent.parent
KIT_DIR = REPO_ROOT / ".basicly" / "core" / "kit"
INSTALLER = KIT_DIR / "install_hook.py"
HOOK = KIT_DIR / "claude_tier_hook.py"


def _load(path: Path) -> ModuleType:
    """Load a kit file by path, the way a consumer who copied it would."""
    name = f"kit_{path.stem}_under_test"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


kit = _load(INSTALLER)


def _settings(root: Path) -> dict:
    return json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))


def _groups(settings: dict, event: str = "PreToolUse") -> list:
    return settings["hooks"][event]


def _our_groups(settings: dict) -> list:
    return [g for g in _groups(settings) if kit._runs_our_hook(g)]


# --- the constraint -----------------------------------------------------------


def test_the_installer_imports_nothing_but_the_standard_library() -> None:
    """A third-party or basicly import would break the kit in a consumer repo."""
    tree = ast.parse(INSTALLER.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "a relative import would make the kit need a package"
            if node.module:
                imported.add(node.module.split(".")[0])
    assert imported, "the AST walk found no imports at all, so it proves nothing"
    assert "basicly" not in imported
    assert imported <= set(sys.stdlib_module_names), sorted(imported - set(sys.stdlib_module_names))


# --- what it writes -----------------------------------------------------------


def test_installing_writes_a_pretooluse_hook_matching_the_agent_tool(tmp_path: Path) -> None:
    """The acceptance criterion: it installs where the host actually loads it from."""
    installed, lines = kit.install(["claude"], tmp_path, user=False, dry_run=False)

    assert installed
    assert any("claude" in line for line in lines)
    group = _our_groups(_settings(tmp_path))
    assert len(group) == 1
    assert group[0]["matcher"] == "Agent"


def test_the_written_command_names_the_hook_by_an_absolute_forward_slashed_path(
    tmp_path: Path,
) -> None:
    """A relative path breaks the moment a spawn happens in a subdirectory."""
    kit.install(["claude"], tmp_path, user=False, dry_run=False)

    command = _our_groups(_settings(tmp_path))[0]["hooks"][0]["command"]
    assert HOOK.resolve().as_posix() in command
    assert Path(sys.executable).as_posix() in command
    assert "\\" not in command


def test_the_report_names_the_host_and_the_file_it_wrote(tmp_path: Path) -> None:
    """Reports which host it configured and what it wrote, verbatim from the AC."""
    _, lines = kit.install(["claude"], tmp_path, user=False, dry_run=False)

    joined = "\n".join(lines)
    assert "claude" in joined
    assert (tmp_path / ".claude" / "settings.json").as_posix() in joined.replace("\\", "/")


# --- converging rather than duplicating ---------------------------------------


def test_a_second_run_converges_without_duplicating_the_hook(tmp_path: Path) -> None:
    """Re-running an installer is the normal case, not the exceptional one."""
    kit.install(["claude"], tmp_path, user=False, dry_run=False)
    first = _settings(tmp_path)

    installed, lines = kit.install(["claude"], tmp_path, user=False, dry_run=False)

    assert installed
    assert "already installed" in "\n".join(lines)
    assert _settings(tmp_path) == first
    assert len(_our_groups(first)) == 1


def test_a_stale_entry_is_replaced_rather_than_raced(tmp_path: Path) -> None:
    """A moved interpreter must leave one hook behind, not two that disagree."""
    old_command = '"/old/python" "/old/claude_tier_hook.py"'
    stale = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Agent",
                    "hooks": [{"type": "command", "command": old_command}],
                }
            ]
        }
    }
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(stale), encoding="utf-8")

    kit.install(["claude"], tmp_path, user=False, dry_run=False)

    ours = _our_groups(_settings(tmp_path))
    assert len(ours) == 1
    assert "/old/python" not in ours[0]["hooks"][0]["command"]


def test_hooks_the_consumer_wrote_are_left_untouched(tmp_path: Path) -> None:
    """Matched by the script they run, never by position in the list."""
    theirs = {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": "python their_own_guard.py"}],
    }
    existing = {"hooks": {"PreToolUse": [theirs], "PostToolUse": [theirs]}, "model": "opus"}
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(existing), encoding="utf-8")

    kit.install(["claude"], tmp_path, user=False, dry_run=False)

    after = _settings(tmp_path)
    assert theirs in _groups(after)
    assert after["hooks"]["PostToolUse"] == [theirs]
    assert after["model"] == "opus", "unrelated settings keys must survive"
    assert len(_our_groups(after)) == 1, "positive control: ours was installed alongside theirs"


# --- scope --------------------------------------------------------------------


def test_the_user_scope_writes_the_configured_config_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dictated rather than discovered, so the path is one answer on every platform."""
    configured = tmp_path / "dictated-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(configured))

    assert kit.settings_path(tmp_path, user=True) == configured / "settings.json"
    assert kit.settings_path(tmp_path, user=False) == tmp_path / ".claude" / "settings.json"


def test_the_user_scope_falls_back_to_the_home_config_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default a consumer gets when they dictate nothing."""
    home = tmp_path / "fake-home"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))

    assert kit.settings_path(tmp_path, user=True) == home / ".claude" / "settings.json"


def test_installing_at_user_scope_writes_only_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wider scope must not also write into the repository, or uninstall lies."""
    configured = tmp_path / "dictated-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(configured))

    installed, lines = kit.install(["claude"], tmp_path, user=True, dry_run=False)

    assert installed
    assert "user" in "\n".join(lines)
    assert (configured / "settings.json").is_file()
    assert not (tmp_path / ".claude").exists()


# --- a host that cannot intercept ---------------------------------------------


def test_copilot_installs_nothing_and_says_why(tmp_path: Path) -> None:
    """The AC clause: it must not report success for a hook that will never fire."""
    installed, lines = kit.install(["copilot"], tmp_path, user=False, dry_run=False)

    assert not installed
    joined = "\n".join(lines)
    assert "nothing installed" in joined
    assert "hook" in joined, "the reason has to name what is missing, not just decline"
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / ".github").exists()


def test_every_known_host_is_reported_even_when_only_one_installs(tmp_path: Path) -> None:
    """A silent omission would read as "copilot was fine", which is the failure."""
    installed, lines = kit.install(list(kit.HOSTS), tmp_path, user=False, dry_run=False)

    assert installed, "positive control: claude still installs alongside the decline"
    joined = "\n".join(lines)
    for host in kit.HOSTS:
        assert host in joined


# --- refusing rather than clobbering ------------------------------------------


@pytest.mark.parametrize(
    "content", ["{not json", '"a string"', "[1, 2]"], ids=["malformed", "scalar", "array"]
)
def test_settings_that_cannot_be_parsed_are_refused_never_overwritten(
    content: str, tmp_path: Path
) -> None:
    """It is the consumer's file; a mangled one is worse than an uninstalled hook."""
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError):
        kit.install(["claude"], tmp_path, user=False, dry_run=False)

    assert path.read_text(encoding="utf-8") == content


def test_an_empty_settings_file_is_installed_into_rather_than_refused(tmp_path: Path) -> None:
    """Positive control for the refusal above: empty is not the same as unparseable."""
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text("   \n", encoding="utf-8")

    installed, _ = kit.install(["claude"], tmp_path, user=False, dry_run=False)

    assert installed
    assert len(_our_groups(_settings(tmp_path))) == 1


def test_a_dry_run_reports_the_write_without_making_it(tmp_path: Path) -> None:
    """The safe way to see what the wider scope would do before choosing it."""
    installed, lines = kit.install(["claude"], tmp_path, user=False, dry_run=True)

    assert installed
    assert "would write" in "\n".join(lines)
    assert not (tmp_path / ".claude").exists()


def test_a_missing_hook_script_is_reported_rather_than_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A half-copied kit must not leave a settings entry pointing at nothing."""
    monkeypatch.setattr(kit, "HOOK_FILENAME", "no_such_hook.py")

    installed, lines = kit.install(["claude"], tmp_path, user=False, dry_run=False)

    assert not installed
    assert "missing" in "\n".join(lines)
    assert not (tmp_path / ".claude").exists()


# --- the command line, with no basicly ----------------------------------------


def _pruned_env(tmp_path: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    """An environment with no basicly on PATH and nothing pointing at this repo."""
    empty = tmp_path / "empty-path-dir"
    empty.mkdir(exist_ok=True)
    home = tmp_path / "scratch-home"
    home.mkdir(exist_ok=True)
    env = {"PATH": str(empty), "HOME": str(home), "USERPROFILE": str(home)}
    for name in ("SystemRoot", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP"):
        value = os.environ.get(name)
        if value is not None:
            env[name] = value
    if extra:
        env.update(extra)
    return env


def _run(args: list[str], cwd: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-S", "-I", str(INSTALLER), *args],
        cwd=cwd,
        env=_pruned_env(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_command_line_installs_from_a_consumer_shaped_interpreter(tmp_path: Path) -> None:
    """The whole entry point, with basicly neither importable nor on PATH."""
    consumer = tmp_path / "consumer"
    consumer.mkdir()

    result = _run(["--host", "claude"], cwd=consumer, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "claude" in result.stdout
    assert len(_our_groups(_settings(consumer))) == 1


def test_the_command_line_exits_non_zero_when_nothing_was_installed(tmp_path: Path) -> None:
    """A script must be able to branch on it without parsing the report."""
    consumer = tmp_path / "consumer"
    consumer.mkdir()

    declined = _run(["--host", "copilot"], cwd=consumer, tmp_path=tmp_path)
    assert declined.returncode == 1, declined.stdout
    assert "nothing installed" in declined.stdout

    # Positive control: the same command line for the host that can intercept.
    assert _run(["--host", "claude"], cwd=consumer, tmp_path=tmp_path).returncode == 0


def test_the_command_line_refuses_unparseable_settings_with_a_reason(tmp_path: Path) -> None:
    """Refusal has to reach stderr and the exit status, not just an exception."""
    consumer = tmp_path / "consumer"
    path = consumer / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    result = _run(["--host", "claude"], cwd=consumer, tmp_path=tmp_path)

    assert result.returncode == 1
    assert "refusing to overwrite" in result.stderr
    assert path.read_text(encoding="utf-8") == "{not json"
