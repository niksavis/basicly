from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from basicly import claude_settings, lefthook_binary, lefthook_config
from basicly import hooks as hooks_module
from basicly.hooks import (
    COPILOT_EVENTS,
    HookSpec,
    agent_hook_surface_present,
    check_copilot_hooks,
    check_hooks,
    claude_hook_specs,
    copilot_hook_specs,
    git_hook_specs,
    hook_stages,
    install_hooks,
    load_hook_specs,
    missing_hook_installations,
    remove_copilot_hooks,
    render_copilot_hook,
    selected_hook_specs,
    sync_copilot_hooks,
    sync_hooks,
    uninstall_hooks,
)
from basicly.schema import ValidationError

CORE_HOOKS_DIR = Path(".basicly/core/hooks")
REPO_ROOT = Path(__file__).parent.parent


_IGNORE_BYTECODE = shutil.ignore_patterns("__pycache__")


def _copy_hooks(src: Path, dst: Path) -> None:

    shutil.copytree(src, dst, ignore=_IGNORE_BYTECODE)


def _materialize_hooks(tmp_path: Path, catalog: Path | None = None) -> None:
    _copy_hooks(catalog or REPO_ROOT / CORE_HOOKS_DIR, tmp_path / CORE_HOOKS_DIR)


def _write_bytecode_cache(hooks_dir: Path) -> list[str]:

    cache = hooks_dir / "__pycache__"
    cache.mkdir(parents=True, exist_ok=True)
    names = ["pre-commit.cpython-314.pyc", "pre-commit.cpython-314.pyc.140234567890123"]
    for name in names:
        (cache / name).write_bytes(b"bytecode, not catalog content")
    return names


def _local_hook_ids(config: dict) -> set[str]:
    return {
        spec["id"]
        for name, group in config.items()
        if isinstance(group, dict)
        for command in group.get("commands", {}).values()
        for spec in json.loads(command["env"]["BASICLY_HOOK_SPECS"])
        if spec["manager"] == "git"
    }


def test_manifest_lists_every_catalog_hook() -> None:
    specs = load_hook_specs()
    ids = {spec.id for spec in specs}
    assert ids == {
        "markdownlint",
        "identity-guard",
        "pre-commit-script",
        "catalog-lint",
        "secret-scan",
        "tracker-path-scan",
        "internal-info-scan",
        "kit-boundary",
        "no-comments",
        "headroom-guard",
        "commit-msg-script",
        "tracker-commit-msg-script",
        "tracker-claim-script",
        "pre-push-script",
        "protect-generated",
        "protect-generated-commit",
        "unsplit-loop-guard",
        "pipe-status-guard",
        "bare-var-guard",
        "rg-replace-guard",
        "tool-usage",
        "tool-usage-copilot",
        "session-start",
        "session-start-copilot",
        "tracker-fold",
        "tier-injection",
        "protect-generated-codex",
        "session-start-codex",
    }


def test_manifest_ships_identity_guard_at_pre_commit() -> None:
    specs = load_hook_specs()
    guard = next(spec for spec in specs if spec.id == "identity-guard")
    assert guard.script == "identity-guard.py"
    assert guard.stage == "pre-commit"
    assert guard.always_run is True


def test_manifest_ships_protect_generated_for_claude() -> None:
    specs = load_hook_specs()
    guard = next(spec for spec in specs if spec.id == "protect-generated")
    assert guard.script == "protect-generated.py"
    assert guard.manager == "claude"
    assert git_hook_specs(specs) == [s for s in specs if s.manager == "git"]
    assert guard in claude_hook_specs(specs)


def test_manifest_ships_protect_generated_commit_for_git() -> None:
    specs = load_hook_specs()
    backstop = next(spec for spec in specs if spec.id == "protect-generated-commit")
    assert backstop.script == "protect-generated-commit.py"
    assert backstop.stage == "pre-commit"
    assert backstop.manager == "git"
    assert backstop in git_hook_specs(specs)


def test_agent_hook_surface_present_probes_the_host_binary() -> None:
    installed = {"claude": "/usr/local/bin/claude"}

    def which(cmd: str) -> str | None:
        return installed.get(cmd)

    assert agent_hook_surface_present("claude", which=which)
    assert not agent_hook_surface_present("copilot", which=which)
    assert not agent_hook_surface_present("git", which=which)


