from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest
import yaml

from basicly.scaffolds import CONSUMER_CI_WORKFLOW

ROOT = Path(__file__).resolve().parents[1]
HOOKS = Path(".basicly/core/hooks")
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="CI workflows require Bash")


class History(NamedTuple):
    root: Path
    base: str
    before: str
    head: str


def _env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, env=_env(), text=True).strip()


def _history(tmp_path: Path, seed: str = "fix: seed (fx-1)") -> History:
    root = tmp_path / "consumer"
    hooks = root / HOOKS
    hooks.mkdir(parents=True)
    for name in ("commit-msg.py", "tracker-commit-msg.py"):
        shutil.copyfile(ROOT / HOOKS / name, hooks / f"real-{name}")
        (hooks / name).write_text(
            "import runpy\nfrom pathlib import Path\n"
            "with Path('calls.log').open('a') as log:\n    log.write('called\\n')\n"
            f"runpy.run_path(str(Path(__file__).with_name('real-{name}')), run_name='__main__')\n",
            encoding="utf-8",
        )
    guard = ROOT / HOOKS / "commit-range.py"
    if guard.exists():
        shutil.copyfile(guard, hooks / guard.name)
    ledger = root / ".basicly/ledger"
    ledger.mkdir()
    (ledger / "events-test.jsonl").write_text('{"record":"fx-1"}\n', encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "config", "maintenance.auto", "false")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", seed)
    base = _git(root, "rev-parse", "HEAD")
    for number in (1, 2):
        (root / "app.txt").write_text(str(number), encoding="utf-8")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", f"fix: change {number} (fx-1)")
    return History(root, base, _git(root, "rev-parse", "HEAD^"), _git(root, "rev-parse", "HEAD"))


@pytest.fixture(params=["dogfood", "consumer"])
def workflow(request: pytest.FixtureRequest) -> dict:
    text = (
        (ROOT / ".github/workflows/quality-gates.yml").read_text(encoding="utf-8")
        if request.param == "dogfood"
        else CONSUMER_CI_WORKFLOW
    )
    return yaml.safe_load(text)


def _run(
    workflow: dict,
    history: History,
    *,
    event: str = "workflow_dispatch",
    base: str = "",
    audit: str = "false",
) -> subprocess.CompletedProcess[str]:
    values = {
        "github.event_name": event,
        "github.sha": history.head,
        "github.event.before": base if event == "push" else "",
        "github.event.pull_request.head.sha": history.head if event == "pull_request" else "",
        "github.event.pull_request.base.sha": base if event == "pull_request" else "",
        "inputs.base": base if event == "workflow_dispatch" else "",
        "inputs.all_history": audit if event == "workflow_dispatch" else "",
    }

    def render(text: str) -> str:
        def value(match: re.Match[str]) -> str:
            return next(
                (values.get(k.strip(), "") for k in match[1].split("||") if values.get(k.strip())),
                "",
            )

        return re.sub(r"\$\{\{\s*(.*?)\s*\}\}", value, text)

    step = next(s for s in workflow["jobs"]["commit-messages"]["steps"] if "run" in s)
    env = {**_env(), **{key: render(str(value)) for key, value in step.get("env", {}).items()}}
    script = render(step["run"]).replace("uv run python ", "python3 ")
    script = script.replace("python3 ", shlex.quote(Path(sys.executable).as_posix()) + " ")
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        cwd=history.root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_manual_run_without_scope_refuses_before_invoking_message_hooks(
    tmp_path: Path, workflow: dict
) -> None:
    history = _history(tmp_path)
    result = _run(workflow, history)
    assert result.returncode != 0
    assert "commit scope" in result.stderr.lower()
    assert not (history.root / "calls.log").exists()


@pytest.mark.parametrize("event", ["push", "pull_request", "workflow_dispatch"])
def test_valid_ranges_check_only_the_selected_commits(
    tmp_path: Path, workflow: dict, event: str
) -> None:
    history = _history(tmp_path, "legacy history before harness installation")
    result = _run(
        workflow, history, event=event, base=history.before if event == "push" else history.base
    )
    assert result.returncode == 0, result.stdout + result.stderr
    calls = (history.root / "calls.log").read_text().splitlines()
    assert len(calls) == (2 if event == "push" else 4)


