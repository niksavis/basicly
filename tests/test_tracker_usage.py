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


# --- Splitting an invocation into a surface -----------------------------------


def test_split_invocation_keeps_flag_names_and_drops_every_value() -> None:
    """A value can be an issue title, a path, or a secret; only names are recorded."""
    subcommand, flags = tracker_usage.split_invocation([
        "create",
        "Fix the thing",
        "-t",
        "bug",
        "--json",
        "-d",
        "/home/someone/secret/notes",
    ])
    assert subcommand == "create"
    assert flags == ("--json", "-d", "-t")
    joined = " ".join(flags)
    assert "someone" not in joined and "Fix the thing" not in joined


def test_split_invocation_truncates_an_inline_flag_value() -> None:
    """``--db=/home/me/x.db`` records the name only."""
    _, flags = tracker_usage.split_invocation(["list", "--db=/home/me/beads.db"])
    assert flags == ("--db",)


def test_split_invocation_joins_a_two_word_subcommand() -> None:
    """``dep add`` and ``dep cycles`` are distinct operations the replacement owes separately."""
    assert tracker_usage.split_invocation(["dep", "add", "a", "b"])[0] == "dep add"
    assert tracker_usage.split_invocation(["comments", "list", "x"])[0] == "comments list"


def test_split_invocation_treats_a_leading_flag_as_the_surface() -> None:
    """``br --version`` has no positional, and the flag is the only name for it."""
    subcommand, flags = tracker_usage.split_invocation(["--version"])
    assert subcommand == "--version"
    assert flags == ("--version",)


def test_split_invocation_rejects_shell_text_as_a_surface() -> None:
    """A redirection or an unexpanded variable is not a surface (basicly-vkh0.2).

    ``br --version 2>&1`` recorded the surface ``2>&1`` and ``br $g --help`` inside
    a shell loop recorded ``$g``; four fake surfaces reached the committed ledger
    that way. Only the junk word is dropped, so the real surface — the leading flag
    — is still recorded rather than the whole observation being lost.
    """
    assert tracker_usage.split_invocation(["--version", "2>&1"])[0] == "--version"
    assert tracker_usage.split_invocation(["$g", "--help"])[0] == "--help"
    assert tracker_usage.split_invocation(["show", "2>&1"])[0] == "show"
    assert tracker_usage.split_invocation(["2>&1"])[0] == ""


def test_split_invocation_joins_every_group_br_actually_has() -> None:
    """The group set missed six real groups, collapsing five ``label`` operations into one.

    ``br label add`` and ``br label list`` differ in access class, so recording
    both as ``label`` understates the surface count a freeze reads and makes the
    read/write ratio meaningless for the pair.
    """
    assert tracker_usage.split_invocation(["label", "add", "x"])[0] == "label add"
    assert tracker_usage.split_invocation(["epic", "status"])[0] == "epic status"
    assert tracker_usage.split_invocation(["query", "run", "q"])[0] == "query run"
    assert tracker_usage.split_invocation(["history", "diff"])[0] == "history diff"
    assert tracker_usage.split_invocation(["audit", "log"])[0] == "audit log"
    assert tracker_usage.split_invocation(["doctor", "health"])[0] == "doctor health"


def test_split_invocation_does_not_invent_a_group_br_lacks() -> None:
    """``catalog`` is a basicly command; br has never had one, so ``sync`` stays bare."""
    assert "catalog" not in tracker_usage.GROUP_SUBCOMMANDS
    assert tracker_usage.split_invocation(["sync", "--flush-only"])[0] == "sync"


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


def test_is_valid_surface_accepts_only_the_shapes_split_invocation_emits() -> None:
    """The predicate is the ledger's boundary check, so its edges are worth pinning."""
    assert tracker_usage.is_valid_surface("show")
    assert tracker_usage.is_valid_surface("dep add")
    assert tracker_usage.is_valid_surface("--robot-next")
    assert not tracker_usage.is_valid_surface("")
    assert not tracker_usage.is_valid_surface("2>&1")
    assert not tracker_usage.is_valid_surface("$g")
    assert not tracker_usage.is_valid_surface("--agents-")
    assert not tracker_usage.is_valid_surface("show me the money")


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

    try:
        with tracker_usage.timed(repo, "br", ["ready"], site=tracker_usage.SITE_ENGINE):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    else:  # pragma: no cover - the context manager must not swallow
        raise AssertionError("timed() swallowed the caller's exception")
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
