from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from basicly import ci_records, scaffolds
from basicly.cli import cmd_check
from basicly.scaffolds import CONSUMER_CI_WORKFLOW
from tests.test_kit_vault_cli_wiring import _environment
from tests.test_kit_vault_share import bare
from tests.test_vault_harness_hooks import _frozen

__all__ = ["_environment", "bare"]
ROOT = Path(__file__).resolve().parents[1]


def test_no_ci_workflow_or_scaffold_reads_tracker_records() -> None:
    workflows = sorted((ROOT / ".github/workflows").glob("*.yml"))
    texts = [CONSUMER_CI_WORKFLOW, *(path.read_text(encoding="utf-8") for path in workflows)]
    assert [ci_records.violations(text) for text in texts] == [[] for _ in texts]


def test_the_records_gate_names_each_line_of_the_0_46_3_scaffold(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = tmp_path / "basicly-gates.yml"
    old.write_text(
        '            python3 .basicly/core/hooks/tracker-commit-msg.py "${msg_file}" || failed=1\n'
        "        run: uvx --from basicly basicly verify --mode full\n",
        encoding="utf-8",
    )

    assert ci_records.main([str(old)]) == 1
    said = capsys.readouterr().out
    assert f"{old}:1: runs the record-existence hook" in said
    assert f"{old}:2: runs verify without --ci" in said


def _check(root: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    (root / "basicly.toml").write_text("", encoding="utf-8")
    (root / ".basicly").mkdir(exist_ok=True)
    (root / ".basicly/generated-manifest.json").write_text('{"outputs": {}}', encoding="utf-8")
    monkeypatch.chdir(root)
    return cmd_check(argparse.Namespace())


def test_basicly_check_names_each_workflow_line_that_reads_records_and_its_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / ".github" / "workflows"
    folder.mkdir(parents=True)
    (folder / "gates.yml").write_text(
        '            python3 .basicly/core/hooks/tracker-commit-msg.py "${msg_file}" || failed=1\n',
        encoding="utf-8",
    )
    (folder / "other.yaml").write_text(
        "        run: uvx basicly verify --mode full\n", encoding="utf-8"
    )

    code = _check(tmp_path, monkeypatch)

    err = capsys.readouterr().err
    assert code == 1
    assert ".github/workflows/gates.yml:1: runs the record-existence hook" in err
    assert ".github/workflows/other.yaml:1: runs verify without --ci" in err
    assert err.count("fix: remove the line, or add --ci to verify --mode full") == 2


def test_basicly_check_names_workflow_lines_even_when_it_refuses_for_version_skew(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / ".github" / "workflows"
    folder.mkdir(parents=True)
    (folder / "gates.yml").write_text(
        "        run: uvx basicly verify --mode full\n", encoding="utf-8"
    )
    state = tmp_path / ".basicly" / "state" / "install.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        '{"schema_version": 1, "basicly_version": "0.45.0", '
        '"installed_at": "2026-07-23T00:00:00+00:00", "core": {}}',
        encoding="utf-8",
    )

    code = _check(tmp_path, monkeypatch)

    err = capsys.readouterr().err
    assert code == 1
    assert "Version skew" in err
    assert ".github/workflows/gates.yml:1: runs verify without --ci" in err


TERMINAL_PRE_PUSH = (
    'import subprocess\nsubprocess.run(["uv", "run", "pytest", "tests/"], check=True)\n'
)


@pytest.mark.parametrize(
    ("script", "flagged"),
    [
        (TERMINAL_PRE_PUSH, False),
        ((ROOT / ".basicly/core/hooks/pre-push.py").read_text(encoding="utf-8"), True),
        (
            'CMD = ["basicly", "verify", "--mode", "full"]\nrun("basicly verify --mode full")\n',
            True,
        ),
        ("run('basicly verify --mode full --ci')\n", False),
    ],
    ids=["terminal-pytest-only", "basicly-own-pre-push", "verify-full", "verify-full-ci"],
)
def test_basicly_check_flags_a_hook_script_line_only_when_the_script_runs_full_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    script: str,
    flagged: bool,
) -> None:
    hook = tmp_path / ".scripts" / "git-hooks" / "pre-push.py"
    hook.parent.mkdir(parents=True)
    hook.write_text(script, encoding="utf-8")
    folder = tmp_path / ".github" / "workflows"
    folder.mkdir(parents=True)
    (folder / "quality-gates.yml").write_text(
        "        run: uv run python .scripts/git-hooks/pre-push.py\n", encoding="utf-8"
    )

    code = _check(tmp_path, monkeypatch)

    err = capsys.readouterr().err
    expected = (
        ".github/workflows/quality-gates.yml:1: runs .scripts/git-hooks/pre-push.py, "
        "which runs the local full checks without --ci"
    )
    assert (code, expected in err) == ((1, True) if flagged else (0, False)), err


def test_basicly_check_still_flags_a_direct_verify_line_without_ci(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / ".github" / "workflows"
    folder.mkdir(parents=True)
    (folder / "gates.yml").write_text(
        "        run: uv run basicly verify --mode full\n"
        "        run: uv run basicly verify --mode full --ci\n",
        encoding="utf-8",
    )

    assert _check(tmp_path, monkeypatch) == 1
    err = capsys.readouterr().err
    assert ".github/workflows/gates.yml:1: runs verify without --ci" in err
    assert "gates.yml:2:" not in err


def test_basicly_check_passes_a_repo_whose_workflows_read_no_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / ".github" / "workflows"
    folder.mkdir(parents=True)
    (folder / "gates.yml").write_text(CONSUMER_CI_WORKFLOW, encoding="utf-8")

    code = _check(tmp_path, monkeypatch)

    assert code == 0, capsys.readouterr().err


def test_basicly_check_passes_a_repo_with_no_workflows_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _check(tmp_path, monkeypatch)

    assert code == 0, capsys.readouterr().err


def _unreachable(tmp_path: Path, bare: Path) -> Path:
    root = _frozen(tmp_path, bare)
    shutil.rmtree(bare)
    return root


def _ci_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "BASICLY_CI_NO_RECORDS")}
    return env | {
        "BASICLY_TRACKER_STATE": str(tmp_path / "ci-state"),
        "GIT_TERMINAL_PROMPT": "0",
        "PYTHONPATH": str(ROOT / "src"),
        **extra,
    }


