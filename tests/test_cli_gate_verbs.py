"""Tests for the Hold and Kill gate verbs (basicly-u2hl.3, requirements D3/D15).

Go and Recycle the engine already had. These two were words: every escalation
offered ``park`` and no answer carried it out, and ``kill`` had no surface at all.

What is asserted here is the *effect* an operator gets, not the writes that
produce it — Hold by ``loop_state.is_dispatchable`` refusing the lane's new
status, Kill by the bead being closed only after its worktree is gone and only
behind a code a human relayed. The tracker and the filesystem are faked so those
assertions are about this wiring and nothing else.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from basicly import br, cli, decisions, loop_state, policy, worktree

_ISSUE = "basicly-x1"
_WORKTREE = "basicly-x1-1"
_BRANCH = f"harness/{_WORKTREE}"
_REQUIREMENTS_MD = Path(__file__).resolve().parents[1] / "docs" / "requirements" / "factory-loop.md"


class _Proc:
    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


class _FakeBr:
    """Stateful br stand-in: comment writes are visible to later reads.

    Records the ``update``/``close`` argv verbatim, because the two verbs are
    defined by which of those they issue and a test that only read the comments
    back could not tell a recorded reason from an enforced one.
    """

    def __init__(self) -> None:
        self.comments: list[str] = []
        self.calls: list[list[str]] = []
        self.external_ref: str | None = None

    def __call__(self, _repo_root: Path, args: list[str], **_kwargs: object) -> _Proc:
        self.calls.append(list(args))
        if args[:2] == ["comments", "list"]:
            return _Proc(json.dumps([{"text": text} for text in self.comments]))
        if args[:2] == ["comments", "add"]:
            self.comments.append(args[-1])
            return _Proc("")
        if args[:1] == ["show"]:
            record = {"status": "open", "dependents": [], "external_ref": self.external_ref}
            return _Proc(json.dumps([record]))
        if args[:1] in (["update"], ["close"]):
            return _Proc("")
        raise AssertionError(f"unexpected br call: {args}")

    def argv_for(self, verb: str) -> list[list[str]]:
        """Every recorded call whose first token is *verb*."""
        return [call for call in self.calls if call[:1] == [verb]]


@pytest.fixture
def fake_br(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _FakeBr:
    """A faked tracker, with the process rooted at an empty tmp repo."""
    monkeypatch.chdir(tmp_path)
    fake = _FakeBr()
    monkeypatch.setattr(policy, "_write", fake)
    # Three seams, one stand-in: markers go through br.run_br (basicly-s5li),
    # `br show` through try_run_br behind br.read_record, and policy keeps its own
    # alias. Patching all three means no path can reach a real br.
    monkeypatch.setattr(br, "run_br", fake)
    monkeypatch.setattr(br, "try_run_br", fake)
    return fake


@pytest.fixture
def torn_down(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, bool]]:
    """Record the worktree teardowns a kill performs, without performing any."""
    calls: list[tuple[str, bool]] = []

    def cleanup(name: str, *, force: bool = False, **_kwargs: object) -> None:
        calls.append((name, force))

    monkeypatch.setattr(worktree, "cleanup", cleanup)
    return calls


def _escalate(repo_root: Path, question: str) -> decisions.DecisionItem:
    """Queue one rework escalation on the lane, as the supervisor would."""
    return decisions.enqueue(
        repo_root, _ISSUE, policy.REWORK_ESCALATION_KIND, question, "the lane failed twice"
    )


# --- Hold ---------------------------------------------------------------------


def test_answering_park_defers_the_lane_so_dispatch_refuses_it(
    fake_br: _FakeBr, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An answered ``park`` sets the status the dispatch rule refuses (AC1)."""
    item = _escalate(tmp_path, policy.rework_escalation_question("verify"))

    rc = cli.main(["loop", "answer", item.decision_id, "park - the upstream fix lands next week"])

    assert rc == 0
    assert fake_br.argv_for("update") == [["update", _ISSUE, "--status", policy.HELD_STATUS]]
    # The point of the status, asserted as the property rather than the string:
    # this is the whole mechanism by which the next pass leaves the lane alone.
    assert not loop_state.is_dispatchable(policy.HELD_STATUS)
    assert f"parked {_ISSUE}" in capsys.readouterr().out


