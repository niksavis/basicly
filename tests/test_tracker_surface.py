"""Tests for the full br/bv surface inventory and the never-used set (basicly-vkh0.2)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from basicly import tracker_surface, tracker_usage

REPO_ROOT = Path(__file__).parent.parent
HOOK = REPO_ROOT / ".basicly" / "core" / "hooks" / "tool-usage.py"

# A clap help block with the two shapes that broke a naive parser: a wrapped
# description whose continuation line is indented deeper than the command column,
# and clap's own `help` entry. Held as test data rather than probed from br, so the
# parser is tested without needing the binary installed (platform-hermetic).
BR_HELP = """Agent-first issue tracker (SQLite + JSONL)

Usage: br [OPTIONS] <COMMAND>

Commands:
  blocked       List blocked issues
  dep           Manage dependencies
  gate          Workflow gate engine: record and inspect gate results
                and keep going on a wrapped line
  show          Show issue details
  help          Print this message

Options:
      --db <DB>    Database path
      --json       Output as JSON
"""

DEP_HELP = """Manage dependencies

Usage: br dep <COMMAND>

Commands:
  add     Add a dependency
  remove  Remove a dependency
  help    Print this message
"""


# --- Parsing br's documented contract -----------------------------------------


def test_parse_commands_reads_the_command_block() -> None:
    """The names in `Commands:` are the surface; clap's own `help` is not one."""
    assert tracker_surface.parse_commands(BR_HELP) == ["blocked", "dep", "gate", "show"]


def test_parse_commands_ignores_a_wrapped_description_line() -> None:
    """A continuation line is indented deeper, so its first word is not a command."""
    assert "and" not in tracker_surface.parse_commands(BR_HELP)


def test_parse_commands_is_empty_without_a_command_block() -> None:
    """Bv has no subcommands at all, and that must read as empty rather than raise."""
    assert tracker_surface.parse_commands("Usage: bv [flags]\n\nGeneral Flags:\n  --db x\n") == []


def test_parse_flags_keeps_long_flags_only() -> None:
    """A short flag is an alias and adds no surface to freeze."""
    assert tracker_surface.parse_flags(BR_HELP) == ["--db", "--json"]


def test_parse_flags_rejects_a_hyphen_wrapped_fragment() -> None:
    """Help text wraps a long flag mid-name; `--agents-` is not a flag."""
    wrapped = "  --agents-\n      update   Update AGENTS.md\n  --robot-next  Next issue\n"
    assert tracker_surface.parse_flags(wrapped) == ["--robot-next"]


# --- The reduction the freeze reads -------------------------------------------


@pytest.fixture
def inventory() -> dict:
    """A minimal inventory with one group, one two-word surface, and a flag-only bv."""
    return {
        "br": {
            "version": "br 0.2.16",
            "groups": ["dep"],
            "commands": ["blocked", "dep", "dep add", "dep remove", "show"],
            "global_flags": ["--json"],
        },
        "bv": {"version": "bv v0.18.0", "commands": [], "flags": ["--robot-next"]},
    }


def test_never_used_names_the_surfaces_we_can_skip(inventory: dict) -> None:
    """Deciding not to build these is how owning the tracker stays tractable."""
    unused = tracker_surface.never_used(inventory, {("br", "show"), ("br", "dep add")})
    assert unused["br"] == ["blocked", "dep", "dep remove"]
    assert unused["bv"] == ["--robot-next"]


def test_never_used_is_empty_when_everything_is_exercised(inventory: dict) -> None:
    """The empty case must read as "nothing to skip", not as a missing inventory."""
    measured = {("br", name) for name in inventory["br"]["commands"]}
    measured.add(("bv", "--robot-next"))
    assert tracker_surface.never_used(inventory, measured) == {"br": [], "bv": []}


def test_bv_flags_are_its_surfaces(inventory: dict) -> None:
    """`split_invocation` records a leading flag as the subcommand, so both sides match."""
    assert tracker_surface.known_surfaces(inventory)["bv"] == {"--robot-next"}


def test_unknown_used_flags_a_surface_the_inventory_does_not_have(inventory: dict) -> None:
    """Either br drifted or the recorder invented a surface; both must be visible."""
    assert tracker_surface.unknown_used(inventory, {("br", "2>&1"), ("br", "show")}) == [
        ("br", "2>&1")
    ]


def test_load_returns_none_for_a_corrupt_inventory(tmp_path: Path) -> None:
    """A broken artifact degrades the report; it never crashes the command."""
    path = tmp_path / tracker_surface.INVENTORY_FILE
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert tracker_surface.load(tmp_path) is None


def test_save_then_load_round_trips(tmp_path: Path, inventory: dict) -> None:
    """The artifact is written by one command and read by another, so the pair must agree."""
    tracker_surface.save(tmp_path, inventory)
    assert tracker_surface.load(tmp_path) == inventory


def test_save_is_byte_stable_for_an_unchanged_inventory(tmp_path: Path, inventory: dict) -> None:
    """No timestamp: a regenerated artifact must not churn the diff."""
    first = tracker_surface.save(tmp_path, inventory).read_bytes()
    assert tracker_surface.save(tmp_path, inventory).read_bytes() == first


# --- The committed artifact must agree with the constants that mirror it -------


def test_committed_inventory_is_present_and_current() -> None:
    """The report reads this file offline, so it has to be in the tree."""
    committed = tracker_surface.load(REPO_ROOT)
    assert committed is not None, f"{tracker_surface.INVENTORY_FILE} is not committed"
    assert committed["schema"] == tracker_surface.SCHEMA
    assert committed["br"]["commands"], "inventory has no br commands"


def test_group_subcommands_matches_the_committed_inventory() -> None:
    """The hand-maintained group set drifted from br once and cost a surface count.

    `tracker_usage.GROUP_SUBCOMMANDS` decides whether `br label add` is recorded as
    one surface or collapsed to `label`. It was missing six real groups and carried
    `catalog`, which br has never had. The inventory is generated from `br --help`,
    so comparing against it is what makes the constant checkable rather than
    trusted.
    """
    committed = tracker_surface.load(REPO_ROOT)
    assert committed is not None
    assert tracker_surface.groups(committed) == tracker_usage.GROUP_SUBCOMMANDS


def test_hook_and_engine_agree_on_every_surface_split() -> None:
    """The hook duplicates the split by necessity; a comment cannot hold them in step.

    The hook runs as a standalone script under the host's interpreter, so it cannot
    import the package — the duplication is deliberate. This test is the gate that
    replaces "kept in step": both must produce the same surface for the same argv.
    """
    spec = importlib.util.spec_from_file_location("tool_usage_hook", HOOK)
    assert spec is not None and spec.loader is not None
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)

    assert set(tracker_usage.GROUP_SUBCOMMANDS) == hook.TWO_WORD_SUBCOMMANDS

    corpus = [
        ["show", "basicly-1"],
        ["dep", "add", "a", "b"],
        ["label", "add", "x"],
        ["comments", "list", "--json"],
        ["gate", "report", "--status", "pass"],
        ["--version"],
        ["--version", "2>&1"],
        ["$g", "--help"],
        ["sync", "--flush-only"],
        ["list", "--db=/home/me/beads.db"],
        [],
    ]
    for args in corpus:
        engine = tracker_usage.split_invocation(args)
        hooked = hook._split_invocation(list(args))
        assert (engine[0], list(engine[1])) == (hooked[0], hooked[1]), args
