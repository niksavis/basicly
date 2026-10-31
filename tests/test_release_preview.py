from __future__ import annotations

import json
import shlex
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path

import pytest

from basicly import release, release_preview
from tests.test_release import no_regen, repo

__all__ = ["no_regen", "repo"]


@pytest.mark.usefixtures("no_regen")
@pytest.mark.parametrize("exit_code", [0, 1])
@pytest.mark.parametrize("stage", ["pre-commit", "commit-msg"])
def test_dry_run_executes_commit_gates_without_changing_the_source(
    repo: Path, exit_code: int, stage: str
) -> None:
    hook = repo / ".git/hooks" / stage
    verdict = "checks passed: 1/1 in 0.01s" if exit_code == 0 else "FAILED: preview-gate (0.01s)"
    script = (
        "from pathlib import Path; "
        "assert '0.6.0' in Path('src/basicly/__init__.py').read_text(); "
        f"print({verdict!r}); "
        f"raise SystemExit({exit_code})"
    )
    python = shlex.quote(Path(sys.executable).as_posix())
    hook.write_text(
        f"#!/bin/sh\nexec {python} -c {shlex.quote(script)}\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    before = {p.relative_to(repo): p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    result = release.run_release(
        repo, release.plan_release(repo, "0.6.0", date="2026-07-26"), issue_id="fx-1", dry_run=True
    )
    assert result.refused is (exit_code != 0)
    assert not result.tagged
    assert result.dry_run
    if exit_code:
        assert "preview-gate" in "\n".join(result.refusals)
    else:
        assert "checks passed: 1/1 in 0.01s" in "\n".join(result.steps)
    after = {p.relative_to(repo): p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    assert before == after


@pytest.mark.usefixtures("no_regen")
def test_preview_hooks_resolve_inside_a_managed_snapshot(repo: Path) -> None:
    script = (
        "import subprocess, tempfile\n"
        "from pathlib import Path\n"
        "root = Path.cwd()\n"
        "with tempfile.TemporaryDirectory() as directory:\n"
        "    snapshot = Path(directory)\n"
        "    (snapshot / '.git').write_text('gitdir: ' + str(root / '.git') + '\\n')\n"
        "    subprocess.run(['git', 'rev-parse', '--path-format=absolute', "
        "'--git-path', 'hooks'], cwd=snapshot, check=True)\n"
    )
    hook = repo / ".git/hooks/pre-commit"
    python = shlex.quote(Path(sys.executable).as_posix())
    hook.write_text(f"#!/bin/sh\nexec {python} -c {shlex.quote(script)}\n", encoding="utf-8")
    hook.chmod(0o755)
    result = release.run_release(
        repo, release.plan_release(repo, "0.6.0", date="2026-07-26"), issue_id="fx-1", dry_run=True
    )
    assert not result.refused, result.refusals


def test_preview_environment_writes_do_not_reach_the_source(repo: Path) -> None:
    marker = repo / ".venv/marker"
    marker.parent.mkdir()
    marker.write_text("original", encoding="utf-8")

    def assemble(scratch: Path) -> None:
        (scratch / ".venv/marker").write_text("reinstalled", encoding="utf-8")
        (scratch / "CHANGELOG.md").write_text("preview", encoding="utf-8")

    release_preview.run(repo, assemble, "chore: preview")
    assert marker.read_text(encoding="utf-8") == "original"


def test_preview_waits_for_automatic_git_maintenance(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "gitconfig"
    trace = tmp_path / "git-trace.jsonl"
    for key, value in (
        ("maintenance.strategy", "incremental"),
        ("maintenance.commit-graph.enabled", "true"),
        ("maintenance.commit-graph.auto", "1"),
        ("maintenance.autoDetach", "true"),
    ):
        subprocess.run(["git", "config", "--file", str(config), key, value], check=True)
    sanitise = release_preview.checkout.sanitised_git_env

    def traced_env(env: Mapping[str, str]) -> dict[str, str]:
        return {
            **sanitise(env),
            "GIT_CONFIG_GLOBAL": str(config),
            "GIT_TRACE2_EVENT": str(trace),
        }

    monkeypatch.setattr(release_preview.checkout, "sanitised_git_env", traced_env)

    def assemble(scratch: Path) -> None:
        (scratch / "CHANGELOG.md").write_text("preview", encoding="utf-8")

    release_preview.run(repo, assemble, "chore: preview")
    deadline = time.monotonic() + 10
    while True:
        events = []
        for line in trace.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        graph = next(
            (
                event
                for event in events
                if event["event"] == "start" and "commit-graph" in event.get("argv", [])
            ),
            None,
        )
        finished = next(
            (
                event
                for event in events
                if graph and event["event"] == "exit" and event["sid"] == graph["sid"]
            ),
            None,
        )
        if finished:
            break
        assert time.monotonic() < deadline, "automatic commit-graph maintenance did not finish"
        time.sleep(0.01)
    commit = next(
        event
        for event in events
        if event["event"] == "start" and event.get("argv", [])[:2] == ["git", "commit"]
    )
    committed = next(
        event for event in events if event["event"] == "exit" and event["sid"] == commit["sid"]
    )
    assert events.index(finished) < events.index(committed)
    assert finished["code"] == 0
