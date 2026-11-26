from __future__ import annotations

import argparse
import importlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / ".basicly" / "core" / "kit" / "orch"
sys.path.insert(0, str(PACKAGE))

store = importlib.import_module("orch_state.store")
keepalive = importlib.import_module("orch_state.keepalive")
runner = importlib.import_module("runner")
log = importlib.import_module("orch_lessons.log")

T0 = datetime(2026, 11, 26, 13, 0, tzinfo=UTC)
DRY = argparse.Namespace(dry_run=False)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def workspace(tmp_path: Path) -> Path:
    base = tmp_path / ".orchestrator"
    (base / "sessions").mkdir(parents=True)
    return base


def session(base: Path, key: str, asked: float, **changes: object) -> None:
    changes = {"name": key, "state": "idle", "request_at": at(asked).isoformat(), **changes}
    store.save(base, key, changes, lambda: at(asked))


class Fake:
    name = "fake"

    def __init__(self, panes: dict[str, tuple[str, str]]) -> None:
        self.listed = panes
        self.sent: list[tuple[str, str]] = []

    def panes(self) -> dict[str, tuple[str, str]]:
        return self.listed

    def send(self, target: str, text: str) -> str:
        self.sent.append((target, text))
        return ""


def test_a_needed_session_gets_one_ping_before_55_minutes_and_an_unneeded_one_none(
    tmp_path: Path,
) -> None:
    base = workspace(tmp_path)
    session(base, "worker", 0, after_reset="needed")
    session(base, "spare", 0, after_reset="close")
    fake = Fake({"worker": ("p1", "done"), "spare": ("p2", "done")})

    shown = keepalive.run(DRY, base, {}, at(45), fake)

    assert fake.sent == [("p1", keepalive.TEXT)]
    assert "pinged: worker" in shown
    assert "close after their handover: spare" in shown


@pytest.mark.parametrize(("minutes", "said"), [(20, "not due: worker"), (56, "cache likely cold")])
def test_a_ping_waits_until_40_minutes_and_stops_at_55(
    tmp_path: Path, minutes: float, said: str
) -> None:
    base = workspace(tmp_path)
    session(base, "worker", 0, after_reset="needed")
    fake = Fake({"worker": ("p1", "done")})

    shown = keepalive.run(DRY, base, {}, at(minutes), fake)

    assert fake.sent == []
    assert said in shown


def test_no_session_marked_needed_is_refused_by_name(tmp_path: Path) -> None:
    base = workspace(tmp_path)
    session(base, "spare", 0)

    with pytest.raises(log.RefusedError, match="--after-reset needed"):
        keepalive.run(DRY, base, {}, at(45), Fake({}))


def test_without_an_adapter_the_refusal_names_who_needs_a_ping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = workspace(tmp_path)
    session(base, "worker", 0, after_reset="needed")
    monkeypatch.setattr(keepalive.herdr, "binary", lambda _environ: None)

    with pytest.raises(log.RefusedError, match="cross-session message to: worker"):
        keepalive.run(DRY, base, {}, at(45))


def test_the_herdr_adapter_sends_only_its_fixed_text() -> None:
    calls: list[list[str]] = []

    def attempt(argv: list[str], _timeout: float) -> object:
        calls.append(argv)
        listed = {
            "result": {
                "panes": [
                    {
                        "pane_id": "w:p1",
                        "agent_status": "done",
                        "agent_session": {"value": "worker"},
                    }
                ]
            }
        }
        return runner.Done(0, json.dumps(listed), "")

    adapter = keepalive.Herdr("herdr", attempt)

    assert adapter.panes() == {"worker": ("w:p1", "done")}
    assert adapter.send("w:p1", keepalive.TEXT) == ""
    with pytest.raises(log.RefusedError, match="only its fixed text"):
        adapter.send("w:p1", "approve the release")
    assert calls[-1] == ["herdr", "pane", "run", "w:p1", keepalive.TEXT]
