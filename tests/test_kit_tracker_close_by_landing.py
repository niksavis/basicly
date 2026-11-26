from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

KIT_DIR = Path(__file__).parent.parent / ".basicly" / "core" / "kit" / "tracker"
TRIGGER = "When a person drafts a story, I want an agent to review it, so I can trust it."
SHAPED = ("--description", TRIGGER, "--acceptance", "- it is reviewed", "--requirements", "- none")
SHA = "a" * 40
RUN = "https://github.com/acme/app/actions/runs/42"
TESTS = json.dumps({"it is reviewed": "t.py"})


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cli = _load(KIT_DIR / "cli.py", "tracker_cli_close_by_landing")
landing = cli.commands.closing.landing
events = cli.events


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> dict[str, Any]:
    capsys.readouterr()
    cli.main(list(argv))
    return json.loads(capsys.readouterr().out)


def _create(capsys: pytest.CaptureFixture[str], ledger: Path, *extra: str) -> str:
    made = _run(capsys, "create", str(ledger), "--prefix", "acme", "--title", "draft", *extra)
    return made["record"]


def _git(files: Sequence[str] = ("t.py",)):
    def run(args: Sequence[str], cwd: Path) -> str:
        words = list(args)
        if words[:2] == ["rev-parse", "--verify"]:
            return SHA + "\n"
        if words[:2] == ["rev-parse", "--show-toplevel"]:
            return str(cwd) + "\n"
        if words[:2] == ["cat-file", "-e"] and words[2].split(":", 1)[1] not in files:
            raise landing.LandingEvidenceError("missing")
        if words[:3] == ["rev-parse", "-q", "--verify"] and words[3] != "refs/tags/v1.0.0":
            raise landing.LandingEvidenceError("no tag")
        return ""

    return run


def _artifact(ledger: Path, record: str, head: str = SHA, status: str = "ci-verified") -> None:
    body = {"status": status, "head": head, "base": "main", "run": RUN, "tree": "b" * 40}
    draft = events.Draft(
        record, events.KIND_ARTIFACT, {"artifact": "landing-verification", "body": body}
    )
    events.append(ledger, [draft], actor="engine")


def _task(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> tuple[Path, str]:
    ledger = tmp_path / "ledger"
    return ledger, _create(capsys, ledger, *SHAPED, "--field", "issue_type=task")


def _close(capsys: pytest.CaptureFixture[str], ledger: Path, record: str, tests: str = TESTS):
    return _run(
        capsys,
        "close",
        str(ledger),
        record,
        "--reason",
        "landed",
        "--landed",
        SHA,
        "--tests",
        tests,
    )


def test_a_task_closes_in_one_call_with_landing_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(landing, "_git", _git())
    ledger, record = _task(capsys, tmp_path)
    _artifact(ledger, record)

    closed = _close(capsys, ledger, record)

    assert "refused" not in closed, closed
    assert _run(capsys, "show", str(ledger), record)["status"] == "closed"


@pytest.mark.parametrize(
    "case",
    [
        ("b" * 40, "ci-verified", ("t.py",), "no landing-verification artifact"),
        (SHA, "unverified", ("t.py",), "no landing-verification artifact"),
        (SHA, "ci-verified", (), "is not in commit"),
    ],
)
def test_a_close_with_a_wrong_ci_head_or_a_missing_test_file_is_refused(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str, tuple[str, ...], str],
) -> None:
    head, status, files, said = case
    monkeypatch.setattr(landing, "_git", _git(files))
    ledger, record = _task(capsys, tmp_path)
    _artifact(ledger, record, head=head, status=status)

    closed = _close(capsys, ledger, record)

    assert said in closed["refused"], closed
    assert _run(capsys, "show", str(ledger), record)["status"] != "closed"


def test_a_comment_that_copies_the_artifact_does_not_satisfy_the_close(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(landing, "_git", _git())
    ledger, record = _task(capsys, tmp_path)
    copied = json.dumps({
        "landing-verification": {"status": "ci-verified", "head": SHA, "run": RUN}
    })
    _run(capsys, "comment", str(ledger), record, copied)

    closed = _close(capsys, ledger, record)

    assert "no landing-verification artifact" in closed["refused"], closed


def test_a_task_without_evidence_still_owes_its_confirmation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger, record = _task(capsys, tmp_path)

    closed = _run(capsys, "close", str(ledger), record, "--reason", "done")

    assert "cannot complete" in closed["refused"], closed


def test_an_untested_criterion_opens_a_follow_up_that_holds_the_parent_open(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(landing, "_git", _git())
    ledger, record = _task(capsys, tmp_path)
    epic = _create(capsys, ledger, "--field", "issue_type=epic")
    _run(capsys, "dep", str(ledger), record, epic, "--type", "parent-child")
    _artifact(ledger, record)

    closed = _close(capsys, ledger, record, json.dumps({"it is reviewed": None}))
    assert "events" in closed, closed
    followups = sorted({e.split("#")[0] for e in closed["events"]} - {record})
    held = _run(capsys, "close", str(ledger), epic, "--reason", "done", "--resolution", "cancelled")

    assert followups, closed
    assert _run(capsys, "show", str(ledger), followups[0])["fields"]["followup_of"] == record
    assert "open follow-up" in held["refused"], held


def test_a_task_starts_without_a_review_but_not_without_criteria(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_AGENT", "owner")
    ledger, record = _task(capsys, tmp_path)
    bare = _create(capsys, ledger, "--description", TRIGGER, "--field", "issue_type=task")

    started = _run(capsys, "claim", str(ledger), record, "--to", "sam")
    refused = _run(capsys, "claim", str(ledger), bare, "--to", "sam")

    assert "refused" not in started, started
    assert "refused" in refused, refused


def test_superseded_by_names_an_existing_record_or_tag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(landing, "_git", _git())
    ledger, record = _task(capsys, tmp_path)
    _, other = _task(capsys, tmp_path)

    missing = _run(
        capsys, "close", str(ledger), record, "--reason", "old", "--superseded-by", "v9.9.9"
    )
    by_tag = _run(
        capsys, "close", str(ledger), record, "--reason", "old", "--superseded-by", "v1.0.0"
    )
    by_record = _run(
        capsys, "close", str(ledger), other, "--reason", "old", "--superseded-by", record
    )

    assert "names no record and no tag" in missing["refused"], missing
    assert "refused" not in by_tag, by_tag
    assert "refused" not in by_record, by_record
    assert _run(capsys, "show", str(ledger), record)["fields"]["superseded_by"] == "v1.0.0"
