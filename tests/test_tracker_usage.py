"""Tests for the measured br/bv surface ledger (basicly-vkh0.1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import tracker_usage


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo that has opted into recording by committing the ledger directory."""
    (tmp_path / tracker_usage.LEDGER_FILE).parent.mkdir(parents=True)
    return tmp_path


def _spool_lines(repo: Path) -> list[dict]:
    raw = (repo / tracker_usage.SPOOL_FILE).read_text(encoding="utf-8")
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


@pytest.fixture
def worktree_of(repo: Path, tmp_path: Path) -> Path:
    """A loop worktree sharing *repo*'s tracker through br's ``.beads/redirect``.

    Both halves of the real shape matter to the bug under test: the redirect file
    pointing at the base's ``.beads``, and the worktree's *own* checked-out
    ``.basicly/ledger/`` directory — the latter is what made the recorder believe
    the worktree owned a ledger.
    """
    base_beads = repo / ".beads"
    base_beads.mkdir(parents=True, exist_ok=True)
    worktree = tmp_path / "wt"
    (worktree / ".beads").mkdir(parents=True)
    (worktree / ".beads" / "redirect").write_text(f"{base_beads}\n", encoding="utf-8")
    (worktree / tracker_usage.LEDGER_FILE).parent.mkdir(parents=True)
    return worktree


# --- One ledger per repo, never one per worktree (basicly-vkh0.8) --------------


def test_a_worktree_records_into_the_base_checkouts_spool(repo: Path, worktree_of: Path) -> None:
    """Teardown deletes the worktree, so a spool inside it is a discarded observation.

    Every engine tracker call from a lane was lost this way, and it made ``where``
    — called on every single provisioning — read as never used in the surface
    report (basicly-vkh0.8).
    """
    tracker_usage.record(worktree_of, "br", ["where", "--json"], site=tracker_usage.SITE_ENGINE)

    assert [entry["subcommand"] for entry in _spool_lines(repo)] == ["where"]
    # The defect: anything here dies with the worktree.
    assert not (worktree_of / tracker_usage.SPOOL_FILE).exists()


def test_ledger_root_follows_the_redirect_to_the_base_checkout(
    repo: Path, worktree_of: Path
) -> None:
    """One authority for the ledger's location, mirroring ``br.beads_dir``."""
    assert tracker_usage.ledger_root(worktree_of) == repo
    assert tracker_usage.ledger_root(repo) == repo


def test_ledger_root_ignores_a_redirect_that_does_not_name_a_beads_dir(tmp_path: Path) -> None:
    """A stale or hand-edited redirect must not scatter the spool somewhere arbitrary."""
    (tmp_path / ".beads").mkdir()
    (tmp_path / ".beads" / "redirect").write_text("/nonexistent/elsewhere\n", encoding="utf-8")
    assert tracker_usage.ledger_root(tmp_path) == tmp_path


def test_a_worktree_promotes_the_shared_spool_into_its_own_ledger(
    repo: Path, worktree_of: Path
) -> None:
    """The tracked ledger belongs to the branch; the spool belongs to the machine.

    Promoting from a lane must grow the *worktree's* ledger, because that is the file
    that lands, while draining the base's spool where the observations accumulated.
    """
    tracker_usage.record(worktree_of, "br", ["where"], site=tracker_usage.SITE_ENGINE)

    assert tracker_usage.promote(worktree_of) == (1, 0)

    committed = (worktree_of / tracker_usage.LEDGER_FILE).read_text(encoding="utf-8")
    assert json.loads(committed.strip())["subcommand"] == "where"
    # The base's committed ledger is untouched: it is a different branch's file.
    assert not (repo / tracker_usage.LEDGER_FILE).exists()
    # And the shared spool is drained, so the next promote cannot double-count.
    assert _spool_lines(repo) == []


def test_summarize_from_a_worktree_sees_what_its_lane_recorded(worktree_of: Path) -> None:
    """A report run inside a lane must not read as though the lane did nothing."""
    tracker_usage.record(worktree_of, "br", ["where"], site=tracker_usage.SITE_ENGINE)

    rows = {row.subcommand: row for row in tracker_usage.summarize(worktree_of)}
    assert rows["where"].engine_calls == 1


# --- Classifying a surface's access ------------------------------------------


def test_classify_access_reports_unknown_as_unclassified() -> None:
    """Guessing a bucket would bias the read/write ratio this ledger exists to produce."""
    assert tracker_usage.classify_access("list") == "read"
    assert tracker_usage.classify_access("create") == "write"
    assert tracker_usage.classify_access("teleport") == "unclassified"