def test_copilot_hooks_sync_check_and_remove_roundtrip(tmp_path: Path) -> None:
    result = sync_copilot_hooks(tmp_path, CORE_HOOKS_DIR)
    hook_file = tmp_path / ".github/hooks/basicly-tool-usage-copilot.json"
    assert hook_file in result.written

    config = json.loads(hook_file.read_text(encoding="utf-8"))
    assert config["version"] == 1
    entry = config["hooks"]["postToolUse"][0]
    assert entry["type"] == "command"
    assert (
        entry["bash"]
        == "uv run --no-project --no-python-downloads --python '>=3.14' python -X utf8 "
        ".basicly/core/hooks/lefthook-runtime.py copilot posttooluse"
    )
    assert "lefthook-runtime.py" in entry["powershell"]

    assert check_copilot_hooks(tmp_path, CORE_HOOKS_DIR) == []
    again = sync_copilot_hooks(tmp_path, CORE_HOOKS_DIR)
    assert again.written == []

    stray = tmp_path / ".github/hooks/basicly-retired.json"
    stray.write_text("{}\n", encoding="utf-8")
    assert any(
        "stale managed" in reason for _, reason in check_copilot_hooks(tmp_path, CORE_HOOKS_DIR)
    )
    sync_copilot_hooks(tmp_path, CORE_HOOKS_DIR)
    assert not stray.exists()

    foreign = tmp_path / ".github/hooks/my-own.json"
    foreign.write_text("{}\n", encoding="utf-8")
    assert remove_copilot_hooks(tmp_path) == len(copilot_hook_specs(load_hook_specs()))
    assert foreign.exists() and not hook_file.exists()


def test_manifest_ships_tool_usage_for_both_agent_managers() -> None:
    specs = load_hook_specs()
    claude = next(spec for spec in specs if spec.id == "tool-usage")
    assert (claude.manager, claude.stage, claude.matcher) == ("claude", "posttooluse", "Bash|Skill")
    copilot = next(spec for spec in specs if spec.id == "tool-usage-copilot")
    assert (copilot.manager, copilot.stage) == ("copilot", "posttooluse")
    assert copilot.script == claude.script == "tool-usage.py"


def test_manifest_ships_session_start_for_both_agent_managers() -> None:

    specs = load_hook_specs()
    claude = next(spec for spec in specs if spec.id == "session-start")
    assert (claude.manager, claude.stage, claude.matcher) == ("claude", "sessionstart", "*")
    copilot = next(spec for spec in specs if spec.id == "session-start-copilot")
    assert (copilot.manager, copilot.stage, copilot.matcher) == ("copilot", "sessionstart", "")
    assert copilot.script == claude.script == "session-start.py"

    assert claude_settings.AGENT_HOOK_EVENTS["sessionstart"] == "SessionStart"
    assert COPILOT_EVENTS["sessionstart"] == "sessionStart"
    rendered = json.loads(render_copilot_hook(copilot, ".basicly/core/hooks"))
    assert list(rendered["hooks"]) == ["sessionStart"]
    assert "matcher" not in rendered["hooks"]["sessionStart"][0]


def test_load_rejects_unknown_manager(tmp_path: Path) -> None:
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "hooks.yaml").write_text(
        "hooks:\n  - id: x\n    script: x.py\n    stage: pre-commit\n    manager: lefthook\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown manager"):
        load_hook_specs(hooks_dir)


def test_sync_hooks_scaffolds_and_check_round_trips(tmp_path: Path) -> None:
    _materialize_hooks(tmp_path)
    result = sync_hooks(tmp_path, CORE_HOOKS_DIR)
    assert result.written

    config = tmp_path / ".basicly/lefthook.yml"
    assert config.is_file()
    for script in ("pre-commit.py", "commit-msg.py", "tracker-commit-msg.py", "pre-push.py"):
        assert (tmp_path / CORE_HOOKS_DIR / script).is_file()

    loaded = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert "pre-push-script" in _local_hook_ids(loaded)
    assert "protect-generated" not in _local_hook_ids(loaded)

    assert check_hooks(tmp_path, CORE_HOOKS_DIR) == []

    again = sync_hooks(tmp_path, CORE_HOOKS_DIR)
    assert again.written == []


def test_sync_hooks_requires_materialized_core(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="basicly install"):
        sync_hooks(tmp_path, CORE_HOOKS_DIR)


