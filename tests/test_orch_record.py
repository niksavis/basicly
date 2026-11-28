from __future__ import annotations

import importlib
import json
import shlex
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / ".basicly" / "core" / "kit" / "orch"))

log = importlib.import_module("orch_lessons.log")
messages = importlib.import_module("orch_relay.messages")
record = importlib.import_module("orch_relay.record")
start = importlib.import_module("orch_relay.start")
fields = importlib.import_module("orch_relay.fields")
accept = importlib.import_module("orch_relay.accept")
cli = importlib.import_module("orch_lessons.cli")

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


@pytest.mark.parametrize(
    ("field", "text"),
    [("action", "uv run pytest -q"), ("proof", "uv run basicly verify --mode full")],
)
def test_decide_refuses_an_action_or_proof_that_names_a_local_test_set(
    repo: Path, field: str, text: str
) -> None:
    fake = FakeTracker([])
    bad = (
        messages.Action("ci-edit", "x-0", "app", text, []) if field == "action" else act("ci-edit")
    )
    why = decision(proof=text) if field == "proof" else decision()

    with pytest.raises(log.RefusedError, match=rf"refuses --{field}: it names a local test set"):
        writer(repo, fake).decide(bad, why)

    assert fake.comments == []


@pytest.mark.parametrize("command", ["decide", "approve"])
def test_risk_without_a_check_line_in_the_action_is_refused_by_name(
    repo: Path, command: str
) -> None:
    fake = FakeTracker([])
    item = "ci-edit" if command == "decide" else "deploy"
    bad = act(item, ["red CI => gh run view 1"])
    with pytest.raises(log.RefusedError, match=r"risk 'red CI' maps to no check line"):
        if command == "decide":
            writer(repo, fake).decide(bad, decision())
        else:
            writer(repo, fake).approve(bad, "push it")
    assert fake.comments == []


def test_report_says_it_only_records_and_names_the_send_step() -> None:
    step = messages.send_step("x-1")
    assert "recorded on x-1 only" in step
    assert "SendMessage" in step


def test_send_order_refuses_an_order_without_goal_limits_and_proof() -> None:
    with pytest.raises(log.RefusedError, match=r"--goal, --limits, --proof"):
        messages.order("app", "x-1", "", "", "")


def test_send_order_refuses_a_placeholder() -> None:
    with pytest.raises(log.RefusedError, match=r"placeholder '<NAME>'"):
        messages.order("app", "x-1", "ship <NAME>", "no push", "tests pass")


@pytest.mark.parametrize("state", ["working", "done", "blocked", "needs-decision", "progress"])
def test_report_accepts_only_the_report_states_with_a_record(repo: Path, state: str) -> None:
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
        ("deploy", None),
        ("push", "an ask-first item"),
        ("ci-edit", "an ask-first item"),
        ("gate-bypass", None),
        ("hook-weakening", None),
        ("gate-exception", None),
        ("rule-change", None),
        ("hook-change", "unknown"),
        ("irreversible-destruction", "stays with the person in the worker's own window"),
        ("classifier-refusal", "stays with the person in the worker's own window"),
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


@pytest.mark.parametrize(
    ("diff", "limit"),
    [
        (True, "Good for this exact diff until it lands; any change to the diff voids it."),
        (False, "Good for one use, within 60 minutes of"),
    ],
)
def test_approve_states_the_time_limit_for_a_diff_and_for_a_command(
    repo: Path, diff: bool, limit: str
) -> None:
    text = writer(repo, FakeTracker([])).approve(act("rule-change"), "go", diff)

    assert limit in text


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


@pytest.mark.parametrize("item", ["test-weakening", "gate-bypass", "hook-weakening"])
def test_decide_takes_a_gate_change_only_with_its_council_run(repo: Path, item: str) -> None:
    fake = FakeTracker([])
    with pytest.raises(log.RefusedError, match=r"without --council"):
        writer(repo, fake).decide(act(item), decision())
    assert fake.comments == []

    text = writer(repo, fake).decide(act(item), decision(council="run-7: 3 of 3 for"))

    assert "Council run: run-7: 3 of 3 for" in text
    assert fake.comments == [("x-0", text)]


@pytest.mark.parametrize("command", ["orch decide", "orch approve"])
def test_a_risk_refusal_names_every_problem_at_once_and_its_example_passes(
    repo: Path, command: str
) -> None:
    with pytest.raises(log.RefusedError) as refused:
        fields.risks(["red CI", "slow CI => gh run view 1"], ACTION, command)
    said = str(refused.value)
    assert said.startswith(f"{command} refuses --risk: ")
    assert "risk 'red CI' names no check" in said
    assert "risk 'slow CI' maps to no check line of the action" in said
    argv = shlex.split(said.split("Example that passes: ", 1)[1])
    assert argv[:2] == command.split()
    fake = FakeTracker(["orchestration-record: goal"])
    text = cli.relay(cli.parser().parse_args(argv[1:]), writer(repo, fake))
    assert f"- risk: red CI; check: {fields.RISK_EXAMPLE_CHECK}" in text
    assert fake.comments == [("x-0", text)]


def test_send_order_says_it_printed_the_order_and_sent_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["send", "--order", "--to", "app", "--record", "x-1"]
    argv += ["--goal", "ship", "--limits", "no push", "--proof", "tests pass"]
    assert cli.run(argv, tmp_path, {}) == 0
    first, *rest = capsys.readouterr().out.splitlines()
    assert first.startswith("printed, not sent: ") and "SendMessage" in first
    assert rest[0] == "Order to app on x-1."


