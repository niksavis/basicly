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

**Every project-scope test installs into a repository that contains the kit**
(the ``consumer`` fixture), because that is the only arrangement a consumer ever
has: ``basicly install`` copies the kit into the repo it manages. Running the
installer out of basicly's own checkout while writing settings into an unrelated
``tmp_path`` is what hid basicly-dukb — the hook was never inside the root, so no
test could observe how the repository's own committed file gets addressed.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).parent.parent
KIT_RELATIVE_DIR = Path(".basicly") / "core" / "kit"
KIT_DIR = REPO_ROOT / KIT_RELATIVE_DIR
INSTALLER = KIT_DIR / "install_hook.py"
HOOK = KIT_DIR / "claude_tier_hook.py"


def _load(path: Path, suffix: str = "") -> ModuleType:
    """Load a kit file by path, the way a consumer who copied it would."""
    name = f"kit_{path.stem}_under_test{suffix}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


kit = _load(INSTALLER)


@pytest.fixture
def consumer(tmp_path: Path) -> Path:
    """A repository shaped like a consumer's, with the kit copied inside it.

    Both kit files, at the path ``basicly install`` puts them, so the installer
    can name the hook relative to the repository it is writing into.
    """
    kit_dir = tmp_path / KIT_RELATIVE_DIR
    kit_dir.mkdir(parents=True)
    for source in (INSTALLER, HOOK):
        shutil.copy2(source, kit_dir / source.name)
    return tmp_path


def _installer_in(repo: Path) -> ModuleType:
    """The installer as it sits inside *repo*, not as it sits in this checkout."""
    return _load(repo / KIT_RELATIVE_DIR / INSTALLER.name, suffix=f"_{repo.name}")


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


def test_installing_writes_a_pretooluse_hook_matching_the_agent_tool(consumer: Path) -> None:
    """The acceptance criterion: it installs where the host actually loads it from."""
    installed, lines = _installer_in(consumer).install(
        ["claude"], consumer, user=False, dry_run=False
    )

    assert installed
    assert any("claude" in line for line in lines)
    group = _our_groups(_settings(consumer))
    assert len(group) == 1
    assert group[0]["matcher"] == "Agent"


def test_the_project_scope_command_carries_no_absolute_path_at_all(consumer: Path) -> None:
    """The bug (basicly-dukb): this file is committed, so an absolute path leaks a username.

    The earlier version of this test asserted the opposite, justified by "a
    relative path breaks the moment a spawn happens in a subdirectory". That
    rationale is true, and it was never an argument for an *absolute* path: the
    host substitutes ``${CLAUDE_PROJECT_DIR}`` itself, which is neither absolute
    nor dependent on the directory the spawn happened in.
    """
    _installer_in(consumer).install(["claude"], consumer, user=False, dry_run=False)

    command = _our_groups(_settings(consumer))[0]["hooks"][0]["command"]
    assert "${CLAUDE_PROJECT_DIR}" in command
    assert consumer.resolve().as_posix() not in command, "leaks the repository location"
    assert Path(sys.executable).as_posix() not in command, "leaks the interpreter location"
    assert "\\" not in command
    # Positive control: naming neither absolute path only means something if the
    # command still names the hook and something that can run it.
    assert (KIT_RELATIVE_DIR / HOOK.name).as_posix() in command
    assert command.startswith("uv run ")