def test_selected_hook_specs_filters_tagged_specs() -> None:
    universal = HookSpec(id="a", script="a.py", stage="pre-commit")
    tagged = HookSpec(id="b", script="b.py", stage="pre-commit", technologies=("node",))
    specs = [universal, tagged]
    assert selected_hook_specs(specs, None) == specs
    assert selected_hook_specs(specs, frozenset({"node"})) == specs
    assert selected_hook_specs(specs, frozenset({"python"})) == [universal]


def test_sync_hooks_prunes_hook_excluded_by_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _materialize_hooks(tmp_path)
    tagged = HookSpec(
        id="uv-lock-check", script="uv-lock.py", stage="pre-commit", technologies=("python",)
    )
    real_specs = load_hook_specs()
    monkeypatch.setattr(hooks_module, "load_hook_specs", lambda *_a, **_k: [*real_specs, tagged])

    sync_hooks(tmp_path, CORE_HOOKS_DIR)
    config = tmp_path / ".basicly/lefthook.yml"
    assert "uv-lock-check" in _local_hook_ids(yaml.safe_load(config.read_text(encoding="utf-8")))

    selection = frozenset({"zsh"})
    mismatches = check_hooks(tmp_path, CORE_HOOKS_DIR, selection)
    assert any("out of sync" in reason for _, reason in mismatches)

    result = sync_hooks(tmp_path, CORE_HOOKS_DIR, selection)
    assert result.written == [config]
    assert "uv-lock-check" not in _local_hook_ids(
        yaml.safe_load(config.read_text(encoding="utf-8"))
    )
    assert check_hooks(tmp_path, CORE_HOOKS_DIR, selection) == []


def test_check_detects_wiring_drift(tmp_path: Path) -> None:
    _materialize_hooks(tmp_path)
    sync_hooks(tmp_path, CORE_HOOKS_DIR)
    config = tmp_path / ".basicly/lefthook.yml"
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    data.pop("pre-push")
    config.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert (config, "managed Lefthook configuration out of sync") in check_hooks(
        tmp_path, CORE_HOOKS_DIR
    )


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)  # nosec B603 B607


def _init_repo(root: Path) -> None:
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init", "--allow-empty")


def test_check_reports_consumer_hook_script_drift(tmp_path: Path) -> None:
    _materialize_hooks(tmp_path)
    sync_hooks(tmp_path, CORE_HOOKS_DIR)
    assert check_hooks(tmp_path, CORE_HOOKS_DIR) == []

    edited = tmp_path / CORE_HOOKS_DIR / "pre-commit.py"
    edited.write_text("# hand-edited in the consumer\n", encoding="utf-8")
    (tmp_path / CORE_HOOKS_DIR / "commit-msg.py").unlink()

    reasons = {path.name: reason for path, reason in check_hooks(tmp_path, CORE_HOOKS_DIR)}
    assert reasons["pre-commit.py"] == "differs from catalog"
    assert reasons["commit-msg.py"] == "missing"


def test_bytecode_cache_is_neither_materialized_nor_reported_as_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    catalog = tmp_path / "catalog"
    _copy_hooks(REPO_ROOT / CORE_HOOKS_DIR, catalog)
    cached = _write_bytecode_cache(catalog)
    monkeypatch.setattr(hooks_module, "_catalog_hooks_dir", lambda: catalog)

    repo = tmp_path / "repo"
    repo.mkdir()
    _materialize_hooks(repo, catalog)
    sync_hooks(repo, CORE_HOOKS_DIR)
    materialized = repo / CORE_HOOKS_DIR

    assert (materialized / "pre-commit.py").is_file()
    assert [path.name for path in materialized.rglob("*") if path.name in cached] == []
    assert not (materialized / "__pycache__").exists()
    assert check_hooks(repo, CORE_HOOKS_DIR) == []

    _write_bytecode_cache(materialized)
    assert check_hooks(repo, CORE_HOOKS_DIR) == []


