"""Tests for naming the surface a br/bv invocation exercised (basicly-vkh0.1)."""

from __future__ import annotations

from basicly import tracker_invocation


def test_split_invocation_keeps_flag_names_and_drops_every_value() -> None:
    """A value can be an issue title, a path, or a secret; only names are recorded."""
    subcommand, flags = tracker_invocation.split_invocation([
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
    _, flags = tracker_invocation.split_invocation(["list", "--db=/home/me/beads.db"])
    assert flags == ("--db",)


def test_split_invocation_joins_a_two_word_subcommand() -> None:
    """``dep add`` and ``dep cycles`` are distinct operations the replacement owes separately."""
    assert tracker_invocation.split_invocation(["dep", "add", "a", "b"])[0] == "dep add"
    assert tracker_invocation.split_invocation(["comments", "list", "x"])[0] == "comments list"


def test_split_invocation_treats_a_leading_flag_as_the_surface() -> None:
    """``br --version`` has no positional, and the flag is the only name for it."""
    subcommand, flags = tracker_invocation.split_invocation(["--version"])
    assert subcommand == "--version"
    assert flags == ("--version",)


def test_split_invocation_rejects_shell_text_as_a_surface() -> None:
    """A redirection or an unexpanded variable is not a surface (basicly-vkh0.2).

    ``br --version 2>&1`` recorded the surface ``2>&1`` and ``br $g --help`` inside
    a shell loop recorded ``$g``; four fake surfaces reached the committed ledger
    that way. Only the junk word is dropped, so the real surface — the leading flag
    — is still recorded rather than the whole observation being lost.
    """
    assert tracker_invocation.split_invocation(["--version", "2>&1"])[0] == "--version"
    assert tracker_invocation.split_invocation(["$g", "--help"])[0] == "--help"
    assert tracker_invocation.split_invocation(["show", "2>&1"])[0] == "show"
    assert tracker_invocation.split_invocation(["2>&1"])[0] == ""


def test_split_invocation_joins_every_group_br_actually_has() -> None:
    """The group set missed six real groups, collapsing five ``label`` operations into one.

    ``br label add`` and ``br label list`` differ in access class, so recording
    both as ``label`` understates the surface count a freeze reads and makes the
    read/write ratio meaningless for the pair.
    """
    assert tracker_invocation.split_invocation(["label", "add", "x"])[0] == "label add"
    assert tracker_invocation.split_invocation(["epic", "status"])[0] == "epic status"
    assert tracker_invocation.split_invocation(["query", "run", "q"])[0] == "query run"
    assert tracker_invocation.split_invocation(["history", "diff"])[0] == "history diff"
    assert tracker_invocation.split_invocation(["audit", "log"])[0] == "audit log"
    assert tracker_invocation.split_invocation(["doctor", "health"])[0] == "doctor health"


def test_split_invocation_does_not_invent_a_group_br_lacks() -> None:
    """``catalog`` is a basicly command; br has never had one, so ``sync`` stays bare."""
    assert "catalog" not in tracker_invocation.GROUP_SUBCOMMANDS
    assert tracker_invocation.split_invocation(["sync", "--flush-only"])[0] == "sync"


def test_is_valid_surface_accepts_only_the_shapes_split_invocation_emits() -> None:
    """The predicate is the ledger's boundary check, so its edges are worth pinning."""
    assert tracker_invocation.is_valid_surface("show")
    assert tracker_invocation.is_valid_surface("dep add")
    assert tracker_invocation.is_valid_surface("--robot-next")
    assert not tracker_invocation.is_valid_surface("")
    assert not tracker_invocation.is_valid_surface("2>&1")
    assert not tracker_invocation.is_valid_surface("$g")
    assert not tracker_invocation.is_valid_surface("--agents-")
    assert not tracker_invocation.is_valid_surface("show me the money")