def test_the_project_scope_command_does_not_depend_on_the_working_directory(
    consumer: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The property the absolute rendering was reaching for, kept without an absolute path.

    Rendered from a subdirectory, the command has to come out identical — the
    repository root is the anchor, not wherever the installer happened to run.
    """
    kit_local = _installer_in(consumer)
    hook = consumer / KIT_RELATIVE_DIR / HOOK.name
    subdirectory = consumer / "docs"
    subdirectory.mkdir()

    monkeypatch.chdir(consumer)
    from_root = kit_local.hook_command(hook, root=consumer)
    monkeypatch.chdir(subdirectory)
    from_subdirectory = kit_local.hook_command(hook, root=consumer)

    assert from_root == from_subdirectory
    assert (KIT_RELATIVE_DIR / HOOK.name).as_posix() in from_subdirectory


def test_the_user_scope_command_stays_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the AC: that file is machine-local, so absolute is correct there.

    It also needs nothing on ``PATH`` — the interpreter that ran the installer is
    named outright.
    """
    configured = tmp_path / "dictated-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(configured))

    kit.install(["claude"], tmp_path, user=True, dry_run=False)

    settings = json.loads((configured / "settings.json").read_text(encoding="utf-8"))
    group = next(g for g in _groups(settings) if kit._runs_our_hook(g))
    command = group["hooks"][0]["command"]
    assert HOOK.resolve().as_posix() in command
    assert Path(sys.executable).as_posix() in command
    assert "${CLAUDE_PROJECT_DIR}" not in command


def test_a_project_scope_install_refuses_a_hook_outside_the_repository(tmp_path: Path) -> None:
    """Fail closed: falling back to the absolute rendering would reinstate the bug."""
    outside = tmp_path / "not-the-repo"
    outside.mkdir()

    with pytest.raises(ValueError, match="outside"):
        kit.hook_command(HOOK, root=outside)

    # Positive control: the same call succeeds once the hook is under the root.
    assert kit.hook_command(HOOK, root=REPO_ROOT)


def test_an_interpreter_override_is_written_for_a_consumer_without_uv(consumer: Path) -> None:
    """The kit must stay usable with no basicly and no uv, which is why this exists."""
    kit_local = _installer_in(consumer)

    kit_local.install(["claude"], consumer, user=False, dry_run=False, interpreter="py -3")

    command = _our_groups(_settings(consumer))[0]["hooks"][0]["command"]
    assert command.startswith("py -3 ")
    assert "uv run" not in command
    assert "${CLAUDE_PROJECT_DIR}" in command


def test_the_report_names_the_host_and_the_file_it_wrote(consumer: Path) -> None:
    """Reports which host it configured and what it wrote, verbatim from the AC."""
    _, lines = _installer_in(consumer).install(["claude"], consumer, user=False, dry_run=False)

    joined = "\n".join(lines)
    assert "claude" in joined
    assert (consumer / ".claude" / "settings.json").as_posix() in joined.replace("\\", "/")


# --- converging rather than duplicating ---------------------------------------


def test_a_second_run_converges_without_duplicating_the_hook(consumer: Path) -> None:
    """Re-running an installer is the normal case, not the exceptional one."""
    kit_local = _installer_in(consumer)
    kit_local.install(["claude"], consumer, user=False, dry_run=False)
    first = _settings(consumer)

    installed, lines = kit_local.install(["claude"], consumer, user=False, dry_run=False)

    assert installed
    assert "already installed" in "\n".join(lines)
    assert _settings(consumer) == first
    assert len(_our_groups(first)) == 1


def test_a_stale_entry_is_replaced_rather_than_raced(consumer: Path) -> None:
    """An entry left by an older kit must leave one hook behind, not two that disagree."""
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
    path = consumer / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(stale), encoding="utf-8")

    _installer_in(consumer).install(["claude"], consumer, user=False, dry_run=False)

    ours = _our_groups(_settings(consumer))
    assert len(ours) == 1
    assert "/old/python" not in ours[0]["hooks"][0]["command"]


def test_hooks_the_consumer_wrote_are_left_untouched(consumer: Path) -> None:
    """Matched by the script they run, never by position in the list."""
    theirs = {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": "python their_own_guard.py"}],
    }
    existing = {"hooks": {"PreToolUse": [theirs], "PostToolUse": [theirs]}, "model": "opus"}
    path = consumer / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(existing), encoding="utf-8")

    _installer_in(consumer).install(["claude"], consumer, user=False, dry_run=False)

    after = _settings(consumer)
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


def test_every_known_host_is_reported_even_when_only_one_installs(consumer: Path) -> None:
    """A silent omission would read as "copilot was fine", which is the failure."""
    installed, lines = _installer_in(consumer).install(
        list(kit.HOSTS), consumer, user=False, dry_run=False
    )

    assert installed, "positive control: claude still installs alongside the decline"
    joined = "\n".join(lines)
    for host in kit.HOSTS:
        assert host in joined