def test_check_ignores_a_hook_edit_in_a_sibling_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    repo = tmp_path / "repo"
    repo.mkdir()
    _materialize_hooks(repo)
    sync_hooks(repo, CORE_HOOKS_DIR)
    _init_repo(repo)
    monkeypatch.setattr(hooks_module, "_catalog_hooks_dir", lambda: repo / CORE_HOOKS_DIR)
    assert check_hooks(repo, CORE_HOOKS_DIR) == []

    linked = tmp_path / "repo.worktrees" / "wt"
    _git(repo, "worktree", "add", "-b", "harness/wt", str(linked))
    assert install_hooks(linked, hook_stages(load_hook_specs()))[0]
    script = linked / CORE_HOOKS_DIR / "pre-commit.py"
    script.write_text(script.read_text(encoding="utf-8") + "\n# the change under review\n", "utf-8")

    assert check_hooks(linked, CORE_HOOKS_DIR) == []


def test_check_reports_drift_when_the_catalog_sits_inside_the_consumer_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    _materialize_hooks(tmp_path)
    sync_hooks(tmp_path, CORE_HOOKS_DIR)
    vendored = tmp_path / ".venv/lib/site-packages/basicly/catalog/hooks"
    _copy_hooks(tmp_path / CORE_HOOKS_DIR, vendored)
    _init_repo(tmp_path)
    monkeypatch.setattr(hooks_module, "_catalog_hooks_dir", lambda: vendored)
    assert check_hooks(tmp_path, CORE_HOOKS_DIR) == []

    edited = tmp_path / CORE_HOOKS_DIR / "pre-commit.py"
    edited.write_text("# drifted from the wheel's catalog\n", encoding="utf-8")
    assert (edited, "differs from catalog") in check_hooks(tmp_path, CORE_HOOKS_DIR)


def test_dogfood_config_passes_check() -> None:

    assert check_hooks(REPO_ROOT, CORE_HOOKS_DIR) == []


def test_semantically_synced_config_is_left_untouched(tmp_path: Path) -> None:
    _materialize_hooks(tmp_path)
    sync_hooks(tmp_path, CORE_HOOKS_DIR)
    config = tmp_path / "lefthook.yml"
    commented = "# consumer comment\n" + config.read_text(encoding="utf-8")
    config.write_text(commented, encoding="utf-8")
    assert sync_hooks(tmp_path, CORE_HOOKS_DIR).written == []
    assert config.read_text(encoding="utf-8") == commented
    assert check_hooks(tmp_path, CORE_HOOKS_DIR) == []


def test_out_of_sync_managed_hook_triggers_rewrite(tmp_path: Path) -> None:
    _materialize_hooks(tmp_path)
    sync_hooks(tmp_path, CORE_HOOKS_DIR)
    config = tmp_path / ".basicly/lefthook.yml"
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    data["pre-push"]["commands"]["basicly-dispatch"]["run"] = "echo tampered"
    config.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert check_hooks(tmp_path, CORE_HOOKS_DIR)
    assert config in sync_hooks(tmp_path, CORE_HOOKS_DIR).written
    assert check_hooks(tmp_path, CORE_HOOKS_DIR) == []


def test_hook_stages_returns_distinct_stages_in_order() -> None:
    specs = [
        HookSpec(id="a", script="a.py", stage="pre-commit"),
        HookSpec(id="b", script="b.py", stage="commit-msg"),
        HookSpec(id="c", script="c.py", stage="commit-msg"),
        HookSpec(id="d", script="d.py", stage="pre-push"),
        HookSpec(id="e", script="e.py", stage="pretooluse", manager="claude"),
    ]
    assert hook_stages(specs) == ["pre-commit", "commit-msg", "pre-push"]


def test_missing_hook_installations_detects_uninstalled_and_unmanaged(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)  # nosec B603 B607
    hooks_dir = tmp_path / ".git" / "hooks"
    (hooks_dir / "pre-commit").write_text(
        "#!/usr/bin/env bash\n# basicly Lefthook launcher\n", encoding="utf-8"
    )
    (hooks_dir / "pre-push").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")

    missing = missing_hook_installations(tmp_path, ["pre-commit", "commit-msg", "pre-push"])
    assert missing == ["commit-msg", "pre-push"]


def test_missing_hook_installations_degrades_when_git_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    def _no_git(*_args: object, **_kwargs: object) -> object:
        raise FileNotFoundError(2, "The system cannot find the file specified")

    monkeypatch.setattr(hooks_module.subprocess, "run", _no_git)
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "pre-commit").write_text("# basicly Lefthook launcher\n", encoding="utf-8")

    assert missing_hook_installations(tmp_path, ["pre-commit", "pre-push"]) == ["pre-push"]