def test_a_decision_on_a_project_record_is_accepted_by_that_project(repo: Path) -> None:
    fake = FakeTracker([])
    on_project = messages.Action("ci-edit", "x-0", "app", ACTION, [], "app-7")
    text = writer(repo, fake).decide(on_project, decision())
    assert "Record: x-0\nProject record: app-7\n" in text
    assert fake.comments == [("x-0", text)]
    said = accept.check(text, "app-7", "app", ACTION, lambda: T0)
    assert said.startswith("accepted decision for app-7 in app")
    with pytest.raises(log.RefusedError, match=r"names record 'x-0', not 'app-8'"):
        accept.check(text, "app-8", "app", ACTION, lambda: T0)


def test_a_record_the_workspace_ledger_lacks_names_the_project_record_flag(repo: Path) -> None:
    def refuse(_argv: list[str]) -> str:
        raise log.RefusedError("the tracker refused: the ledger holds no record app-7")

    with pytest.raises(log.RefusedError, match=r"with --project-record$"):
        record.Tracker(repo, refuse).comment("app-7", "text")


def test_start_requires_a_role() -> None:
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["start", "--vendor", "claude", "--name", "app-x"])


def _adapter(tmp_path: Path) -> Path:
    root = tmp_path / ".orchestrator"
    (root / start.ADAPTER).parent.mkdir(parents=True)
    (root / start.ADAPTER).write_text("", encoding="utf-8")
    return root


def test_start_refuses_a_second_orchestrator_while_one_is_live(tmp_path: Path) -> None:
    ask = start.Request("claude", "orchestrator", "Orchestrator", tmp_path)
    with pytest.raises(log.RefusedError, match=r"a live Orchestrator session holds that role"):
        start.plan(ask, tmp_path, _adapter(tmp_path), ["0123456789"])


def test_start_says_a_role_other_than_worker_is_printed_not_started(tmp_path: Path) -> None:
    ask = start.Request("claude", "orchestrator", "Orchestrator", tmp_path)
    found = start.plan(ask, tmp_path, _adapter(tmp_path), [])
    assert not found.run and found.note.startswith("printed, not started: ")
    assert found.argv == ["claude", "--agent", "orchestrator", "-n", "Orchestrator"]


def test_start_runs_a_worker_only_through_the_session_adapter(tmp_path: Path) -> None:
    root = _adapter(tmp_path)
    ask = start.Request("codex", "worker", "codex-app-fix", tmp_path / "app", "p.md")
    found = start.plan(ask, tmp_path, root, [])
    assert found.run and found.argv == [
        str(root / start.ADAPTER),
        "start",
        "codex-app-fix",
        "--dir",
        str(tmp_path / "app"),
        "--codex",
        "--prompt",
        "p.md",
    ]
    with pytest.raises(log.RefusedError, match=r"no session adapter at"):
        start.plan(ask, tmp_path, None, [])
    no_dir = start.Request("claude", "worker", "app-fix", None)
    with pytest.raises(log.RefusedError, match=r"refuses --role worker without --dir"):
        start.plan(no_dir, tmp_path, root, [])
    copilot = start.Request("copilot", "worker", "app-fix", tmp_path)
    with pytest.raises(log.RefusedError, match=r"refuses --vendor copilot for a worker"):
        start.plan(copilot, tmp_path, root, [])


def test_orch_start_runs_the_session_adapter_and_passes_its_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".git").mkdir()
    root = _adapter(tmp_path)
    adapter = root / start.ADAPTER
    adapter.write_text('#!/bin/sh\necho "adapter $*"\nexit 3\n', encoding="utf-8")
    adapter.chmod(0o755)
    argv = ["start", "--vendor", "claude", "--role", "worker", "--name", "app-fix"]
    argv += ["--dir", str(tmp_path / "app")]
    environ = {"ORCH_STATE_ROOT": str(root)}
    assert cli.run(argv, tmp_path, environ) == 3
    assert capsys.readouterr().out == f"adapter start app-fix --dir {tmp_path / 'app'}\n"


def test_orch_start_refuses_by_name_when_the_session_adapter_hangs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()
    root = _adapter(tmp_path)
    adapter = root / start.ADAPTER
    adapter.write_text("#!/bin/sh\nsleep 5\n", encoding="utf-8")
    adapter.chmod(0o755)
    monkeypatch.setattr(cli, "START_TIMEOUT", 0.2)
    argv = ["start", "--vendor", "claude", "--role", "worker", "--name", "app-fix"]
    argv += ["--dir", str(tmp_path)]
    with pytest.raises(log.RefusedError, match=r"did not finish within 0.2 s"):
        cli.run(argv, tmp_path, {"ORCH_STATE_ROOT": str(root)})


@pytest.mark.parametrize("item", ["gate-exception", "gate-bypass", "hook-weakening"])
def test_the_persons_own_words_approve_a_gate_change_for_one_use_only(
    repo: Path, item: str
) -> None:
    fake = FakeTracker([])
    text = writer(repo, fake).approve(act(item), "Allow it once")
    assert '"Allow it once"' in text and "Good for one use, within 60 minutes of" in text
    with pytest.raises(log.RefusedError, match=r"refuses --diff for"):
        writer(repo, fake).approve(act(item), "Allow it once", True)
    with pytest.raises(log.RefusedError, match=r"without --council"):
        writer(repo, fake).decide(act(item), decision())
    assert fake.comments == [("x-0", text)]


def test_a_refused_approve_names_the_command_that_takes_the_item(repo: Path) -> None:
    with pytest.raises(log.RefusedError, match=r"decides it with orch decide$"):
        writer(repo, FakeTracker([])).approve(act("push"), "go")
