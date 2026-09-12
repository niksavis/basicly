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
KIT_RELATIVE_DIR = Path(".basicly/core/kit/tier")
KIT_DIR = REPO_ROOT / KIT_RELATIVE_DIR
INSTALLER = KIT_DIR / "install_hook.py"
HOOK = KIT_DIR / "claude_tier_hook.py"


def _load(path: Path, suffix: str = "") -> ModuleType:
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

    kit_dir = tmp_path / KIT_RELATIVE_DIR
    kit_dir.mkdir(parents=True)
    for source in (INSTALLER, HOOK):
        shutil.copy2(source, kit_dir / source.name)
    return tmp_path


def _installer_in(repo: Path) -> ModuleType:
    return _load(repo / KIT_RELATIVE_DIR / INSTALLER.name, suffix=f"_{repo.name}")


def _settings(root: Path) -> dict:
    return json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))


def _groups(settings: dict, event: str = "PreToolUse") -> list:
    return settings["hooks"][event]


def _our_groups(settings: dict) -> list:
    return [g for g in _groups(settings) if kit._runs_our_hook(g)]


def test_the_installer_imports_nothing_but_the_standard_library() -> None:
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


def test_installing_writes_a_pretooluse_hook_matching_the_agent_tool(consumer: Path) -> None:
    installed, lines = _installer_in(consumer).install(
        ["claude"], consumer, user=False, dry_run=False
    )

    assert installed
    assert any("claude" in line for line in lines)
    group = _our_groups(_settings(consumer))
    assert len(group) == 1
    assert group[0]["matcher"] == "Agent"


def test_the_project_scope_command_carries_no_absolute_path_at_all(consumer: Path) -> None:

    _installer_in(consumer).install(["claude"], consumer, user=False, dry_run=False)

    command = _our_groups(_settings(consumer))[0]["hooks"][0]["command"]
    assert "${CLAUDE_PROJECT_DIR}" in command
    assert consumer.resolve().as_posix() not in command, "leaks the repository location"
    assert Path(sys.executable).as_posix() not in command, "leaks the interpreter location"
    assert "\\" not in command
    assert (KIT_RELATIVE_DIR / HOOK.name).as_posix() in command
    assert command.startswith("uv run ")


def test_the_project_scope_command_does_not_depend_on_the_working_directory(
    consumer: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

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
    outside = tmp_path / "not-the-repo"
    outside.mkdir()

    with pytest.raises(ValueError, match="outside"):
        kit.hook_command(HOOK, root=outside)

    assert kit.hook_command(HOOK, root=REPO_ROOT)


def test_an_interpreter_override_is_written_for_a_consumer_without_uv(consumer: Path) -> None:
    kit_local = _installer_in(consumer)

    kit_local.install(["claude"], consumer, user=False, dry_run=False, interpreter="py -3")

    command = _our_groups(_settings(consumer))[0]["hooks"][0]["command"]
    assert command.startswith("py -3 ")
    assert "uv run" not in command
    assert "${CLAUDE_PROJECT_DIR}" in command


def test_the_report_names_the_host_and_the_file_it_wrote(consumer: Path) -> None:
    _, lines = _installer_in(consumer).install(["claude"], consumer, user=False, dry_run=False)

    joined = "\n".join(lines)
    assert "claude" in joined
    assert (consumer / ".claude" / "settings.json").as_posix() in joined.replace("\\", "/")


def _restated(lines: list[str]) -> bool:

    joined = "\n".join(lines).lower()
    return "quit" in joined and "relaunch" in joined


def test_a_run_that_writes_says_the_host_must_be_quit_and_relaunched(consumer: Path) -> None:
    _, lines = _installer_in(consumer).install(["claude"], consumer, user=False, dry_run=False)

    assert _restated(lines)
    assert lines[-1] == kit.RESTART_NOTICE, "it is the next step, so it comes last"


def test_a_dry_run_does_not_ask_for_a_restart_it_changed_nothing(consumer: Path) -> None:
    _, lines = _installer_in(consumer).install(["claude"], consumer, user=False, dry_run=True)

    assert not _restated(lines)
    assert "would write" in "\n".join(lines), "positive control: it still reported the write"


def test_an_already_installed_run_does_not_ask_for_a_restart(consumer: Path) -> None:
    kit_local = _installer_in(consumer)
    _, first = kit_local.install(["claude"], consumer, user=False, dry_run=False)

    _, second = kit_local.install(["claude"], consumer, user=False, dry_run=False)

    assert _restated(first), "positive control: the run that wrote did say it"
    assert not _restated(second)


def test_a_host_that_installs_nothing_does_not_ask_for_a_restart(tmp_path: Path) -> None:
    _, lines = kit.install(["copilot"], tmp_path, user=False, dry_run=False)

    assert not _restated(lines)


def test_the_restart_notice_is_reported_once_for_the_whole_run(consumer: Path) -> None:
    _, lines = _installer_in(consumer).install(list(kit.HOSTS), consumer, user=False, dry_run=False)

    assert _restated(lines)
    assert [line for line in lines if line == kit.RESTART_NOTICE] == [kit.RESTART_NOTICE]


def test_a_second_run_converges_without_duplicating_the_hook(consumer: Path) -> None:
    kit_local = _installer_in(consumer)
    kit_local.install(["claude"], consumer, user=False, dry_run=False)
    first = _settings(consumer)

    installed, lines = kit_local.install(["claude"], consumer, user=False, dry_run=False)

    assert installed
    assert "already installed" in "\n".join(lines)
    assert _settings(consumer) == first
    assert len(_our_groups(first)) == 1


def test_a_stale_entry_is_replaced_rather_than_raced(consumer: Path) -> None:
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


def test_the_user_scope_writes_the_configured_config_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = tmp_path / "dictated-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(configured))

    assert kit.settings_path(tmp_path, user=True) == configured / "settings.json"
    assert kit.settings_path(tmp_path, user=False) == tmp_path / ".claude" / "settings.json"


def test_the_user_scope_falls_back_to_the_home_config_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "fake-home"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))

    assert kit.settings_path(tmp_path, user=True) == home / ".claude" / "settings.json"