def _ci_verify(root: Path, env: dict[str, str], *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "basicly.cli", *argv],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _scaffold_job(name: str) -> str:
    text = scaffolds.CONSUMER_CI_WORKFLOW
    start = text.index(f"  {name}:\n")
    following = re.search(r"\n  [a-z][a-z0-9-]*:\n", text[start + 1 :])
    return text[start : start + 1 + following.start()] if following else text[start:]


def test_rehearsal_both_ci_jobs_pass_on_a_frozen_repo_with_an_unreachable_vault(
    tmp_path: Path, bare: Path
) -> None:
    root = _unreachable(tmp_path, bare)
    for name, value in (("user.name", "t"), ("user.email", "t@example.invalid")):
        subprocess.run(["git", "-C", str(root), "config", name, value], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-q", "--no-verify", "-m", "chore: freeze (acme-1)"],
        check=True,
    )
    block = re.search(r"run: \|\n((?:          .*\n|\n)+)", _scaffold_job("commit-messages"))
    assert block
    script = "\n".join(line[10:] for line in block.group(1).splitlines())
    verify_line = next(
        line for line in _scaffold_job("gates").splitlines() if "verify --mode" in line
    )
    argv = verify_line.split("run: ", 1)[1].replace(scaffolds.UVX_COMMAND, "").split()
    env = _ci_env(tmp_path, BASICLY_CI_ALL_HISTORY="true", BASICLY_CI_EVENT="workflow_dispatch")

    commits = subprocess.run(
        ["bash", "-c", script], cwd=root, env=env, capture_output=True, text=True, check=False
    )
    checks = _ci_verify(root, env, *argv)
    old = _ci_verify(root, env, "verify", "--mode", "full")

    assert commits.returncode == 0, commits.stdout + commits.stderr
    assert checks.returncode == 0, checks.stdout + checks.stderr
    state = tmp_path / "ci-state"
    assert not state.exists() or not any(state.rglob("*"))
    assert old.returncode == 0, old.stdout + old.stderr


def test_ci_verify_fails_a_check_that_reads_records_and_names_a_local_only_one(
    tmp_path: Path, bare: Path
) -> None:
    root = _unreachable(tmp_path, bare)
    reader = (
        "import importlib.util as u, sys;"
        "s=u.spec_from_file_location('e','.basicly/core/kit/tracker/events.py');"
        "m=u.module_from_spec(s);sys.modules['e']=m;s.loader.exec_module(m);"
        "m.read_events('.basicly/ledger-archive')"
    )
    (root / "basicly.toml").write_text(
        "[[verify.checks]]\n"
        'name = "reads-records"\n'
        f"command = [{sys.executable!r}, '-c', {reader!r}]\n"
        'modes = ["full"]\n'
        "[[verify.checks]]\n"
        'name = "landing-only"\n'
        f"command = [{sys.executable!r}, '-c', 'raise SystemExit(3)']\n"
        'modes = ["full"]\n'
        "local_only = true\n",
        encoding="utf-8",
    )

    done = _ci_verify(root, _ci_env(tmp_path), "verify", "--mode", "full", "--ci")
    said = done.stdout + done.stderr
    log = (root / ".basicly/usage/verify-fail-reads-records.log").read_text(encoding="utf-8")

    assert done.returncode == 1
    assert "FAIL: reads-records" in said
    assert "CI never reads tracker records" in log
    assert "landing-only: local only" in said


def test_the_ci_guard_covers_the_ledger_a_worktree_redirects_to(tmp_path: Path) -> None:
    main = tmp_path / "main"
    lane = tmp_path / "lane"
    (main / ".basicly/ledger").mkdir(parents=True)
    (lane / ".basicly/ledger").mkdir(parents=True)
    (lane / ".basicly/ledger/redirect").write_text(f"{main}\n", encoding="utf-8")

    roots = ci_records.guard_value(lane).split(os.pathsep)

    assert roots == [str(lane.resolve()), str(main.resolve())]
