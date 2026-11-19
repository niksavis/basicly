from __future__ import annotations

import importlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "packages" / "basicly-orch"))

log = importlib.import_module("orch_lessons.log")
messages = importlib.import_module("orch_relay.messages")
record = importlib.import_module("orch_relay.record")
start = importlib.import_module("orch_relay.start")

T0 = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
ACTION = "git push origin v1.2.0\ngh run list --commit abc123"
CHECK = "gh run list --commit abc123"


class FakeTracker:
    def __init__(self, titles: list[str]) -> None:
        self.titles = titles
        self.comments: list[tuple[str, str]] = []

    def __call__(self, argv: list[str]) -> str:
        if "list" in argv:
            records = [
                {"record": f"x-{n}", "fields": {"title": title}}
                for n, title in enumerate(self.titles)
            ]
            return json.dumps({"records": records})
        self.comments.append((argv[-2], argv[-1]))
        return ""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    cli = tmp_path / record.TRACKER_CLI
    cli.parent.mkdir(parents=True)
    cli.write_text("", encoding="utf-8")
    return tmp_path


def writer(repo: Path, fake: FakeTracker) -> Any:
    return messages.Writer(record.Tracker(repo, fake), lambda _: None, lambda: T0)


def act(item: str, risks: list[str] | None = None) -> Any:
    return messages.Action(item, "x-0", "app", ACTION, risks or [])


def decision(**changes: str) -> Any:
    values = {
        "rule": "The orchestrator decides ask-first items.",
        "rule_date": "2026-11-18",
        "reason": "r",
        "condition": "c",
        "proof": "p",
        "if_proof_fails": "f",
    }
    return messages.Decision(**{**values, **changes})


@pytest.mark.parametrize(
    ("titles", "count"),
    [([], 0), (["orchestration-record: a", "orchestration-record: b"], 2)],
)
def test_record_refuses_unless_exactly_one_match_and_names_the_count(
    repo: Path, titles: list[str], count: int
) -> None:
    tracker = record.Tracker(repo, FakeTracker([*titles, "other work"]))
    with pytest.raises(log.RefusedError, match=rf"found {count} active records"):
        record.find(tracker)


def test_record_finds_the_one_active_orchestration_record(repo: Path) -> None:
    tracker = record.Tracker(repo, FakeTracker(["other", "orchestration-record: goal"]))
    assert record.find(tracker) == "x-1"


@pytest.mark.parametrize("missing", ["reason", "condition", "proof", "if_proof_fails"])
def test_decide_refuses_a_decision_without_each_required_field(repo: Path, missing: str) -> None:
    fake = FakeTracker([])
    with pytest.raises(log.RefusedError, match=missing.replace("_", "-")):
        writer(repo, fake).decide(act("ci-edit"), decision(**{missing: " "}))
    assert fake.comments == []


def test_decide_writes_the_entry_and_returns_the_relayed_decision(repo: Path) -> None:
    fake = FakeTracker([])
    text = writer(repo, fake).decide(act("ci-edit", [f"red CI => {CHECK}"]), decision())
    assert fake.comments == [("x-0", text)]
    assert "(2026-11-18)" in text and "Entry stamp: 2026-11-19T09:00:00+00:00" in text
    assert f"- risk: red CI; check: {CHECK}" in text


@pytest.mark.parametrize("command", ["decide", "approve"])
def test_risk_without_a_check_line_in_the_action_is_refused_by_name(
    repo: Path, command: str
) -> None:
    fake = FakeTracker([])
    item = "ci-edit" if command == "decide" else "push"
    bad = act(item, ["red CI => gh run view 1"])
    with pytest.raises(log.RefusedError, match=r"risk 'red CI' maps to no check line"):
        if command == "decide":
            writer(repo, fake).decide(bad, decision())
        else:
            writer(repo, fake).approve(bad, "push it")
    assert fake.comments == []


def test_send_order_refuses_an_order_without_goal_limits_and_proof() -> None:
    with pytest.raises(log.RefusedError, match=r"--goal, --limits, --proof"):
        messages.order("app", "x-1", "", "", "")


def test_send_order_refuses_a_placeholder() -> None:
    with pytest.raises(log.RefusedError, match=r"placeholder '<NAME>'"):
        messages.order("app", "x-1", "ship <NAME>", "no push", "tests pass")


@pytest.mark.parametrize("state", ["done", "blocked", "needs-decision", "progress"])
def test_report_accepts_only_three_states_with_a_record(repo: Path, state: str) -> None:
    fake = FakeTracker([])
    if state == "progress":
        with pytest.raises(log.RefusedError, match=r"refuses state 'progress'"):
            writer(repo, fake).report("x-1", state, "s", "go")
        return
    writer(repo, fake).report("x-1", state, "s", "go")
    assert fake.comments[0][0] == "x-1"
    with pytest.raises(log.RefusedError, match=r"--record"):
        writer(repo, fake).report("", state, "s", "go")


@pytest.mark.parametrize(
    ("item", "refusal"),
    [
        ("push", None),
        ("ci-edit", "an ask-first item"),
        ("gate-bypass", "stays with the person in the worker's own window"),
    ],
)
def test_approve_accepts_only_the_class_the_person_approves_through_the_orchestrator(
    repo: Path, item: str, refusal: str | None
) -> None:
    fake = FakeTracker([])
    if refusal is None:
        text = writer(repo, fake).approve(act(item), "push it")
        assert '"push it"' in text and fake.comments == [("x-0", text)]
        return
    with pytest.raises(log.RefusedError, match=refusal):
        writer(repo, fake).approve(act(item), "push it")
    assert fake.comments == []


def test_start_builds_the_launch_command_for_each_vendor(tmp_path: Path) -> None:
    role = tmp_path / start.ROLE_FILES / "orchestrator.md"
    role.parent.mkdir(parents=True)
    role.write_text('Say "hi"\\n\nnext', encoding="utf-8")
    assert start.launch("claude", "orchestrator", "Orchestrator", tmp_path) == [
        "claude",
        "--agent",
        "orchestrator",
        "-n",
        "Orchestrator",
    ]
    assert start.launch("copilot", "orchestrator", "o", tmp_path) == [
        "copilot",
        "--agent",
        "orchestrator",
    ]
    codex = start.launch("codex", "orchestrator", "o", tmp_path)
    key, _, value = codex[2].partition("=")
    assert codex[:2] == ["codex", "-c"] and key == "developer_instructions"
    assert json.loads(value) == role.read_text(encoding="utf-8")