def test_parking_records_the_reason_and_the_gate_on_the_bead(
    fake_br: _FakeBr, tmp_path: Path
) -> None:
    """The reason is durable on the bead, or an undispatchable lane has no why (AC1)."""
    item = _escalate(tmp_path, policy.rework_escalation_question("verify"))

    cli.main(["loop", "answer", item.decision_id, "park - waiting on basicly-y2"])

    held = [text for text in fake_br.comments if text.startswith(f"{policy.MARKER} hold")]
    assert held == [f"{policy.MARKER} hold gate=verify park - waiting on basicly-y2"]


def test_parking_works_on_an_escalation_whose_question_names_no_gate(
    fake_br: _FakeBr, tmp_path: Path
) -> None:
    """``park`` is offered by escalations with no gate in them, and means the same.

    ``supervise._capped_dispatch`` raises three question shapes and only one
    carries a gate; keying the verb on the gate would leave the other two offering
    a route that still did nothing.
    """
    item = _escalate(tmp_path, "a landing keeps breaking this lane's merge: re-scope it, or park?")

    rc = cli.main(["loop", "answer", item.decision_id, "park"])

    assert rc == 0
    assert fake_br.argv_for("update") == [["update", _ISSUE, "--status", policy.HELD_STATUS]]
    assert f"{policy.MARKER} hold park" in fake_br.comments


