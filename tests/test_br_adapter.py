"""Tests for the single br adapter seam (src/basicly/br.py)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from basicly import br


def test_run_br_raises_when_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The hard entry point raises with one canonical absence message."""
    monkeypatch.setattr(br, "which", lambda: None)
    with pytest.raises(RuntimeError, match="br is not on PATH"):
        br.run_br(tmp_path, ["ready"])


def test_try_run_br_returns_none_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The soft entry point degrades to None for optional tracker features."""
    monkeypatch.setattr(br, "which", lambda: None)
    assert br.try_run_br(tmp_path, ["sync", "--merge"]) is None


def test_version_probe_warns_below_the_floor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An outdated br gets one warning per process, never a failure."""
    monkeypatch.setattr(br, "which", lambda: "/usr/bin/br")
    monkeypatch.setattr(br, "_probed_paths", set())

    def fake_run(cmd, **_kw):
        out = "br 0.0.1" if "--version" in cmd else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(br.subprocess, "run", fake_run)
    br.run_br(tmp_path, ["ready"])
    br.run_br(tmp_path, ["ready"])
    err = capsys.readouterr().err
    assert err.count("older than the harness floor") == 1


def test_version_probe_warns_when_newer_than_the_pin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A br *above* the pin warns too: 0.2.19 broke `gate report` (basicly-o7z5).

    The floor check cannot see this — it compares major.minor, where 0.2.19 and
    the pinned 0.2.16 are equal — so an upgraded machine ran a tracker the
    harness was never tested against and the only symptom was integration
    tests failing there while CI, still on the pin, stayed green.
    """
    monkeypatch.setattr(br, "which", lambda: "/usr/bin/br")
    monkeypatch.setattr(br, "_probed_paths", set())

    def fake_run(cmd, **_kw):
        out = "br 0.2.19" if "--version" in cmd else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(br.subprocess, "run", fake_run)
    br.run_br(tmp_path, ["ready"])
    br.run_br(tmp_path, ["ready"])
    err = capsys.readouterr().err
    assert err.count("is not the pinned") == 1
    assert br.PINNED_VERSION in err
    assert "older than the harness floor" not in err


def test_version_probe_is_silent_on_the_pinned_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The supported state warns about nothing, so the warning stays meaningful."""
    monkeypatch.setattr(br, "which", lambda: "/usr/bin/br")
    monkeypatch.setattr(br, "_probed_paths", set())

    def fake_run(cmd, **_kw):
        out = f"br {br.PINNED_VERSION}" if "--version" in cmd else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(br.subprocess, "run", fake_run)
    br.run_br(tmp_path, ["ready"])
    assert capsys.readouterr().err == ""


# --- Reading the committed export (basicly-kjc5.50) --------------------------


