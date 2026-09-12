from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import classify, tracker
from basicly.config import WORK_TYPES
from tests import fake_tracker, flipped_tracker


class _Proc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class _FakeBr:
    def __init__(
        self, *, acceptance_criteria: str | None = None, description: str | None = None
    ) -> None:
        self.acceptance_criteria = acceptance_criteria
        self.description = description
        self.recorded_type: str | None = None
        self.calls: list[list[str]] = []
        self.comments: list[str] = []

    def read_comments(self, _repo_root: Path, _issue_id: str) -> list[dict]:
        return [{"text": text} for text in self.comments]

    def add_comment(self, _repo_root: Path, _issue_id: str, body: str) -> None:
        self.comments.append(body)

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        self.calls.append(args)
        if args[:1] == ["update"]:
            self.recorded_type = args[args.index("-t") + 1]
            return _Proc("")
        if args[:1] == ["show"]:
            return _Proc(
                json.dumps([
                    {
                        "acceptance_criteria": self.acceptance_criteria,
                        "description": self.description,
                    }
                ])
            )
        raise AssertionError(f"unexpected br call: {args}")


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeBr) -> None:
    monkeypatch.setattr(classify, "_write", fake)
    fake_tracker.install(monkeypatch, fake)
    monkeypatch.setattr(classify, "_read_comments", fake.read_comments)
    monkeypatch.setattr(classify, "_add_comment", fake.add_comment)


@pytest.mark.parametrize("work_type", WORK_TYPES)
def test_classify_records_each_valid_type(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, work_type: str
) -> None:
    fake = _FakeBr()
    _install(monkeypatch, fake)
    result = classify.classify(tmp_path, "i", work_type)
    assert result.work_type == work_type
    assert fake.recorded_type == work_type


def test_classify_rejects_unknown_type_before_touching_br(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr()
    _install(monkeypatch, fake)
    with pytest.raises(ValueError, match="unknown work type"):
        classify.classify(tmp_path, "i", "story")
    assert fake.recorded_type is None
    assert fake.calls == []


_TRIGGER = "## Trigger\n\nWhen gated, I want a trigger, so I can validate it.\n\n"


def test_classify_reports_ready_dor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:

    _install(
        monkeypatch,
        _FakeBr(
            acceptance_criteria="given x then y",
            description=_TRIGGER,
        ),
    )
    result = classify.classify(tmp_path, "i", "feature")
    assert result.dor.ready is True
    assert result.can_leave_classify is True


def test_classify_reports_not_ready_dor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install(monkeypatch, _FakeBr())
    result = classify.classify(tmp_path, "i", "feature")
    assert result.work_type == "feature"
    assert result.can_leave_classify is False
    assert result.dor.missing == ("## Trigger", "## Acceptance Criteria")


def test_classify_assigns_and_records_the_integrity_level(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake = _FakeBr()
    _install(monkeypatch, fake)
    result = classify.classify(tmp_path, "i", "task", ("src/basicly/cli.py",))
    assert result.level == "L3"
    assert len(fake.comments) == 1
    body = fake.comments[0]
    assert body.startswith(f"{classify.CLASSIFICATION_MARKER} level=L3 rule=cli-surface")
    assert "gates=full,validate-as-consumer,evidence-binding" in body
    assert "tier=maximum" in body
    assert "rework=2" in body
    assert "ship=human" in body


def test_classify_records_the_classification_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr()
    _install(monkeypatch, fake)
    classify.classify(tmp_path, "i", "task", ("src/basicly/loop.py",))
    classify.classify(tmp_path, "i", "task", ("src/basicly/loop.py",))
    assert len(fake.comments) == 1


def test_classify_without_a_scope_still_assigns_a_level(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr()
    _install(monkeypatch, fake)
    result = classify.classify(tmp_path, "i", "task")
    assert result.level == "L2"
    assert "reason=no scope declared" in fake.comments[0]


def test_the_type_and_the_marker_land_in_the_owned_ledger_with_br_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = flipped_tracker.flipped_repo(tmp_path)
    flipped_tracker.seed(
        repo, "seam-1", description=_TRIGGER + "## Acceptance Criteria\n\n- given x\n"
    )
    flipped_tracker.refuse_spawn(monkeypatch)

    result = classify.classify(repo, "seam-1", "task", ("src/basicly/policy.py",))

    record = tracker.read_record(repo, "seam-1")
    assert record is not None
    assert record["issue_type"] == "task"
    assert result.dor.ready is True
    marker = tracker.read_comments(repo, "seam-1")[0]["text"]
    assert marker.startswith(f"{classify.CLASSIFICATION_MARKER} level=L2")


def test_the_dor_verdict_comes_out_of_the_owned_record_with_br_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    repo = flipped_tracker.flipped_repo(tmp_path)
    for bead in ("ready-1", "bug-1"):
        flipped_tracker.seed(
            repo, bead, description=_TRIGGER + "## Acceptance Criteria\n\n- given x\n"
        )
    flipped_tracker.refuse_spawn(monkeypatch)

    assert classify.classify(repo, "ready-1", "task").dor.ready is True
    verdict = classify.classify(repo, "bug-1", "bug").dor
    assert verdict.ready is False
    assert verdict.missing == ("## Steps to Reproduce",)
