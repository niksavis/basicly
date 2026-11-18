from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import ci_records, scaffolds
from basicly.scaffolds import CONSUMER_CI_WORKFLOW
from tests.test_kit_vault_cli_wiring import _environment
from tests.test_kit_vault_share import bare
from tests.test_vault_harness_hooks import _frozen

if TYPE_CHECKING:
    import pytest

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
    assert old.returncode == 1
    assert "vault" in old.stdout + old.stderr


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