def _write_export(beads: Path, *lines: str) -> None:
    beads.mkdir(parents=True, exist_ok=True)
    (beads / "issues.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_export_records_skips_junk_and_keeps_file_order(tmp_path: Path) -> None:
    """An unparsable or id-less line is skipped, never fatal: every consumer is evidence."""
    _write_export(
        tmp_path / ".beads",
        json.dumps({"id": "b-1"}),
        "{not json",
        json.dumps({"title": "no id"}),
        "",
        json.dumps({"id": "b-2"}),
    )
    assert [record["id"] for record in br.export_records(tmp_path)] == ["b-1", "b-2"]


def test_export_records_is_empty_without_an_export(tmp_path: Path) -> None:
    """No workspace, no records — the callers all degrade rather than fail."""
    assert br.export_records(tmp_path) == []


def test_export_records_follows_the_beads_redirect(tmp_path: Path) -> None:
    """A harness worktree shares the base tracker, so the redirect target is authoritative."""
    base = tmp_path / "base" / ".beads"
    _write_export(base, json.dumps({"id": "base-1"}))
    worktree = tmp_path / "wt"
    _write_export(worktree / ".beads", json.dumps({"id": "stale-1"}))
    (worktree / ".beads" / "redirect").write_text(str(base), encoding="utf-8")

    assert br.beads_dir(worktree) == base
    assert [record["id"] for record in br.export_records(worktree)] == ["base-1"]


def test_export_comment_texts_reads_only_well_formed_comments() -> None:
    """Comments are the shared ledger's carrier; a malformed row is ignored."""
    record = {
        "id": "b-1",
        "comments": [{"text": "first"}, {"author": "niksa"}, "not a row", {"text": 7}],
    }
    assert br.export_comment_texts(record) == ["first"]
    assert br.export_comment_texts({"id": "b-2"}) == []


# --- br's clock-skew rejection (basicly-jr0l.41) -------------------------------

# br's real output, copied from an observed failure rather than composed (basicly-aswc).
# The previous fixture read "Error: Validation failed: updated_at: cannot be before
# created_at", which br has never printed — it prints a Rust struct with the field and
# the message as separate members. Every test below passed against that invented string
# while the recogniser matched nothing in the field, so the retry was dead code and the
# suite said otherwise. A fixture for a dependency's error text has to be observed.
_SKEW_STDERR = (
    'Error: Validation errors: [ValidationError { field: "updated_at", message: '
    '"cannot be before created_at" }, ValidationError { field: "closed_at", message: '
    '"cannot be before created_at" }]'
)


def _skewed_run(monkeypatch: pytest.MonkeyPatch, failures: int, stderr: str) -> list[list[str]]:
    """Fake br: fail *failures* times with *stderr*, then succeed. Returns the calls."""
    monkeypatch.setattr(br, "which", lambda: "/usr/bin/br")
    monkeypatch.setattr(br, "_probed_paths", {"/usr/bin/br"})
    calls: list[list[str]] = []

    def fake_run(cmd, **_kw):
        calls.append(list(cmd))
        if len(calls) <= failures:
            return subprocess.CompletedProcess(cmd, 1, "", stderr)
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(br.subprocess, "run", fake_run)
    return calls


def test_a_clock_skew_rejection_is_retried_until_it_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Br rejects an update whose updated_at precedes created_at: the clock, not the request.

    This blocked a landing twice consecutively with a different victim test each
    run, so it read as suite flakiness rather than as a dependency defect.
    """
    calls = _skewed_run(monkeypatch, failures=2, stderr=_SKEW_STDERR)
    slept: list[float] = []
    monkeypatch.setattr(br.time, "sleep", slept.append)

    proc = br.run_br(tmp_path, ["update", "x", "-t", "task"])

    assert proc.returncode == 0
    assert len(calls) == 3  # two rejections, then the retry that stuck
    assert slept, "the retry must wait for the clock to catch up, not re-read the same skew"


def test_any_other_br_error_is_not_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real error must still fail fast — this is one defect's escape hatch, not a retry policy."""
    calls = _skewed_run(monkeypatch, failures=1, stderr="Error: issue not found")
    monkeypatch.setattr(br.time, "sleep", lambda _s: pytest.fail("must not back off"))

    with pytest.raises(RuntimeError, match="issue not found"):
        br.run_br(tmp_path, ["update", "nope"])

    assert len(calls) == 1


def test_a_persistent_clock_skew_gives_up_at_the_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retry is bounded by elapsed time, not by an attempt count.

    Bounded that way because the wait a skew needs cannot be derived — br's error
    names no timestamps — so a fixed ladder could not span a step larger than
    itself (basicly-jr0l.42). The clock here advances slowly enough to allow far
    more attempts than any fixed ladder would, which is what makes this
    discriminating rather than merely satisfied.

    Injects the clock rather than patching ``br.time.monotonic``: that patches the
    global ``time`` module, so ``tracker_usage.timed`` would consume the same
    ticks for its own latency measurement and the count would silently be wrong.
    """
    _skewed_run(monkeypatch, failures=99, stderr=_SKEW_STDERR)
    calls: list[float] = []
    ticks = iter([n * 0.5 for n in range(40)])

    proc = br._spawn_tolerating_transient(
        "/usr/bin/br",
        tmp_path,
        ["update", "x"],
        sleep=calls.append,
        monotonic=lambda: next(ticks),
    )

    assert br._is_clock_skew(proc), "the caller must still see the unrescued failure"
    # deadline = 0.0 + 5.0; the check reaches it on the tenth attempt, so nine waits
    assert len(calls) == 9, len(calls)


def test_the_deadline_never_consults_the_wall_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wall clock is the thing misbehaving, so it cannot bound a wait on itself.

    A wall-clock deadline would be extended by the very backward step it is
    waiting out, so the retry could outlive its own bound.
    """
    _skewed_run(monkeypatch, failures=1, stderr=_SKEW_STDERR)
    monkeypatch.setattr(br.time, "sleep", lambda _s: None)
    monkeypatch.setattr(br.time, "time", lambda: pytest.fail("must not read the wall clock"))

    assert br.run_br(tmp_path, ["update", "x"]).returncode == 0


def test_a_retry_is_countable_in_the_usage_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bound stays a guess until the ledger says how many attempts a real skew needs.

    Only a retry carries the field, so existing ledger lines stay byte-identical
    and the committed file does not churn.
    """
    _skewed_run(monkeypatch, failures=2, stderr=_SKEW_STDERR)
    monkeypatch.setattr(br.time, "sleep", lambda _s: None)
    recorded: list[int] = []
    monkeypatch.setattr(
        br.tracker_usage, "record", lambda *_a, **kw: recorded.append(kw.get("attempt", 1))
    )

    br.run_br(tmp_path, ["update", "x"])

    assert recorded == [1, 2, 3]


def test_a_soft_call_site_tolerates_the_same_skew(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """try_run_br swallows failures, so without the retry the skew would corrupt state silently."""
    calls = _skewed_run(monkeypatch, failures=1, stderr=_SKEW_STDERR)
    monkeypatch.setattr(br.time, "sleep", lambda _s: None)

    proc = br.try_run_br(tmp_path, ["comments", "add", "x", "note"])

    assert proc is not None and proc.returncode == 0
    assert len(calls) == 2


# --- the recogniser must match what br actually prints (basicly-aswc) --------


def test_the_recogniser_matches_the_error_br_really_emits() -> None:
    """The regression: the marker never appeared in br's output, so nothing was retried.

    Pinned against the observed text directly, not through the retry loop, because
    the loop passed for two releases while this returned False — a fixture composed
    from the field name and the message joined by a colon matched the recogniser and
    nothing else.
    """
    proc = subprocess.CompletedProcess(["br"], 1, "", _SKEW_STDERR)
    assert br._is_clock_skew(proc) is True


def test_the_recogniser_matches_the_message_on_closed_at_alone() -> None:
    """A `br close` reports the same message against closed_at; that is still the skew."""
    stderr = (
        'Error: Validation errors: [ValidationError { field: "closed_at", '
        'message: "cannot be before created_at" }]'
    )
    assert br._is_clock_skew(subprocess.CompletedProcess(["br"], 1, "", stderr)) is True


def test_the_recogniser_reads_stdout_as_well_as_stderr() -> None:
    """Validation failures have arrived on either stream; neither may be missed."""
    proc = subprocess.CompletedProcess(["br"], 1, _SKEW_STDERR, "")
    assert br._is_clock_skew(proc) is True


@pytest.mark.parametrize(
    "stderr",
    [
        "Error: issue not found",
        # A validation error that is not a backwards clock step: the message is the
        # discriminator, and a widened matcher that keyed off the field alone would
        # retry this one until the deadline.
        'Error: Validation errors: [ValidationError { field: "updated_at", '
        'message: "must be an RFC3339 timestamp" }]',
        # ...and the mirror case: the right message attributed to a field that has
        # nothing to do with timestamp ordering.
        'Error: Validation errors: [ValidationError { field: "title", '
        'message: "cannot be before created_at" }]',
    ],
)
def test_an_unrelated_failure_is_not_read_as_clock_skew(stderr: str) -> None:
    """This is one defect's escape hatch, not a retry policy for every br error."""
    assert br._is_clock_skew(subprocess.CompletedProcess(["br"], 1, "", stderr)) is False


# --- br's storage contention under our own fan-out (basicly-vkh0.10) ----------

# br's real output, copied from the 2026-08-02 five-lane pass. The `retryable: false`
# is br's own verdict on it, and it is the wrong one — the same database answered five
# concurrent reads correctly immediately afterwards.
_STORAGE_ERROR_CODE = "DATABASE_ERROR"
_STORAGE_STDERR = (
    f'{{"error": {{"code": "{_STORAGE_ERROR_CODE}", "message": "Database error: WAL '
    'file is corrupt: short read at frame 12: got 0, need 4120", "retryable": false}}'
)


def test_a_storage_contention_failure_is_retried_until_it_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Four of five lane dispatches died here, each on a bead it had not been assigned.

    The store was fine seconds later, so the only thing that made this terminal was
    that nothing backed off.
    """
    calls = _skewed_run(monkeypatch, failures=2, stderr=_STORAGE_STDERR)
    slept: list[float] = []
    monkeypatch.setattr(br.time, "sleep", slept.append)

    proc = br.run_br(tmp_path, ["comments", "list", "basicly-x", "--json"])

    assert proc.returncode == 0
    assert len(calls) == 3  # two contended reads, then the retry that stuck
    assert slept, "the retry must wait for the writer to finish, not re-read the same lock"


def test_a_persistent_storage_failure_still_reaches_the_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store that stays broken must fail, not hang: the deadline bounds the wait.

    Classifying the error as retryable is a licence to back off, never a promise
    that it will clear — a permanent DATABASE_ERROR has to surface as an error.
    """
    _skewed_run(monkeypatch, failures=99, stderr=_STORAGE_STDERR)
    waits: list[float] = []
    ticks = iter([n * 0.5 for n in range(40)])

    proc = br._spawn_tolerating_transient(
        "/usr/bin/br",
        tmp_path,
        ["comments", "list", "basicly-x", "--json"],
        sleep=waits.append,
        monotonic=lambda: next(ticks),
    )

    assert proc.returncode != 0, "the caller must still see the unrescued failure"
    assert waits, "and it must have backed off before giving up"


def test_the_storage_recogniser_reads_brs_own_error_code() -> None:
    """Pinned against the observed response directly, not through the retry loop.

    The clock-skew recogniser passed its retry tests for two releases while matching
    nothing in the field, because the fixture was composed rather than observed
    (basicly-aswc). Keying on ``DATABASE_ERROR`` — the field br fills in for every
    storage failure — is what survives the message text changing with which page tore.
    """
    assert br.is_transient_storage_error(_STORAGE_STDERR) is True
    # The same failure rendered as plain text rather than the JSON envelope.
    assert br.is_transient_storage_error("Error: Database error: database is locked") is True


@pytest.mark.parametrize(
    "text",
    [
        "Error: issue not found",
        "br show basicly-x failed: no such issue",
        # A lane's own agent output quoting the words is not the tracker failing.
        "runner exited 3: the database error path needs a test",
    ],
)
def test_an_unrelated_failure_is_not_read_as_storage_contention(text: str) -> None:
    """Widening this to any error would retry a genuine failure until the deadline."""
    assert br.is_transient_storage_error(text) is False


def test_a_successful_read_of_a_bead_quoting_the_error_is_not_contention() -> None:
    """The recogniser must not read a *record* as a failure (the R1 laundering rule).

    Not hypothetical, and not composed: the bead that filed this requirement quotes
    br's whole error envelope in its own description, so ``br show basicly-vkh0.10
    --json`` returns the phrase on stdout with exit 0. Probing the recogniser against
    the live tracker is what found it — it answered True on that success, and only
    the retry loop's ``returncode == 0`` check kept it from retrying every read of
    that bead. A recogniser that needs its caller to order the checks correctly is a
    trap for the next caller, so the exit code is part of the recognition.
    """
    reading_the_bead = subprocess.CompletedProcess(["br"], 0, _STORAGE_STDERR, "")
    assert br._is_storage_contention(reading_the_bead) is False
    assert br._is_transient(reading_the_bead) is False


def test_record_text_on_stdout_cannot_outvote_the_real_error_on_stderr() -> None:
    """A failure that printed records first is diagnosed from stderr, not from both.

    Joining the streams lets a payload quoting the envelope classify a failure that
    stderr says is something else entirely — and the same joined text would then be
    retried to the deadline instead of failing fast.
    """
    printed_then_failed = subprocess.CompletedProcess(
        ["br"],
        1,
        f'[{{"id": "basicly-x", "description": "{_STORAGE_ERROR_CODE}"}}]',
        "Error: issue not found",
    )
    assert br._is_storage_contention(printed_then_failed) is False


# --- The one record read seam (basicly-tcmy.14) -------------------------------


class _Spawn:
    """A `try_run_br` stand-in returning one canned result, recording what it was asked."""

    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[list[str]] = []

    def __call__(self, _repo_root: Path, args: list[str]) -> object:
        self.calls.append(list(args))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _proc(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["br"], returncode, stdout, "")


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        # br spells a single record two ways, and both mean the same record. Reading
        # only one is what eleven call sites each wrote out by hand.
        ('[{"id":"b-1","status":"open"}]', {"id": "b-1", "status": "open"}),
        ('{"id":"b-1","status":"open"}', {"id": "b-1", "status": "open"}),
    ],
)
def test_both_spellings_of_one_record_read_the_same(
    payload: str, expected: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare object and a one-element array are the same record."""
    monkeypatch.setattr(br, "try_run_br", _Spawn(_proc(payload)))
    assert br.read_record(tmp_path, "b-1") == expected


@pytest.mark.parametrize(
    ("name", "result"),
    [
        # Every route to "no usable record", each of which some call site used to
        # answer differently: two raised, two returned None, four returned a local
        # empty, one carried a typed absence, and one did not guard the shape at all.
        ("br absent from PATH", None),
        ("a non-zero exit", _proc('{"error":{"code":"ISSUE_NOT_FOUND"}}', returncode=3)),
        ("output that is not JSON", _proc("not json at all")),
        ("an empty array", _proc("[]")),
        ("a JSON null", _proc("null")),
        ("a payload that is not an object", _proc('"a string"')),
        ("a spawn that raises RuntimeError", RuntimeError("br show failed")),
        ("a spawn that raises OSError", OSError("no such file")),
    ],
)
def test_every_absence_reads_as_none(
    name: str, result: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One contract for every way the read comes back without a record.

    The empty array is the case that matters for the flip (basicly-vkh0.19): it is the
    natural in-process answer for "no record matched", and against the eleven hand-written
    unwraps it split six sites raising ``IndexError`` from five taking their documented
    absence. Here it is one answer, decided once.
    """
    monkeypatch.setattr(br, "try_run_br", _Spawn(result))
    assert br.read_record(tmp_path, "b-1") is None, name


@pytest.mark.parametrize(
    "result",
    [
        None,
        _proc("[]"),
        _proc("null"),
        _proc("not json"),
        RuntimeError("br show failed"),
        OSError("no such file"),
    ],
)
def test_require_record_raises_one_message_for_every_absence(
    result: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hard half: a caller that cannot proceed gets an exception naming the bead.

    One message whatever the cause, so a caller no longer has to know whether it is
    looking at a missing bead, a missing binary or a malformed payload to say what
    went wrong.
    """
    monkeypatch.setattr(br, "try_run_br", _Spawn(result))
    with pytest.raises(RuntimeError, match="br show b-1 returned no issue record"):
        br.require_record(tmp_path, "b-1")


def test_require_record_returns_the_record_when_there_is_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control, so the refusal above is not passing because nothing ever succeeds."""
    monkeypatch.setattr(br, "try_run_br", _Spawn(_proc('[{"id":"b-1"}]')))
    assert br.require_record(tmp_path, "b-1") == {"id": "b-1"}


def test_the_read_asks_br_for_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The argv is part of the contract: a consumer reading text would parse prose."""
    spawn = _Spawn(_proc('[{"id":"b-1"}]'))
    monkeypatch.setattr(br, "try_run_br", spawn)
    br.read_record(tmp_path, "b-1")
    assert spawn.calls == [["show", "b-1", "--json"]]


# --- the cutover is inert until it is declared (basicly-vkh0.19) --------------


def test_a_repo_that_declares_no_mode_never_touches_the_owned_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The riskiest property of the dual write is that it changes nothing by default.

    A consumer clone has no ``[tracker]`` section and no kit tracker installed, so
    every br call must behave exactly as it did before the cutover existed — no ledger
    directory created in somebody else's tree, and no kit import attempted. Asserted by
    making the kit loader fail the test if it is reached at all, which is stronger than
    checking that no file appeared.
    """
    monkeypatch.setattr(br, "which", lambda: "/usr/bin/br")
    monkeypatch.setattr(br, "_probed_paths", {"/usr/bin/br"})
    monkeypatch.setattr(br.subprocess, "run", lambda _cmd, **_kw: _proc("", 0))
    monkeypatch.setattr(br, "kit", lambda _root: pytest.fail("the kit must not be loaded"))

    br.run_br(tmp_path, ["comments", "add", "b-1", "a note"])
    br.try_run_br(tmp_path, ["sync", "--merge"])

    assert br.tracker_mode(tmp_path) == br.MODE_EXTERNAL
    assert not (tmp_path / ".basicly").exists()


def test_the_read_seam_still_spawns_br_before_the_flip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`read_record`'s default path is unchanged: the flip is a declared mode, not a drift."""
    spawn = _Spawn(_proc('[{"id":"b-1"}]'))
    monkeypatch.setattr(br, "try_run_br", spawn)
    monkeypatch.setattr(br, "owned_record", lambda *_a: pytest.fail("must not read the ledger"))

    assert br.read_record(tmp_path, "b-1") == {"id": "b-1"}
    assert spawn.calls == [["show", "b-1", "--json"]]


def test_no_module_outside_the_seam_unwraps_a_record_itself() -> None:
    """The rule this bead exists to hold, checked against the tree rather than by eye.

    Eleven call sites across eight modules each wrote this expression out, in two
    variants that disagreed on the empty-array case. `br.py` is the one place allowed to
    know the shape; a twelfth copy anywhere else re-acquires the split, which is the same
    reason :func:`br.dependency_edge` exists.

    Matched on the unwrap *expression*, not on ``isinstance(data, list)`` alone: a plain
    list check is an ordinary shape guard on any payload, and `policy._finding_members`
    is one over a finding set. Banning that would be banning JSON.
    """
    unwrap = "data[0] if isinstance(data, list)"
    root = Path(__file__).parent.parent / "src" / "basicly"
    offenders = [
        path.name
        for path in sorted(root.glob("*.py"))
        if path.name != "br.py" and unwrap in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
