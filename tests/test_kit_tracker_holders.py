from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from basicly import cli as engine_cli
from basicly import loop, tracker
from basicly.config import PolicyConfig
from basicly.loop_state import NodeState
from basicly.policy import GateStatus
from tests import flipped_tracker

KIT_DIR = Path(__file__).parent.parent / ".basicly" / "core" / "kit" / "tracker"
TRIGGER = "When a team shares a backlog, I want to see who holds a story, so I can avoid it."
SHAPED = ("--description", TRIGGER, "--acceptance", "- it is seen", "--requirements", "- stdlib")


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cli = _load(KIT_DIR / "cli.py", "tracker_cli_holders")
events = cli.events


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any]]:
    capsys.readouterr()
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture
def story(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> tuple[Path, str]:
    ledger = tmp_path / "ledger"
    _, made = _run(capsys, "create", str(ledger), "--prefix", "acme", "--title", "s", *SHAPED)
    return ledger, made["record"]


def test_assign_reserves_an_open_story_without_starting_it(
    story: tuple[Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    ledger, record = story

    assert _run(capsys, "assign", str(ledger), record, "--to", "alex")[0] == cli.EXIT_OK

    _, shown = _run(capsys, "show", str(ledger), record)
    assert shown["status"] == "open"
    assert shown["holder"]["name"] == "alex"
    assert shown["holder"]["since"] == shown["dates"]["assigned"]


def test_a_second_person_is_refused_by_name_unless_they_take_it(
    story: tuple[Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    ledger, record = story
    _run(capsys, "assign", str(ledger), record, "--to", "alex")
    before = events.read_events(ledger)[0]

    code, refused = _run(capsys, "claim", str(ledger), record, "--to", "sam")

    assert code == cli.EXIT_REFUSED
    assert "held by alex" in refused["refused"]
    assert events.read_events(ledger)[0] == before
    assert _run(capsys, "claim", str(ledger), record, "--to", "sam", "--take")[0] == cli.EXIT_OK
    _, shown = _run(capsys, "show", str(ledger), record)
    assert (shown["status"], shown["holder"]["name"]) == ("in_progress", "sam")
    assert shown["holder"]["contested"] == []


def test_unassign_frees_the_story_and_ready_names_holders(
    story: tuple[Path, str], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger, record = story
    _run(capsys, "assign", str(ledger), record, "--to", "alex")

    _, ready = _run(capsys, "ready", str(ledger))
    assert ready["records"][0]["holder"]["name"] == "alex"
    monkeypatch.setenv("GIT_AUTHOR_NAME", "alex")
    assert _run(capsys, "ready", str(ledger), "--mine")[1]["count"] == 1
    monkeypatch.setenv("GIT_AUTHOR_NAME", "sam")
    assert _run(capsys, "ready", str(ledger), "--mine")[1]["count"] == 0

    _run(capsys, "unassign", str(ledger), record)
    assert _run(capsys, "show", str(ledger), record)[1]["holder"] is None


def test_a_holder_name_comes_from_the_git_config_and_its_absence_is_refused(
    story: tuple[Path, str], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger, record = story
    home = ledger.parent / "home"
    home.mkdir()
    for variable in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(ledger.parent)

    code, refused = _run(capsys, "assign", str(ledger), record)
    assert code == cli.EXIT_REFUSED
    assert "--to" in refused["refused"]

    (home / ".gitconfig").write_text("[user]\n\tname = Alex Doe\n", encoding="utf-8")
    assert _run(capsys, "assign", str(ledger), record)[0] == cli.EXIT_OK
    assert _run(capsys, "show", str(ledger), record)[1]["holder"]["name"] == "Alex Doe"


def _hold_event(record: str, seq: int, holder: str, ts: str) -> Any:
    return events.Event(
        id=f"{record}#ev-{holder}{seq}",
        record=record,
        seq=seq,
        kind=events.KIND_FIELD,
        actor="operator",
        ts=ts,
        payload={"name": "assignee", "value": holder},
    )


def test_two_holders_from_two_branches_are_contested_not_silently_merged() -> None:
    created = events.Event(
        id="acme-c1#ev-c",
        record="acme-c1",
        seq=1,
        kind=events.KIND_CREATED,
        actor="operator",
        ts="2026-09-01T10:00:00Z",
        payload={"title": "t"},
    )
    alex = _hold_event("acme-c1", 2, "alex", "2026-09-01T10:01:00Z")
    sam = _hold_event("acme-c1", 2, "sam", "2026-09-01T10:02:00Z")

    state = events.fold([created, alex, sam]).records["acme-c1"]

    assert state.contested == ["alex", "sam"]


def test_a_reservation_goes_stale_against_the_ledger_not_the_clock() -> None:
    holders = cli.commands.holders
    created = events.Event(
        id="acme-s1#ev-c",
        record="acme-s1",
        seq=1,
        kind=events.KIND_CREATED,
        actor="operator",
        ts="2026-09-01T10:00:00Z",
        payload={"title": "t"},
    )
    held = _hold_event("acme-s1", 2, "alex", "2026-09-01T10:01:00Z")
    state = events.fold([created, held]).records["acme-s1"]

    assert holders.holding(state, 14, "2026-09-10T00:00:00Z")["stale"] is False
    assert holders.holding(state, 14, "2026-09-20T00:00:00Z")["stale"] is True


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = flipped_tracker.flipped_repo(tmp_path)
    (root / "basicly.toml").write_text(
        '[tracker]\nmode = "owned"\nprefix = "th"\n', encoding="utf-8"
    )
    flipped_tracker.seed(
        root,
        "th-1",
        title="the root",
        assignee="alex",
        description=TRIGGER,
        acceptance_criteria="- it is seen",
        requirements="- stdlib",
    )
    monkeypatch.chdir(root)
    return root


@pytest.mark.usefixtures("repo")
def test_the_engine_route_refuses_to_reassign_a_held_story(
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = ["tracker", "write", "--", "update", "th-1", "--assignee", "sam"]

    assert engine_cli.main(argv) != 0
    assert "held by alex" in capsys.readouterr().err


def test_the_engine_ranking_skips_a_story_someone_else_holds(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_AUTHOR_NAME", "sam")
    assert "th-1" not in {
        row["issue"]["id"] for row in tracker.owned_ranking(repo)["recommendations"]
    }

    monkeypatch.setenv("GIT_AUTHOR_NAME", "alex")
    assert "th-1" in {row["issue"]["id"] for row in tracker.owned_ranking(repo)["recommendations"]}


def _ctx(repo: Path, record: str) -> Any:
    state = NodeState(
        issue_id=record,
        status="open",
        issue_type="task",
        phase="classify",
        worktree=None,
        gates=GateStatus(False, (), (), (), (), ()),
        checkpoints=(),
        rework={},
        has_children=False,
    )
    config = PolicyConfig(required_gates=("verify",), max_rework=2)
    return loop._Ctx(repo, record, state, config, loop.Inputs())


def test_the_loop_stops_on_a_story_someone_else_holds(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_AUTHOR_NAME", "sam")

    held = loop._hold_for_this_session(_ctx(repo, "th-1"))

    assert held is not None and held.blocked
    assert "held by alex" in held.detail
    assert "--take" in held.detail


def test_the_loop_records_the_person_who_runs_it_as_the_holder(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flipped_tracker.seed(repo, "th-2", title="a free story")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "sam")

    assert loop._hold_for_this_session(_ctx(repo, "th-2")) is None
    assert (tracker.read_record(repo, "th-2") or {})["assignee"] == "sam"


def test_resolve_keeps_the_current_value_of_a_status_conflict_and_fsck_is_clean(
    story: tuple[Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    ledger, record = story
    _run(capsys, "update", str(ledger), record, "--status", "in_progress")
    log = sorted(ledger.glob("*.jsonl"))[-1]
    lines = log.read_text(encoding="utf-8").splitlines()
    collided = json.loads(lines[-1])
    collided["seq"] -= 1
    log.write_text("\n".join([*lines[:-1], json.dumps(collided)]) + "\n", encoding="utf-8")
    assert _run(capsys, "fsck", str(ledger))[1]["exit_code"] == 2
    assert _run(capsys, "show", str(ledger), record)[1]["conflicts"][0]["key"] == "status"

    assert _run(capsys, "resolve", str(ledger), record)[0] == cli.EXIT_OK

    _, report = _run(capsys, "fsck", str(ledger))
    assert report["exit_code"] == 0
    assert _run(capsys, "show", str(ledger), record)[1]["conflicts"] == []
    code, refused = _run(capsys, "resolve", str(ledger), record)
    assert code == cli.EXIT_REFUSED
    assert "no unresolved conflict" in refused["refused"]


def test_moving_a_story_nobody_holds_to_in_progress_names_the_claimant(
    story: tuple[Path, str], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger, record = story
    monkeypatch.setenv("GIT_AUTHOR_NAME", "sam")

    assert _run(capsys, "update", str(ledger), record, "--status", "in_progress")[0] == cli.EXIT_OK

    _, shown = _run(capsys, "show", str(ledger), record)
    assert (shown["status"], shown["holder"]["name"]) == ("in_progress", "sam")


def test_moving_a_held_story_to_in_progress_keeps_its_holder(
    story: tuple[Path, str], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger, record = story
    _run(capsys, "assign", str(ledger), record, "--to", "alex")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "sam")

    assert _run(capsys, "update", str(ledger), record, "--status", "in_progress")[0] == cli.EXIT_OK

    _, shown = _run(capsys, "show", str(ledger), record)
    assert (shown["status"], shown["holder"]["name"]) == ("in_progress", "alex")


def test_a_chosen_holder_name_wins_over_the_git_user_name(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    config = "[user]\n\tname = Real Name\n[basicly]\n\tholder = pseudonym\n"
    (home / ".gitconfig").write_text(config, encoding="utf-8")
    holders = cli.commands.holders
    environ = {"HOME": str(home), "GIT_AUTHOR_NAME": "Author Name"}

    assert holders.default_holder(tmp_path, environ) == "pseudonym"
    assert holders.default_holder(tmp_path, {**environ, "BASICLY_HOLDER": "env name"}) == "env name"
    (home / ".gitconfig").write_text("[user]\n\tname = Real Name\n", encoding="utf-8")
    assert holders.default_holder(tmp_path, environ) == "Author Name"