def test_install_hooks_returns_guidance_when_uv_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    ok, message = install_hooks(tmp_path, ["pre-commit", "commit-msg", "pre-push"])
    assert not ok
    assert "uv is not on PATH" in message
    assert "basicly hooks-build" in message


def test_reinstall_does_not_rewrite_unchanged_launchers_or_foreign_hook_backups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)  # nosec B603 B607
    directory = tmp_path / ".git" / "hooks"
    hook = directory / "post-merge"
    body = b"#!/bin/sh\nprintf 'foreign\\n'\n"
    hook.write_bytes(body)
    hook.chmod(0o750)
    assert install_hooks(tmp_path, ["post-merge"])[0]
    before = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in directory.iterdir()
        if path.is_file()
    }

    def unexpected(*_args, **_kwargs):
        pytest.fail("an unchanged Git hook was rewritten")

    monkeypatch.setattr(hooks_module, "write_if_changed", unexpected)
    assert hooks_module.hook_installation_writes(tmp_path, ["post-merge"], CORE_HOOKS_DIR) == []
    assert install_hooks(tmp_path, ["post-merge"])[0]
    assert {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in directory.iterdir()
        if path.is_file()
    } == before
    assert (directory / "post-merge.basicly-original").read_bytes() == body


def test_install_hooks_uses_bundled_binary_without_a_global_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    ok, message = install_hooks(tmp_path, ["pre-commit", "commit-msg"])
    assert ok, message
    assert missing_hook_installations(tmp_path, ["pre-commit", "commit-msg"]) == []
    assert (tmp_path / ".basicly/bin/lefthook").exists() or (
        tmp_path / ".basicly/bin/lefthook.exe"
    ).exists()


def test_install_hooks_no_git_gives_clear_guidance_without_spawning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("pre-commit must not be spawned when there is no .git")

    monkeypatch.setattr(hooks_module.subprocess, "run", _boom)
    ok, message = install_hooks(tmp_path, ["pre-commit"])
    assert ok is False
    assert "not a git repository" in message
    assert "basicly hooks-build" in message


@pytest.mark.parametrize("content", [b"#!/bin/sh\necho foreign\n", b"#!/bin/sh\necho '\xff'\n"])
def test_uninstall_hooks_restores_a_foreign_hook(tmp_path: Path, content: bytes) -> None:
    _init_repo(tmp_path)
    foreign = tmp_path / ".git/hooks/pre-commit"
    foreign.write_bytes(content)
    foreign.chmod(0o755)
    assert install_hooks(tmp_path, ["pre-commit"])[0]
    assert (foreign.parent / "pre-commit.basicly-original").read_bytes() == content
    assert install_hooks(tmp_path, ["pre-commit"])[0]
    assert uninstall_hooks(tmp_path, ["pre-commit"])[0]
    assert foreign.read_bytes() == content


