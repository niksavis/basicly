from __future__ import annotations

import importlib
from datetime import timedelta
from pathlib import Path

import pytest

from tests.test_orch_record import ACTION, T0, FakeTracker, act, decision, repo, writer

__all__ = ["repo"]

accept = importlib.import_module("orch_relay.accept")
log = importlib.import_module("orch_lessons.log")


def _relay(repo: Path) -> str:
    return writer(repo, FakeTracker([])).decide(act("ci-edit"), decision())


def _at(minutes: int):
    return lambda: T0 + timedelta(minutes=minutes)


def test_a_relay_from_orch_decide_is_accepted_for_its_exact_action(repo: Path) -> None:
    shown = accept.check(_relay(repo), "x-0", "app", ACTION, _at(10))

    assert shown.startswith("accepted decision for x-0 in app")
    assert "until 2026-11-19T10:00" in shown


@pytest.mark.parametrize(
    "case",
    [
        ("x-0", "app", ACTION.replace("v1.2.0", "v1.2.1"), 10, "exact action"),
        ("x-0", "other", ACTION, 10, "target repository"),
        ("x-9", "app", ACTION, 10, "record"),
        ("x-0", "app", ACTION, 61, "expired"),
    ],
)
def test_a_relay_that_differs_or_expired_is_refused_by_name(
    repo: Path, case: tuple[str, str, str, int, str]
) -> None:
    record, target, action, minutes, why = case
    with pytest.raises(log.RefusedError, match=why):
        accept.check(_relay(repo), record, target, action, _at(minutes))


def test_a_relay_without_its_entry_stamp_is_refused(repo: Path) -> None:
    text = "\n".join(
        line for line in _relay(repo).splitlines() if not line.startswith("Entry stamp:")
    )

    with pytest.raises(log.RefusedError, match="entry stamp"):
        accept.check(text, "x-0", "app", ACTION, _at(10))


def test_a_text_that_is_no_relay_is_refused() -> None:
    with pytest.raises(log.RefusedError, match="not a relayed decision"):
        accept.check("go ahead", "x-0", "app", ACTION, _at(0))