def test_a_delegated_answer_does_not_park_the_lane(
    fake_br: _FakeBr, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A decider cannot park: a deferred child stops holding its parent open."""
    item = _escalate(tmp_path, policy.rework_escalation_question("verify"))

    rc = cli.main([
        "loop",
        "answer",
        item.decision_id,
        "park - not worth another round",
        "--by",
        f"{decisions.DECIDER_BY_PREFIX}juno",
    ])

    assert rc == 0
    assert fake_br.argv_for("update") == []
    # Never silently: the queue reads as disposed, so the answer must say plainly
    # that the lane still holds (basicly-tcmy.6's shape).
    assert f"does not park {_ISSUE}" in capsys.readouterr().out


def test_answering_retry_does_not_park_the_lane(fake_br: _FakeBr, tmp_path: Path) -> None:
    """The two routes an escalation offers must not both fire on one answer."""
    item = _escalate(tmp_path, policy.rework_escalation_question("verify"))

    cli.main(["loop", "answer", item.decision_id, "retry - the gate flake was unrelated"])

    assert fake_br.argv_for("update") == []


# --- Kill ---------------------------------------------------------------------


def test_kill_with_a_relayed_code_tears_the_worktree_down_then_closes(
    fake_br: _FakeBr, torn_down: list[tuple[str, bool]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A confirmed kill removes the worktree, records the reason, and closes (AC2)."""
    fake_br.external_ref = loop_state.format_worktree_ref(_WORKTREE, _BRANCH)
    monkeypatch.setattr(policy, "_new_code", lambda: "cafe1234")
    assert cli.main(["loop", "kill", _ISSUE, "--reason", "superseded by basicly-y2"]) == 1

    rc = cli.main([
        "loop",
        "kill",
        _ISSUE,
        "--reason",
        "superseded by basicly-y2",
        "--confirm",
        "cafe1234",
    ])

    assert rc == 0
    assert torn_down == [(_WORKTREE, False)]
    assert f"{policy.MARKER} kill superseded by basicly-y2" in fake_br.comments
    assert fake_br.argv_for("close") == [
        ["close", _ISSUE, "--reason", "killed: superseded by basicly-y2"]
    ]


def test_kill_needs_a_code_at_every_integrity_level_even_on_a_tty(
    fake_br: _FakeBr, torn_down: list[tuple[str, bool]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interactive terminal is no substitute for the relay (D15).

    Checkpoint approval accepts a TTY instead of a code. Kill is the verb that
    removes a requirement, so it does not: a lane agent's inherited terminal is not
    evidence a human chose this. Nor is a grant — ``authorize_kill`` takes no
    ``grant_root``, so there is no level at which one could cover it.
    """
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)

    assert cli.main(["loop", "kill", _ISSUE, "--reason", "not this way"]) == 1
    assert fake_br.argv_for("close") == []
    assert torn_down == []


def test_kill_with_no_code_refuses_and_mints_one_without_closing_the_bead(
    fake_br: _FakeBr,
    torn_down: list[tuple[str, bool]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The bare kill is a challenge, and nothing at all is written (AC3)."""
    fake_br.external_ref = loop_state.format_worktree_ref(_WORKTREE, _BRANCH)
    monkeypatch.setattr(policy, "_new_code", lambda: "cafe1234")

    rc = cli.main(["loop", "kill", _ISSUE, "--reason", "superseded"])

    assert rc == 1
    assert fake_br.argv_for("close") == []
    assert torn_down == []
    assert not [text for text in fake_br.comments if text.startswith(f"{policy.MARKER} kill")]
    # Minted, not merely refused: a refusal with no code is a dead end.
    assert "--confirm cafe1234" in capsys.readouterr().err


def test_the_minted_kill_code_is_printed_in_a_runnable_rerun_line(
    fake_br: _FakeBr,
    torn_down: list[tuple[str, bool]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The challenge carries the exact command that completes the kill (AC3)."""
    monkeypatch.setattr(policy, "_new_code", lambda: "cafe1234")

    cli.main(["loop", "kill", _ISSUE, "--reason", "won't work this way", "--discard"])

    err = capsys.readouterr().err
    assert "CONFIRMATION REQUIRED" in err
    assert "The requirement is dropped" in err
    # Asserted by parsing the line back rather than by transcribing shell quoting
    # into the test: what has to hold is that the relayed command reproduces this
    # kill — same reason, --discard carried through, the minted code attached.
    rerun = next(line for line in err.splitlines() if line.strip().startswith("basicly loop kill"))
    assert shlex.split(rerun) == [
        "basicly",
        "loop",
        "kill",
        _ISSUE,
        "--reason",
        "won't work this way",
        "--discard",
        "--confirm",
        "cafe1234",
    ]
    # The challenge itself is inert: it does not tear down and it does not close.
    assert torn_down == []
    assert fake_br.argv_for("close") == []


def test_kill_refuses_an_expired_or_wrong_code_without_closing(
    fake_br: _FakeBr, torn_down: list[tuple[str, bool]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A code that does not match is a refusal, never a close."""
    monkeypatch.setattr(policy, "_new_code", lambda: "cafe1234")
    cli.main(["loop", "kill", _ISSUE, "--reason", "superseded"])

    rc = cli.main(["loop", "kill", _ISSUE, "--reason", "superseded", "--confirm", "0000dead"])

    assert rc == 1
    assert fake_br.argv_for("close") == []
    assert torn_down == []


def test_kill_refuses_when_the_teardown_would_lose_uncommitted_work(
    fake_br: _FakeBr, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A refused teardown leaves the bead open — never closed over a live lane."""
    fake_br.external_ref = loop_state.format_worktree_ref(_WORKTREE, _BRANCH)
    monkeypatch.setattr(policy, "_new_code", lambda: "cafe1234")

    def cleanup(name: str, *, force: bool = False, **_kwargs: object) -> None:
        if not force:
            raise SystemExit(f"worktree {name!r} has uncommitted changes")

    monkeypatch.setattr(worktree, "cleanup", cleanup)
    cli.main(["loop", "kill", _ISSUE, "--reason", "superseded"])

    rc = cli.main(["loop", "kill", _ISSUE, "--reason", "superseded", "--confirm", "cafe1234"])

    assert rc == 1
    assert fake_br.argv_for("close") == []
    assert "--discard" in capsys.readouterr().err


def test_kill_with_discard_forces_the_teardown_and_closes(
    fake_br: _FakeBr, torn_down: list[tuple[str, bool]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--discard`` is the deliberate opposite: the branch and the changes go."""
    fake_br.external_ref = loop_state.format_worktree_ref(_WORKTREE, _BRANCH)
    monkeypatch.setattr(policy, "_new_code", lambda: "cafe1234")
    cli.main(["loop", "kill", _ISSUE, "--reason", "abandoned", "--discard"])

    rc = cli.main([
        "loop",
        "kill",
        _ISSUE,
        "--reason",
        "abandoned",
        "--discard",
        "--confirm",
        "cafe1234",
    ])

    assert rc == 0
    assert torn_down == [(_WORKTREE, True)]
    assert fake_br.argv_for("close") == [["close", _ISSUE, "--reason", "killed: abandoned"]]


def test_kill_closes_a_lane_that_never_provisioned_a_worktree(
    fake_br: _FakeBr, torn_down: list[tuple[str, bool]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kill reaches a bead at any phase, including one with no binding to tear down."""
    monkeypatch.setattr(policy, "_new_code", lambda: "cafe1234")
    cli.main(["loop", "kill", _ISSUE, "--reason", "requirement withdrawn"])

    rc = cli.main([
        "loop",
        "kill",
        _ISSUE,
        "--reason",
        "requirement withdrawn",
        "--confirm",
        "cafe1234",
    ])

    assert rc == 0
    assert torn_down == []
    assert fake_br.argv_for("close") == [
        ["close", _ISSUE, "--reason", "killed: requirement withdrawn"]
    ]


def test_kill_refuses_an_unknown_bead_before_it_mints_a_code_at_all(
    fake_br: _FakeBr,
    torn_down: list[tuple[str, bool]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A typo'd id must not cost a human a code relay before it fails.

    The teardown needs the bead's binding, so that read has to happen; doing it
    after the gate would mint a code, send someone to relay it, and only then fail
    on the record. No challenge printed is the observable form of "nothing minted".
    """
    monkeypatch.setattr(policy, "_new_code", lambda: "cafe1234")
    monkeypatch.setattr(br, "read_record", lambda *_args: None)

    rc = cli.main(["loop", "kill", "basicly-nope", "--reason", "typo"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "CONFIRMATION REQUIRED" not in err
    assert "basicly-nope" in err
    assert torn_down == []
    assert fake_br.argv_for("close") == []


def test_kill_refuses_a_blank_reason_before_it_costs_a_code_relay(
    fake_br: _FakeBr, torn_down: list[tuple[str, bool]], capsys: pytest.CaptureFixture[str]
) -> None:
    """The reason is the only record left once the bead closes, so it is required."""
    rc = cli.main(["loop", "kill", _ISSUE, "--reason", "   "])

    assert rc == 1
    assert fake_br.argv_for("close") == []
    assert torn_down == []
    assert "--reason must say why" in capsys.readouterr().err


# --- The requirements document's own claim (AC4) -------------------------------


def test_the_requirements_document_no_longer_claims_hold_re_admits_the_lane() -> None:
    """§5 blamed the status vocabulary; ``deferred`` was already excluded.

    A design document is the input to its own decomposition, so a false [M] in it
    buys the wrong work. The corrected claim has to name where the gap really was.
    """
    text = _REQUIREMENTS_MD.read_text(encoding="utf-8")

    assert "re-admits the lane" not in text
    assert "DISPATCHABLE_STATUSES" in text