@pytest.mark.parametrize("valid, stale_lock", [(True, False), (False, False), (True, True)])
def test_staged_checks_reuse_prepared_uv_environment_without_building(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valid: bool, stale_lock: bool
) -> None:
    monkeypatch.delenv("UV_NO_SYNC", raising=False)
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    monkeypatch.setenv("UV_PYTHON", sys.executable)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(tmp_path / ".venv")], check=True
    )
    (tmp_path / ".gitignore").write_text(".venv/\n.basicly/bin/\nuv-cache/\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "snapshot-check"\nversion = "0.0.0"\n'
        '[build-system]\nrequires = ["missing-snapshot-build-backend"]\n'
        'build-backend = "missing_snapshot_build_backend"\n'
    )
    subprocess.run(["uv", "lock", "--offline"], cwd=tmp_path, check=True)
    if stale_lock:
        project = tmp_path / "pyproject.toml"
        project.write_text(
            project.read_text().replace(
                'version = "0.0.0"',
                'version = "0.0.0"\ndependencies = ["missing-snapshot-dependency"]',
            )
        )
    _materialize_hooks(tmp_path)
    lefthook_binary.sync_binary(tmp_path)
    source = tmp_path / CORE_HOOKS_DIR / "uv-check.py"
    source.write_text(
        "import subprocess\nraise SystemExit(subprocess.call("
        '["uv", "run", "python", "-c", '
        "\"from pathlib import Path; assert Path('file.txt').read_text() == 'staged-ok'\"]))\n"
    )
    lefthook_config.sync_config(
        tmp_path,
        [HookSpec("uv-check", "uv-check.py", "pre-commit", always_run=True)],
        CORE_HOOKS_DIR.as_posix(),
    )
    target = tmp_path / "file.txt"
    target.write_text("staged-ok" if valid else "invalid")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    target.write_text("staged-ok")
    result = subprocess.run(
        [
            sys.executable,
            str(tmp_path / CORE_HOOKS_DIR / "lefthook-runtime.py"),
            "git",
            "pre-commit",
        ],
        cwd=tmp_path,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert (result.returncode == 0) == (valid and not stale_lock), result.stdout + result.stderr
    assert target.read_text() == "staged-ok"
    assert "missing-snapshot-build-backend" not in result.stdout + result.stderr
    if not valid:
        assert "AssertionError" in result.stdout + result.stderr
    if stale_lock:
        assert "missing-snapshot-dependency" in result.stdout + result.stderr


@pytest.mark.parametrize("stage", ["pre-commit", "pre-push"])
@pytest.mark.parametrize("changed", [False, True])
@pytest.mark.parametrize("gate_code", [0, 1])
def test_native_gate_runs_once_without_changing_foreign_file_filters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, changed: bool, gate_code: int
) -> None:
    tmp_path = tmp_path / "repo"
    tmp_path.mkdir()
    monkeypatch.setenv("UV_PYTHON", sys.executable)
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    counter = tmp_path.parent / (tmp_path.name + ".runs")
    foreign_counter = tmp_path.parent / (tmp_path.name + ".foreign")
    monkeypatch.setenv("BASICLY_TEST_GATE_COUNTER", str(counter))
    monkeypatch.setenv("BASICLY_TEST_FOREIGN_COUNTER", str(foreign_counter))
    subprocess.run(["git", "init", "-qb", "main"], cwd=tmp_path, check=True)
    for key, value in (("user.name", "Test"), ("user.email", "test@example.invalid")):
        subprocess.run(["git", "config", key, value], cwd=tmp_path, check=True)
    (tmp_path / "seed.txt").write_text("initial")
    subprocess.run(["git", "add", "seed.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    subprocess.run(["git", "remote", "add", "origin", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=tmp_path, check=True
    )
    for key, value in (("branch.main.remote", "origin"), ("branch.main.merge", "refs/heads/main")):
        subprocess.run(["git", "config", key, value], cwd=tmp_path, check=True)
    if changed:
        (tmp_path / "seed.txt").write_text("changed")
        subprocess.run(["git", "add", "seed.txt"], cwd=tmp_path, check=True)
        if stage == "pre-push":
            subprocess.run(["git", "commit", "-qm", "changed"], cwd=tmp_path, check=True)
    _materialize_hooks(tmp_path)
    lefthook_binary.sync_binary(tmp_path)
    (tmp_path / CORE_HOOKS_DIR / "gate.py").write_text(
        'import os\nfrom pathlib import Path\np=Path(os.environ["BASICLY_TEST_GATE_COUNTER"])\n'
        'p.write_text(p.read_text()+"run\\n" if p.exists() else "run\\n")\n'
        f"raise SystemExit({gate_code})\n"
    )
    (tmp_path / CORE_HOOKS_DIR / "foreign-marker.py").write_text(
        "import os\nfrom pathlib import Path\n"
        'Path(os.environ["BASICLY_TEST_FOREIGN_COUNTER"]).write_text("unexpected")\n'
    )
    (tmp_path / "lefthook.yml").write_text(
        stage + ':\n  commands:\n    foreign:\n      glob: "*.foreign"\n'
        "      run: uv run --no-project --no-python-downloads python "
        ".basicly/core/hooks/foreign-marker.py\n"
    )
    lefthook_config.sync_config(
        tmp_path, [HookSpec("gate", "gate.py", stage, always_run=True)], CORE_HOOKS_DIR.as_posix()
    )
    assert install_hooks(tmp_path, [stage])[0]
    result = subprocess.run(
        ["sh", str(tmp_path / ".git/hooks" / stage)],
        cwd=tmp_path,
        input="",
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert result.returncode == gate_code, result.stdout + result.stderr
    assert counter.read_text() == "run\n"
    assert not foreign_counter.exists()
