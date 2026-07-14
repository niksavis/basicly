"""Tests for the classify step (onb.6.2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import classify, policy
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

    def __init__(self, *, lint_missing: list[str] | None = None) -> None:
        self.lint_missing = lint_missing or []
        self.recorded_type: str | None = None
        self.calls: list[list[str]] = []

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        self.calls.append(args)
        if args[:1] == ["update"]:
            self.recorded_type = args[args.index("-t") + 1]
            return _Proc("")
        if args[:1] == ["lint"]:
            return _Proc(json.dumps({"results": [{"missing": self.lint_missing}]}))
        raise AssertionError(f"unexpected br call: {args}")


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeBr) -> None:
    monkeypatch.setattr(classify, "_run_br", fake)
    monkeypatch.setattr(policy, "_run_br", fake)


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
    """A ready issue can leave classify (DoR satisfied)."""
    _install(monkeypatch, _FakeBr(lint_missing=[]))
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