@pytest.mark.parametrize("base", ["HEAD~1", "--all", "f" * 40, "0" * 40])
def test_invalid_manual_base_refuses_before_message_hooks(
    tmp_path: Path, workflow: dict, base: str
) -> None:
    history = _history(tmp_path)
    result = _run(workflow, history, base=base)
    assert result.returncode != 0
    assert not (history.root / "calls.log").exists()


def test_history_audit_requires_explicit_opt_in_and_still_checks_legacy_messages(
    tmp_path: Path, workflow: dict
) -> None:
    history = _history(tmp_path, "legacy history before harness installation")
    result = _run(workflow, history, audit="true")
    assert result.returncode != 0
    assert len((history.root / "calls.log").read_text().splitlines()) == 6


def test_explicit_history_audit_can_pass(tmp_path: Path, workflow: dict) -> None:
    history = _history(tmp_path)
    result = _run(workflow, history, audit="true")
    assert result.returncode == 0, result.stdout + result.stderr
    assert len((history.root / "calls.log").read_text().splitlines()) == 6


def test_empty_range_is_not_reported_as_success(tmp_path: Path, workflow: dict) -> None:
    history = _history(tmp_path)
    result = _run(workflow, history, base=history.head)
    assert result.returncode != 0
    assert not (history.root / "calls.log").exists()


@pytest.mark.parametrize("before", ["", "0" * 40])
def test_push_without_a_before_commit_requires_an_explicit_manual_choice(
    tmp_path: Path, workflow: dict, before: str
) -> None:
    history = _history(tmp_path)
    result = _run(workflow, history, event="push", base=before)
    assert result.returncode != 0
    assert "run manually" in result.stderr
    assert not (history.root / "calls.log").exists()


def test_diverged_pr_base_is_supported_but_not_accepted_as_a_manual_ancestor(
    tmp_path: Path, workflow: dict
) -> None:
    history = _history(tmp_path)
    _git(history.root, "switch", "-qc", "advanced", history.base)
    (history.root / "base.txt").write_text("advanced", encoding="utf-8")
    _git(history.root, "add", "-A")
    _git(history.root, "commit", "-qm", "fix: advance base (fx-1)")
    advanced = _git(history.root, "rev-parse", "HEAD")
    _git(history.root, "switch", "-q", "main")
    refused = _run(workflow, history, base=advanced)
    assert refused.returncode != 0
    assert not (history.root / "calls.log").exists()
    result = _run(workflow, history, event="pull_request", base=advanced)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len((history.root / "calls.log").read_text().splitlines()) == 4


@pytest.mark.parametrize("message", ["bad selected message", "fix: unknown issue (fx-999)"])
def test_invalid_selected_messages_still_fail(tmp_path: Path, workflow: dict, message: str) -> None:
    history = _history(tmp_path)
    _git(history.root, "commit", "--allow-empty", "-qm", message)
    history = history._replace(head=_git(history.root, "rev-parse", "HEAD"))
    result = _run(workflow, history, base=history.before)
    assert result.returncode != 0
    assert len((history.root / "calls.log").read_text().splitlines()) == 4


def test_issue_ids_in_the_message_body_are_still_valid(tmp_path: Path, workflow: dict) -> None:
    history = _history(tmp_path)
    _git(history.root, "commit", "--allow-empty", "-qm", "fix: body issue\n\nRefs: fx-1")
    history = history._replace(head=_git(history.root, "rev-parse", "HEAD"))
    result = _run(workflow, history, base=history.before)
    assert result.returncode == 0, result.stdout + result.stderr


def test_conflicting_scope_choices_refuse_before_hooks(tmp_path: Path, workflow: dict) -> None:
    history = _history(tmp_path)
    result = _run(workflow, history, base=history.base, audit="true")
    assert result.returncode != 0
    assert not (history.root / "calls.log").exists()


def test_manual_input_is_data_not_shell_syntax(tmp_path: Path, workflow: dict) -> None:
    history = _history(tmp_path)
    result = _run(workflow, history, base="$(touch injected.txt)")
    assert result.returncode != 0
    assert not (history.root / "injected.txt").exists()
    assert not (history.root / "calls.log").exists()