def test_classify_access_covers_two_word_subcommands() -> None:
    """``split_invocation`` joins the pair, so a single-word entry can never match it.

    Found by running the report on a real pass: every two-word read landed in
    ``unclassified`` and deflated the read side of the ratio the cache design
    rests on. The two halves of one ``dep``/``comments``/``gate`` pair also differ
    in access class, which is the reason the pair is one surface.
    """
    assert tracker_usage.classify_access("comments list") == "read"
    assert tracker_usage.classify_access("comments add") == "write"
    assert tracker_usage.classify_access("gate list") == "read"
    assert tracker_usage.classify_access("gate report") == "write"
    assert tracker_usage.classify_access("dep cycles") == "read"
    assert tracker_usage.classify_access("dep add") == "write"
    # Both sat in `unclassified` against real measured traffic (basicly-vkh0.2):
    # `sync` is the engine's second-most-called write at 57 calls, and `dep list`
    # was the one `dep` read the set forgot while listing its two siblings.
    assert tracker_usage.classify_access("sync") == "write"
    assert tracker_usage.classify_access("dep list") == "read"


def test_promote_discards_a_record_that_is_not_a_surface(repo: Path) -> None:
    """The committed ledger feeds a surface freeze, so junk must not enter it.

    A spool written by an older recorder holds shell text (``2>&1``). Validating at
    the promote boundary means the committed artifact is clean whatever produced the
    spool, rather than depending on every machine having upgraded.
    """
    spool = repo / tracker_usage.SPOOL_FILE
    spool.parent.mkdir(parents=True, exist_ok=True)
    spool.write_text(
        '{"binary":"br","subcommand":"show","site":"engine"}\n'
        '{"binary":"br","subcommand":"2>&1","site":"interactive"}\n'
        '{"binary":"br","subcommand":"$g","site":"interactive"}\n'
        '{"binary":"bv","subcommand":"--robot-next","site":"interactive"}\n',
        encoding="utf-8",
    )

    assert tracker_usage.promote(repo) == (2, 2)
    kept = [
        json.loads(line)["subcommand"]
        for line in (repo / tracker_usage.LEDGER_FILE).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # A leading long flag is a legitimate surface for a binary with no subcommands.
    assert kept == ["show", "--robot-next"]


# --- Opt-in -------------------------------------------------------------------


def test_recording_is_off_without_the_committed_ledger_directory(tmp_path: Path) -> None:
    """A repo that has not opted in must not be written to at all.

    Recording unconditionally created ``.basicly/usage/`` in any consumer repo as
    a side effect of a tracker call, which left ``.basicly`` behind after an
    uninstall that had removed everything it manages. For a distribution that
    uninvited write multiplies across the install base, so the absence of the
    ledger directory has to mean silence rather than "create it".
    """
    assert tracker_usage.is_enabled(tmp_path) is False

    tracker_usage.record(tmp_path, "br", ["list"], site=tracker_usage.SITE_ENGINE)
    with tracker_usage.timed(tmp_path, "br", ["ready"], site=tracker_usage.SITE_ENGINE):
        pass

    assert not (tmp_path / ".basicly").exists()


def test_opting_in_is_the_presence_of_the_ledger_directory(repo: Path) -> None:
    """No config key: the committed directory is the switch."""
    assert tracker_usage.is_enabled(repo) is True


# --- Recording ----------------------------------------------------------------


def test_record_appends_one_line_per_call_and_self_ignores(repo: Path) -> None:
    """The spool must never dirty the tree: dirt outside .beads/ blocks every landing."""
    tracker_usage.record(repo, "br", ["list", "--json"], site=tracker_usage.SITE_ENGINE)
    tracker_usage.record(repo, "bv", ["show", "x"], site=tracker_usage.SITE_INTERACTIVE)

    entries = _spool_lines(repo)
    assert [e["subcommand"] for e in entries] == ["list", "show"]
    assert [e["site"] for e in entries] == ["engine", "interactive"]
    assert (repo / ".basicly/usage/.gitignore").read_text(encoding="utf-8") == "*\n"


def test_record_never_raises_on_an_unwritable_spool(repo: Path) -> None:
    """Telemetry must not be able to fail a tracker call.

    The spool's directory is occupied by a *file*, so ``mkdir`` fails. Chosen over
    a permission bit deliberately: a chmod-based test asserts nothing on Windows,
    where the CI leg runs, while a path-type collision fails identically
    everywhere.
    """
    (repo / ".basicly/usage").write_text("not a directory", encoding="utf-8")

    tracker_usage.record(repo, "br", ["list"], site=tracker_usage.SITE_ENGINE)  # must not raise
    assert not (repo / tracker_usage.SPOOL_FILE).is_file()


def test_timed_records_a_duration_and_reraises(repo: Path) -> None:
    """Duration is the latency half of the measurement; an exception still propagates."""
    with tracker_usage.timed(repo, "br", ["ready"], site=tracker_usage.SITE_ENGINE):
        pass

    entry = _spool_lines(repo)[0]
    assert entry["duration_ms"] >= 0
    assert entry["ok"] is True

    # `pytest.raises` fails when nothing propagates — i.e. when `timed()` swallowed the
    # caller's exception, which is the property under test.
    with (
        pytest.raises(RuntimeError, match="boom"),
        tracker_usage.timed(repo, "br", ["ready"], site=tracker_usage.SITE_ENGINE),
    ):
        raise RuntimeError("boom")
    assert _spool_lines(repo)[1]["ok"] is False


# --- Promote and summarize ----------------------------------------------------


def test_promote_moves_the_spool_into_the_committed_ledger(repo: Path) -> None:
    """The ledger is the part that travels; the spool is only the write buffer."""
    tracker_usage.record(repo, "br", ["list"], site=tracker_usage.SITE_ENGINE)
    tracker_usage.record(repo, "br", ["create"], site=tracker_usage.SITE_ENGINE)

    assert tracker_usage.promote(repo) == (2, 0)
    ledger = (repo / tracker_usage.LEDGER_FILE).read_text(encoding="utf-8")
    assert len(ledger.splitlines()) == 2
    assert _spool_lines(repo) == []
    assert tracker_usage.promote(repo) == (0, 0)  # nothing left to move


def test_promote_appends_rather_than_replacing(repo: Path) -> None:
    """Append-only: a second machine's sample adds to the first, never overwrites it."""
    ledger = repo / tracker_usage.LEDGER_FILE
    ledger.write_text('{"binary":"br","subcommand":"ready","site":"engine"}\n', encoding="utf-8")

    tracker_usage.record(repo, "br", ["list"], site=tracker_usage.SITE_ENGINE)
    tracker_usage.promote(repo)

    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 2


def test_load_and_summarize_span_ledger_and_spool(repo: Path) -> None:
    """An un-promoted sample still counts, or a report would understate today's work."""
    tracker_usage.record(repo, "br", ["list", "--json"], site=tracker_usage.SITE_ENGINE)
    tracker_usage.promote(repo)
    tracker_usage.record(repo, "br", ["list"], site=tracker_usage.SITE_INTERACTIVE)

    rows = {(r.binary, r.subcommand): r for r in tracker_usage.summarize(repo)}
    row = rows[("br", "list")]
    assert row.calls == 2
    assert row.engine_calls == 1 and row.interactive_calls == 1
    assert row.flags == ("--json",)
    assert row.access == "read"


def test_summarize_means_only_over_timed_calls(repo: Path) -> None:
    """An interactive call has no duration; counting it as zero would drag the mean down."""
    tracker_usage.record(repo, "br", ["show"], site=tracker_usage.SITE_ENGINE, duration_ms=40.0)
    tracker_usage.record(repo, "br", ["show"], site=tracker_usage.SITE_INTERACTIVE)

    row = next(r for r in tracker_usage.summarize(repo) if r.subcommand == "show")
    assert row.calls == 2
    assert row.mean_ms == 40.0


def test_a_torn_line_is_discarded_not_fatal(repo: Path) -> None:
    """Several lanes append concurrently; a partial record must not poison the read."""
    spool = repo / tracker_usage.SPOOL_FILE
    spool.parent.mkdir(parents=True)
    spool.write_text(
        '{"binary":"br","subcommand":"list","site":"engine"}\n'
        '{"binary":"br","subcom\n'
        "not json at all\n"
        '{"binary":"br","subcommand":"ready","site":"engine"}\n',
        encoding="utf-8",
    )

    assert [r.subcommand for r in tracker_usage.summarize(repo)] == ["list", "ready"]


def test_summarize_and_load_are_empty_without_a_ledger(repo: Path) -> None:
    """A missing ledger reports nothing rather than failing the command."""
    assert tracker_usage.load(repo) == []
    assert tracker_usage.summarize(repo) == []


def test_access_ratio_totals_calls_per_class(repo: Path) -> None:
    """The read/write ratio is the number the cache design rests on."""
    for args in (["list"], ["show"], ["create"]):
        tracker_usage.record(repo, "br", args, site=tracker_usage.SITE_ENGINE)

    assert tracker_usage.access_ratio(tracker_usage.summarize(repo)) == {"read": 2, "write": 1}


# --- Surface executions, the release gate's committed evidence (basicly-irrm) ---


def test_surface_executions_is_empty_without_a_ledger(repo: Path) -> None:
    """Empty, never None — the no-evidence-at-all judgement spans both ledgers.

    So the caller that reads both owns it, and this half never claims it.
    """
    assert tracker_usage.surface_executions(repo) == {}


def test_surface_executions_credits_the_surface_and_the_bare_binary(repo: Path) -> None:
    """The bare binary is a key too, so `br` is provable from the committed ledger.

    Otherwise a machine that never typed `br` in a shell has no evidence for it and the
    release gate refuses over a capability this ledger proves ran.
    """
    tracker_usage.record(repo, "br", ["show", "fx-1", "--json"], site=tracker_usage.SITE_ENGINE)
    tracker_usage.record(repo, "br", ["gate", "report"], site=tracker_usage.SITE_ENGINE)

    assert tracker_usage.surface_executions(repo) == {
        "br show": 1,
        "br gate report": 1,
        "br": 2,
    }
