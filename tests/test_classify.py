"""Tests for the classify step (onb.6.2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import br, classify, policy
from basicly.config import WORK_TYPES


class _Proc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class _FakeBr:
    """Stand-in for br, routed by subcommand.

    Records the type written by ``br update -t`` and answers ``br lint`` from a
    configurable missing-sections list, so classify (which delegates the DoR
    read to the policy engine) resolves entirely against this fake.
    """

    def __init__(
        self, *, lint_missing: list[str] | None = None, acceptance_criteria: str | None = None
    ) -> None:
        self.lint_missing = lint_missing or []
        self.acceptance_criteria = acceptance_criteria
        self.recorded_type: str | None = None
        self.calls: list[list[str]] = []
        self.comments: list[str] = []

    def read_comments(self, _repo_root: Path, _issue_id: str) -> list[dict]:
        """Stands in for ``br.read_comments`` — the classification marker's read side."""
        return [{"text": text} for text in self.comments]

    def add_comment(self, _repo_root: Path, _issue_id: str, body: str) -> None:
        """Stands in for ``br.add_comment`` — the classification marker's write side."""
        self.comments.append(body)

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        self.calls.append(args)
        if args[:1] == ["update"]:
            self.recorded_type = args[args.index("-t") + 1]
            return _Proc("")
        if args[:1] == ["lint"]:
            return _Proc(json.dumps({"results": [{"missing": self.lint_missing}]}))
        if args[:1] == ["show"]:
            return _Proc(json.dumps([{"acceptance_criteria": self.acceptance_criteria}]))
        raise AssertionError(f"unexpected br call: {args}")


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeBr) -> None:
    monkeypatch.setattr(classify, "_run_br", fake)
    monkeypatch.setattr(policy, "_run_br", fake)
    # The record read goes through `br.read_record`, the one seam every consumer shares
    # (basicly-tcmy.14), rather than each module's alias.
    monkeypatch.setattr(br, "try_run_br", fake)
    # The `[harness-classification]` marker reads and writes comments; both go
    # through classify's own aliases so the fake answers them the same way.
    monkeypatch.setattr(classify, "_read_comments", fake.read_comments)
    monkeypatch.setattr(classify, "_add_comment", fake.add_comment)


@pytest.mark.parametrize("work_type", WORK_TYPES)
def test_classify_records_each_valid_type(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, work_type: str
) -> None:
    """Every fixed work class is accepted and written with br update -t."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    result = classify.classify(tmp_path, "i", work_type)
    assert result.work_type == work_type
    assert fake.recorded_type == work_type


def test_classify_rejects_unknown_type_before_touching_br(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An out-of-set type raises loudly and never records anything."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    with pytest.raises(ValueError, match="unknown work type"):
        classify.classify(tmp_path, "i", "story")
    assert fake.recorded_type is None
    assert fake.calls == []  # rejected before any br call


def test_classify_reports_ready_dor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A ready issue can leave classify (DoR satisfied).

    The criteria are set explicitly because DoR requires them on every bead
    whatever its work type, so a silent lint alone no longer makes an issue ready
    (basicly-kjc5.36).
    """
    _install(monkeypatch, _FakeBr(lint_missing=[], acceptance_criteria="given x then y"))
    result = classify.classify(tmp_path, "i", "feature")
    assert result.dor.ready is True
    assert result.can_leave_classify is True


def test_classify_reports_not_ready_dor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A not-ready issue records the type but cannot yet advance to decompose."""
    _install(monkeypatch, _FakeBr(lint_missing=["## Acceptance Criteria"]))
    result = classify.classify(tmp_path, "i", "feature")
    assert result.work_type == "feature"  # type is still recorded
    assert result.can_leave_classify is False
    assert result.dor.missing == ("## Acceptance Criteria",)


def test_classify_assigns_and_records_the_integrity_level(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The level is assigned from the declared scope and persisted as a marker.

    Written as a `[harness-classification]` comment rather than a tracker field:
    the loop's schema is still being replaced, so evidence lands in a format this
    repo owns and that travels with a clone.
    """
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
    """Classify re-runs until its checkpoint is approved; the marker must not stack."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    classify.classify(tmp_path, "i", "task", ("src/basicly/loop.py",))
    classify.classify(tmp_path, "i", "task", ("src/basicly/loop.py",))
    assert len(fake.comments) == 1


def test_classify_without_a_scope_still_assigns_a_level(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A hand-filed bead declares no scope: it resolves, and the record says so."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    result = classify.classify(tmp_path, "i", "task")
    assert result.level == "L2"
    assert "reason=no scope declared" in fake.comments[0]
