from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from basicly import cli, hooks, lefthook_artifacts, lefthook_binary, release
from basicly.hooks import HookSpec
from basicly.lefthook_config import managed_config, root_config, sync_config


def test_merge_preserves_foreign_hooks_and_is_idempotent() -> None:
    current = "pre-commit:\n  commands:\n    consumer:\n      run: echo hi\n"
    result = root_config(current)
    assert yaml.safe_load(result)["pre-commit"]["commands"]["consumer"] == {"run": "echo hi"}
    assert root_config(result) == result


def test_only_git_hook_groups_follow_child_output() -> None:
    specs = [
        HookSpec("check", "check.py", "pre-push"),
        HookSpec("guard", "guard.py", "PreToolUse", manager="claude"),
        HookSpec("guard", "guard.py", "pretooluse", manager="codex"),
        HookSpec("guard", "guard.py", "preToolUse", manager="copilot"),
    ]
    config = yaml.safe_load(managed_config(specs, ".basicly/core/hooks"))
    assert config["pre-push"]["follow"] is True
    assert all(
        "follow" not in group for name, group in config.items() if name.startswith("basicly-")
    )


def test_hook_entry_quotes_paths_with_spaces() -> None:
    spec = HookSpec("check", "check.py", "pre-commit")
    config = yaml.safe_load(managed_config([spec], "agent config/hooks"))
    command = config["pre-commit"]["commands"]["basicly-dispatch"]
    assert '"agent config/hooks/lefthook-runtime.py"' in command["run"]
    assert (
        json.loads(command["env"]["BASICLY_HOOK_SPECS"])[0]["script"]
        == "agent config/hooks/check.py"
    )


def test_foreign_comments_and_pins_survive_projection() -> None:
    current = (
        "# pinned consumer check\nremotes:\n"
        "  - git_url: https://example.invalid/checks\n    ref: v1\n"
    )
    result = root_config(current)
    assert "# pinned consumer check" in result
    assert yaml.safe_load(result)["remotes"] == yaml.safe_load(current)["remotes"]
    assert root_config(result) == result


def test_consumer_cannot_overwrite_the_managed_dispatcher(tmp_path: Path) -> None:
    config = tmp_path / "lefthook.yml"
    current = "pre-commit:\n  commands:\n    basicly-dispatch:\n      run: echo bypass\n"
    config.write_text(current, encoding="utf-8")
    with pytest.raises(ValueError, match="overrides managed"):
        sync_config(tmp_path, [HookSpec("check", "check.py", "pre-commit")], ".basicly/core/hooks")
    assert config.read_text(encoding="utf-8") == current
    assert not (tmp_path / ".basicly/lefthook.yml").exists()


@pytest.mark.parametrize("filename", ["message with spaces.txt", "message's.txt"])
def test_foreign_commit_message_job_receives_original_git_arguments(
    work_repo: Path, monkeypatch: pytest.MonkeyPatch, filename: str
) -> None:
    monkeypatch.setenv("UV_PYTHON", sys.executable)
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    monkeypatch.setenv("UV_CACHE_DIR", str(work_repo.parent / "uv-cache"))
    subprocess.run(["git", "init", "-q"], cwd=work_repo, check=True)
    lefthook_binary.sync_binary(work_repo)
    for script, result in (
        ("foreign.py", "foreign-arguments"),
        ("managed.py", "managed-arguments"),
    ):
        (work_repo / ".basicly/core/hooks" / script).write_text(
            "import json,sys\nfrom pathlib import Path\n"
            + f'Path("{result}").write_text(json.dumps(sys.argv[1:]))\n'
        )
    (work_repo / "lefthook.yml").write_text(
        "commit-msg:\n  commands:\n    consumer-message:\n"
        "      run: uv run --no-project --no-python-downloads python "
        '.basicly/core/hooks/foreign.py "{1}"\n'
    )
    sync_config(
        work_repo,
        [HookSpec("managed", "managed.py", "commit-msg", pass_filenames=True)],
        ".basicly/core/hooks",
    )
    assert hooks.install_hooks(work_repo, ["commit-msg"])[0]
    message = work_repo / filename
    message.write_text("message")
    result = subprocess.run(
        ["git", "hook", "run", "commit-msg", "--", str(message)],
        cwd=work_repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for output in ("foreign-arguments", "managed-arguments"):
        assert json.loads((work_repo / output).read_text()) == [str(message)]


def test_release_regenerates_lefthook_commands_for_the_bumped_version(
    work_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release.shipped_core, "write_digests", lambda *_args: None)
    monkeypatch.setattr(release, "_refresh_generated_docs", lambda _root: None)
    version_file = work_repo / release.VERSION_FILE
    version_file.write_text(
        release.VERSION_RE.sub('__version__ = "9.8.7"', version_file.read_text()), encoding="utf-8"
    )

    release._regenerate(work_repo)

    config = yaml.safe_load((work_repo / ".basicly/lefthook.yml").read_text())
    command = json.loads(
        config["post-merge"]["commands"]["basicly-dispatch"]["env"]["BASICLY_FOLD_COMMAND"]
    )
    assert command == [
        "uvx",
        "--from",
        "https://github.com/niksavis/basicly/releases/download/v9.8.7/basicly-9.8.7-py3-none-any.whl",
        "basicly",
        "tracker",
        "fold",
    ]


def test_configuration_minimum_follows_the_pinned_artifact_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lefthook_artifacts, "VERSION", "9.8.7")

    assert yaml.safe_load(managed_config([], ".basicly/core/hooks"))["min_version"] == "9.8.7"


def test_runtime_only_bootstrap_preserves_and_detects_configuration_drift(
    work_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(work_repo)
    assert not lefthook_binary.binary_path(work_repo).exists()
    assert cli.main(["hooks-check"]) == 1
    config = work_repo / "lefthook.yml"
    original = config.read_bytes()

    assert cli.main(["hooks-build", "--runtime-only"]) == 0

    assert not lefthook_binary.binary_mismatches(work_repo)
    assert config.read_bytes() == original
    assert cli.main(["hooks-check"]) == 0
    managed = work_repo / ".basicly/lefthook.yml"
    managed.write_text("min_version: 0.0.0\n", encoding="utf-8")
    corrupted = managed.read_bytes()

    assert cli.main(["hooks-build", "--runtime-only"]) == 0
    assert managed.read_bytes() == corrupted
    assert cli.main(["hooks-check"]) == 1
    assert config.read_bytes() == original
