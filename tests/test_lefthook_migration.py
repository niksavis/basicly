from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from basicly import hooks, lefthook_binary, lefthook_config
from basicly.catalog import bundled_catalog_root
from tests.native_hooks import invoke, repository


@pytest.fixture(autouse=True)
def isolated_uv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    monkeypatch.setenv("UV_PYTHON", sys.executable)
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")


def test_native_runner_changes_exit_two_and_frames_stdout(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    (repo / "event.py").write_text('import sys\nprint("protocol")\nsys.exit(2)\n')
    (repo / "lefthook.yml").write_text(
        "event:\n  commands:\n    event:\n      run: uv run --no-project python event.py\n"
    )
    result = subprocess.run(
        [str(lefthook_binary.binary_path(repo)), "run", "event", "--no-auto-install"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 1
    assert "protocol" in result.stdout
    assert "lefthook" in result.stdout


def test_new_shared_stage_allows_an_unmigrated_checkout_without_an_old_hook(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    assert hooks.install_hooks(repo, ["post-merge"])[0]
    runtime = repo / ".basicly/core/hooks/lefthook-runtime.py"
    runtime.rename(runtime.with_suffix(".disabled"))
    result = subprocess.run(
        ["sh", str(repo / ".git/hooks/post-merge")], cwd=repo, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("stage, block", [("post-merge", "compact"), ("commit-msg", "claim")])
def test_tracker_migration_retires_only_its_managed_git_block(
    tmp_path: Path, stage: str, block: str
) -> None:
    repo = repository(tmp_path)
    original = repo / f".git/hooks/{stage}"
    foreign = b"#!/bin/sh\nprintf foreign >> foreign-output\n"
    content = (
        foreign
        + f"# >>> basicly-tracker {block} >>>\n".encode()
        + b"printf obsolete >> obsolete-output\n"
        + f"# <<< basicly-tracker {block} <<<\n".encode()
    )
    original.write_bytes(content)
    original.chmod(0o755)
    (repo / ".basicly/core/hooks/fold.py").write_text(
        'from pathlib import Path\nPath("managed-output").write_text("folded")\n'
    )
    lefthook_config.sync_config(
        repo, [hooks.HookSpec("fold", "fold.py", stage)], ".basicly/core/hooks"
    )
    assert hooks.install_hooks(repo, [stage])[0]
    assert hooks.install_hooks(repo, [stage])[0]
    result = subprocess.run(["sh", str(original)], cwd=repo, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert (repo / "foreign-output").read_text() == "foreign"
    assert (repo / "managed-output").read_text() == "folded"
    assert not (repo / "obsolete-output").exists()
    assert (original.parent / f"{stage}.basicly-original").read_bytes() == content
    runtime = repo / ".basicly/core/hooks/lefthook-runtime.py"
    runtime.rename(runtime.with_suffix(".disabled"))
    fallback = subprocess.run(["sh", str(original)], cwd=repo, capture_output=True, check=False)
    assert fallback.returncode == 0, fallback.stderr
    assert (repo / "obsolete-output").read_text() == "obsolete"
    assert hooks.uninstall_hooks(repo, [stage])[0]
    assert original.read_bytes() == foreign
    assert not (original.parent / f"{stage}.basicly-foreign").exists()


def test_agent_dispatch_replays_payload_and_preserves_blocking_protocol(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    source = repo / ".basicly/core/hooks"
    (source / "first.py").write_text(
        "import json,sys\np=json.load(sys.stdin)\n"
        'assert p["tool_input"]["command"] == "same payload"\n'
        'print(json.dumps({"hookSpecificOutput":{"additionalContext":"first"}}))\n'
    )
    (source / "second.py").write_text(
        "import json,sys\np=json.load(sys.stdin)\n"
        'assert p["tool_input"]["command"] == "same payload"\n'
        'print("blocked",file=sys.stderr)\nsys.exit(2)\n'
    )
    specs = [
        hooks.HookSpec(name, f"{name}.py", "pretooluse", manager="claude", matcher="Bash")
        for name in ("first", "second")
    ]
    lefthook_config.sync_config(repo, specs, ".basicly/core/hooks")
    result = invoke(
        repo,
        "claude",
        "pretooluse",
        {
            "tool_name": "Bash",
            "tool_input": {"command": "same payload"},
        },
    )
    assert result.returncode == 2, result.stderr
    assert json.loads(result.stdout) == {"hookSpecificOutput": {"additionalContext": "first"}}
    assert result.stderr == "blocked\n"
    control = invoke(repo, "claude", "pretooluse", {"tool_name": "Read"})
    assert control.returncode == 0
    assert control.stdout == ""
    assert control.stderr == ""


def test_staged_snapshot_validates_index_and_preserves_unstaged_bytes(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    script = repo / ".basicly/core/hooks/check.py"
    script.write_text('from pathlib import Path\nassert Path("file.txt").read_text() == "staged"\n')
    lefthook_config.sync_config(
        repo,
        [hooks.HookSpec("check", "check.py", "pre-commit", always_run=True)],
        ".basicly/core/hooks",
    )
    target = repo / "file.txt"
    target.write_text("staged")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    target.write_text("unstaged")
    result = invoke(repo, "git", "pre-commit")
    assert result.returncode == 0, result.stdout + result.stderr
    assert target.read_text() == "unstaged"
    staged = subprocess.run(
        ["git", "show", ":file.txt"],
        cwd=repo,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    assert staged.stdout == "staged"


def test_unchanged_dotenv_index_entry_does_not_block_other_staged_files(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input="synthetic fixture\n",
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo", f"100644,{blob},.env.example"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=repo, check=True)
    script = repo / ".basicly/core/hooks/check.py"
    script.write_text('from pathlib import Path\nassert not Path(".env.example").exists()\n')
    spec = hooks.HookSpec("check", "check.py", "pre-commit", always_run=True)
    lefthook_config.sync_config(repo, [spec], ".basicly/core/hooks")
    subprocess.run(["git", "add", ".basicly", "lefthook.yml"], cwd=repo, check=True)
    result = invoke(repo, "git", "pre-commit")
    assert result.returncode == 0, result.stdout + result.stderr


def test_foreign_git_hook_executes_after_reinstallation(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    directory = repo / ".git/hooks"
    original = directory / "pre-commit"
    original.write_text("#!/bin/sh\nprintf ran >> foreign-output\n")
    original.chmod(0o755)
    assert hooks.install_hooks(repo, ["pre-commit"])[0]
    assert hooks.install_hooks(repo, ["pre-commit"])[0]
    lefthook_config.sync_config(
        repo,
        [hooks.HookSpec("allow", "allow.py", "pre-commit", always_run=True)],
        ".basicly/core/hooks",
    )
    (repo / ".basicly/core/hooks/allow.py").write_text("raise SystemExit(0)\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    result = subprocess.run(
        ["git", "hook", "run", "pre-commit"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (repo / "foreign-output").read_text() == "ran"


@pytest.mark.parametrize("excluded", [False, True])
def test_consumer_exclusion_keeps_filename_gate_semantics(tmp_path: Path, excluded: bool) -> None:
    repo = repository(tmp_path)
    (repo / "vendor").mkdir()
    (repo / "vendor/types.py").write_text("vendor types\n", encoding="utf-8")
    (repo / ".basicly/core/hooks/refuse.py").write_text("raise SystemExit(1)\n")
    (repo / "lefthook.yml").write_text(
        "pre-commit:\n  commands:\n    basicly-dispatch:\n      env:\n"
        f"        BASICLY_SECRET_EXCLUDE: '{'^vendor/' if excluded else ''}'\n",
        encoding="utf-8",
    )
    spec = hooks.HookSpec("refuse", "refuse.py", "pre-commit", files=r"^vendor/")
    lefthook_config.sync_config(repo, [spec], ".basicly/core/hooks")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    result = invoke(repo, "git", "pre-commit")
    assert (result.returncode == 0) == excluded, result.stdout + result.stderr


@pytest.mark.parametrize("operation", ["Add File", "Update File", "Delete File", "Move to"])
def test_codex_patch_blocks_every_affected_generated_path(tmp_path: Path, operation: str) -> None:
    repo = repository(tmp_path)
    source = repo / ".basicly/core/hooks"
    shutil.copyfile(bundled_catalog_root() / "hooks/protect-generated.py", source / "guard.py")
    spec = hooks.HookSpec("guard", "guard.py", "pretooluse", manager="codex", matcher="apply_patch")
    lefthook_config.sync_config(repo, [spec], ".basicly/core/hooks")
    nested = repo / "nested"
    nested.mkdir()
    (nested / "generated.md").write_text("Generated by basicly\n", encoding="utf-8")
    (nested / "plain.md").write_text("plain\n", encoding="utf-8")
    patch = (
        f"*** Begin Patch\n*** Update File: plain.md\n*** {operation}: generated.md\n*** End Patch"
    )
    result = invoke(
        repo,
        "codex",
        "pretooluse",
        {
            "cwd": str(nested),
            "tool_name": "apply_patch",
            "tool_input": {"command": patch},
        },
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "generated by basicly" in result.stderr
    control = invoke(
        repo,
        "codex",
        "pretooluse",
        {
            "cwd": str(nested),
            "tool_name": "apply_patch",
            "tool_input": {"command": "*** Begin Patch\n*** Update File: plain.md\n*** End Patch"},
        },
    )
    assert control.returncode == 0


def test_invalid_staged_bytes_are_refused_even_when_working_bytes_pass(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    script = repo / ".basicly/core/hooks/check.py"
    script.write_text('from pathlib import Path\nassert Path("file.txt").read_text() == "valid"\n')
    lefthook_config.sync_config(
        repo,
        [hooks.HookSpec("check", "check.py", "pre-commit", always_run=True)],
        ".basicly/core/hooks",
    )
    target = repo / "file.txt"
    target.write_text("invalid")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    target.write_text("valid")
    result = invoke(repo, "git", "pre-commit")
    assert result.returncode != 0
    assert target.read_text() == "valid"
    staged = subprocess.run(
        ["git", "show", ":file.txt"],
        cwd=repo,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    assert staged.stdout == "invalid"


def test_staged_fixes_preserve_a_partially_staged_working_file(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    script = repo / ".basicly/core/hooks/fix.py"
    script.write_text(
        'import subprocess\nfrom pathlib import Path\nPath("file.txt").write_text("fixed")\n'
        'subprocess.run(["git","add","file.txt"],check=True)\n'
    )
    lefthook_config.sync_config(
        repo,
        [hooks.HookSpec("fix", "fix.py", "pre-commit", always_run=True)],
        ".basicly/core/hooks",
    )
    target = repo / "file.txt"
    target.write_text("staged")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    target.write_text("working-only")
    result = invoke(repo, "git", "pre-commit")
    assert result.returncode == 0, result.stdout + result.stderr
    assert target.read_text() == "working-only"
    staged = subprocess.run(
        ["git", "show", ":file.txt"],
        cwd=repo,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    assert staged.stdout == "fixed"


def test_ordinary_git_commit_runs_lefthook_and_preserves_message_refusal(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    source = repo / ".basicly/core/hooks"
    (source / "gate.py").write_text('print("pre-commit gate executed")\n')
    (source / "message.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        'assert Path(sys.argv[1]).read_text().startswith("chore: allowed")\n'
        'print("commit-msg gate executed")\n'
    )
    specs = [
        hooks.HookSpec("gate", "gate.py", "pre-commit", always_run=True),
        hooks.HookSpec("message", "message.py", "commit-msg", pass_filenames=True),
    ]
    lefthook_config.sync_config(repo, specs, ".basicly/core/hooks")
    assert hooks.install_hooks(repo, hooks.hook_stages(specs))[0]
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    blocked = subprocess.run(
        ["git", "commit", "-m", "wrong"],
        cwd=repo,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert blocked.returncode != 0
    assert "lefthook" in blocked.stdout + blocked.stderr
    allowed = subprocess.run(
        ["git", "commit", "-m", "chore: allowed"],
        cwd=repo,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    assert "pre-commit gate executed" in allowed.stdout + allowed.stderr
    assert "commit-msg gate executed" in allowed.stdout + allowed.stderr


def test_linked_worktree_uses_its_own_binary_and_respects_shared_hooks_path(tmp_path: Path) -> None:
    repo = repository(tmp_path / "repo")
    scripts = repo / ".basicly/core/hooks"
    (scripts / "allow.py").write_text("raise SystemExit(0)\n")
    specs = [hooks.HookSpec("allow", "allow.py", "pre-commit", always_run=True)]
    lefthook_config.sync_config(repo, specs, ".basicly/core/hooks")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=repo, check=True)
    shared = tmp_path / "shared-hooks"
    subprocess.run(["git", "config", "core.hooksPath", str(shared)], cwd=repo, check=True)
    assert hooks.install_hooks(repo, ["pre-commit"])[0]
    lane = tmp_path / "repo.worktrees/lane"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "lane", str(lane)], cwd=repo, check=True)
    assert not lefthook_binary.binary_path(lane).exists()
    assert hooks.install_hooks(lane, ["pre-commit"])[0]
    assert lefthook_binary.binary_path(lane).is_file()
    assert hooks.missing_hook_installations(lane, ["pre-commit"]) == []
    result = subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "lane"],
        cwd=lane,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert str(repo) not in (shared / "pre-commit").read_text()