# --- refusing rather than clobbering ------------------------------------------


@pytest.mark.parametrize(
    "content", ["{not json", '"a string"', "[1, 2]"], ids=["malformed", "scalar", "array"]
)
def test_settings_that_cannot_be_parsed_are_refused_never_overwritten(
    content: str, consumer: Path
) -> None:
    """It is the consumer's file; a mangled one is worse than an uninstalled hook."""
    path = consumer / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError):
        _installer_in(consumer).install(["claude"], consumer, user=False, dry_run=False)

    assert path.read_text(encoding="utf-8") == content


def test_an_empty_settings_file_is_installed_into_rather_than_refused(consumer: Path) -> None:
    """Positive control for the refusal above: empty is not the same as unparseable."""
    path = consumer / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text("   \n", encoding="utf-8")

    installed, _ = _installer_in(consumer).install(["claude"], consumer, user=False, dry_run=False)

    assert installed
    assert len(_our_groups(_settings(consumer))) == 1


def test_a_dry_run_reports_the_write_without_making_it(consumer: Path) -> None:
    """The safe way to see what the wider scope would do before choosing it."""
    installed, lines = _installer_in(consumer).install(
        ["claude"], consumer, user=False, dry_run=True
    )

    assert installed
    assert "would write" in "\n".join(lines)
    assert not (consumer / ".claude").exists()


def test_a_missing_hook_script_is_reported_rather_than_installed(consumer: Path) -> None:
    """A half-copied kit must not leave a settings entry pointing at nothing."""
    kit_local = _installer_in(consumer)
    (consumer / KIT_RELATIVE_DIR / HOOK.name).unlink()

    installed, lines = kit_local.install(["claude"], consumer, user=False, dry_run=False)

    assert not installed
    assert "missing" in "\n".join(lines)
    assert not (consumer / ".claude").exists()


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


def _run(args: list[str], repo: Path) -> subprocess.CompletedProcess[str]:
    """Drive the installer that sits inside *repo*, from *repo*, with no basicly."""
    installer = repo / KIT_RELATIVE_DIR / INSTALLER.name
    return subprocess.run(
        [sys.executable, "-S", "-I", str(installer), *args],
        cwd=repo,
        env=_pruned_env(repo),
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_command_line_installs_from_a_consumer_shaped_interpreter(consumer: Path) -> None:
    """The whole entry point, with basicly neither importable nor on PATH."""
    result = _run(["--host", "claude"], repo=consumer)

    assert result.returncode == 0, result.stderr
    assert "claude" in result.stdout
    assert len(_our_groups(_settings(consumer))) == 1


def test_the_command_line_writes_a_committable_command(consumer: Path) -> None:
    """End to end, through the real entry point: nothing machine-specific reaches the file.

    The unit-level test above can only see what ``hook_command`` returns. This one
    reads the file a consumer would actually commit, which is where basicly-dukb
    was found in the first place.
    """
    assert _run(["--host", "claude"], repo=consumer).returncode == 0

    written = (consumer / ".claude" / "settings.json").read_text(encoding="utf-8")
    assert "${CLAUDE_PROJECT_DIR}" in written
    assert consumer.resolve().as_posix() not in written
    assert Path.home().as_posix() not in written


def test_the_command_line_exits_non_zero_when_nothing_was_installed(consumer: Path) -> None:
    """A script must be able to branch on it without parsing the report."""
    declined = _run(["--host", "copilot"], repo=consumer)
    assert declined.returncode == 1, declined.stdout
    assert "nothing installed" in declined.stdout

    # Positive control: the same command line for the host that can intercept.
    assert _run(["--host", "claude"], repo=consumer).returncode == 0


def test_the_command_line_refuses_unparseable_settings_with_a_reason(consumer: Path) -> None:
    """Refusal has to reach stderr and the exit status, not just an exception."""
    path = consumer / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    result = _run(["--host", "claude"], repo=consumer)

    assert result.returncode == 1
    assert "refusing to overwrite" in result.stderr
    assert path.read_text(encoding="utf-8") == "{not json"