def test_installing_at_user_scope_writes_only_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = tmp_path / "dictated-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(configured))

    installed, lines = kit.install(["claude"], tmp_path, user=True, dry_run=False)

    assert installed
    assert "user" in "\n".join(lines)
    assert (configured / "settings.json").is_file()
    assert not (tmp_path / ".claude").exists()


def test_copilot_installs_nothing_and_says_why(tmp_path: Path) -> None:
    installed, lines = kit.install(["copilot"], tmp_path, user=False, dry_run=False)

    assert not installed
    joined = "\n".join(lines)
    assert "nothing installed" in joined
    assert "hook" in joined, "the reason has to name what is missing, not just decline"
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / ".github").exists()


def test_every_known_host_is_reported_even_when_only_one_installs(consumer: Path) -> None:
    installed, lines = _installer_in(consumer).install(
        list(kit.HOSTS), consumer, user=False, dry_run=False
    )

    assert installed, "positive control: claude still installs alongside the decline"
    joined = "\n".join(lines)
    for host in kit.HOSTS:
        assert host in joined


@pytest.mark.parametrize(
    "content", ["{not json", '"a string"', "[1, 2]"], ids=["malformed", "scalar", "array"]
)
def test_settings_that_cannot_be_parsed_are_refused_never_overwritten(
    content: str, consumer: Path
) -> None:
    path = consumer / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError):
        _installer_in(consumer).install(["claude"], consumer, user=False, dry_run=False)

    assert path.read_text(encoding="utf-8") == content


def test_an_empty_settings_file_is_installed_into_rather_than_refused(consumer: Path) -> None:
    path = consumer / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text("   \n", encoding="utf-8")

    installed, _ = _installer_in(consumer).install(["claude"], consumer, user=False, dry_run=False)

    assert installed
    assert len(_our_groups(_settings(consumer))) == 1


def test_a_dry_run_reports_the_write_without_making_it(consumer: Path) -> None:
    installed, lines = _installer_in(consumer).install(
        ["claude"], consumer, user=False, dry_run=True
    )

    assert installed
    assert "would write" in "\n".join(lines)
    assert not (consumer / ".claude").exists()


def test_a_missing_hook_script_is_reported_rather_than_installed(consumer: Path) -> None:
    kit_local = _installer_in(consumer)
    (consumer / KIT_RELATIVE_DIR / HOOK.name).unlink()

    installed, lines = kit_local.install(["claude"], consumer, user=False, dry_run=False)

    assert not installed
    assert "missing" in "\n".join(lines)
    assert not (consumer / ".claude").exists()


def _pruned_env(tmp_path: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
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
    result = _run(["--host", "claude"], repo=consumer)

    assert result.returncode == 0, result.stderr
    assert "claude" in result.stdout
    assert len(_our_groups(_settings(consumer))) == 1


def test_the_command_line_writes_a_committable_command(consumer: Path) -> None:

    assert _run(["--host", "claude"], repo=consumer).returncode == 0

    written = (consumer / ".claude" / "settings.json").read_text(encoding="utf-8")
    assert "${CLAUDE_PROJECT_DIR}" in written
    assert consumer.resolve().as_posix() not in written
    assert Path.home().as_posix() not in written


def test_the_command_line_exits_non_zero_when_nothing_was_installed(consumer: Path) -> None:
    declined = _run(["--host", "copilot"], repo=consumer)
    assert declined.returncode == 1, declined.stdout
    assert "nothing installed" in declined.stdout

    assert _run(["--host", "claude"], repo=consumer).returncode == 0


def test_the_command_line_refuses_unparseable_settings_with_a_reason(consumer: Path) -> None:
    path = consumer / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    result = _run(["--host", "claude"], repo=consumer)

    assert result.returncode == 1
    assert "refusing to overwrite" in result.stderr
    assert path.read_text(encoding="utf-8") == "{not json"


def test_the_command_line_prints_the_restart_requirement_only_when_it_wrote(
    consumer: Path,
) -> None:
    wrote = _run(["--host", "claude"], repo=consumer)
    assert wrote.returncode == 0, wrote.stderr
    assert _restated([wrote.stdout])

    converged = _run(["--host", "claude"], repo=consumer)
    assert converged.returncode == 0, converged.stderr
    assert "already installed" in converged.stdout
    assert not _restated([converged.stdout])
