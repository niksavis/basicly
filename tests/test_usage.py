"""Tests for the usage report's tool/skill join (basicly-3ymj).

The counter file accumulates across sessions and is never reset, so it still holds
rows written by recorders that have since been fixed: heredoc terminators, Python
keywords out of a heredoc body, flag fragments, worktree basenames. The report is
the half that has to tell those apart from tools, because the tools table is read
as culling evidence and noise there is wrong in both directions — it invents tools
nobody ran, and it buries a real command some earlier parser shredded.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import usage

if TYPE_CHECKING:
    import pytest


def _write_counters(repo: Path, counters: dict[str, dict]) -> Path:
    """Write *counters* as the tool-usage hook would, returning the file."""
    path = repo / usage.USAGE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(counters, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _install_executable(directory: Path, name: str) -> None:
    """Put a command *name* in *directory* that resolves on every platform.

    Both forms, unconditionally: POSIX resolves the bare file by its executable
    bit, Windows resolves the ``.cmd`` twin through PATHEXT and would never look at
    an extensionless one. Writing both makes the platform difference test *data*
    rather than something only the other OS's CI run ever exercises — and it is the
    real shape on disk, since npm installs both shims side by side.
    """
    directory.mkdir(parents=True, exist_ok=True)
    posix = directory / name
    posix.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    posix.chmod(0o755)
    (directory / f"{name}.cmd").write_text("@echo off\r\n", encoding="utf-8")


# --- The tools table lists commands, not fragments ------------------------------


def test_a_heredoc_terminator_or_keyword_is_never_a_tool(tmp_path: Path) -> None:
    """`PYEOF`, `def`, `assert`, `-d`: recorded heads that name no command.

    These are the top of the real table as of 2026-08-04 and the reason the tools
    half could not be cited: `PYEOF` at 33 executions reads exactly like a tool.
    """
    _write_counters(
        tmp_path,
        {
            "EOF": {"count": 28, "last_used": "2026-07-16"},
            "PYEOF": {"count": 33, "last_used": "2026-07-16"},
            "assert": {"count": 83, "last_used": "2026-07-16"},
            "def": {"count": 90, "last_used": "2026-07-17"},
            "-d": {"count": 120, "last_used": "2026-07-17"},
            "return": {"count": 31, "last_used": "2026-07-16"},
        },
    )

    report = usage.build_report(tmp_path, [])

    assert report is not None
    assert report.tools == ()
    assert [entry.name for entry in report.unresolved] == [
        "-d",
        "def",
        "assert",
        "PYEOF",
        "return",
        "EOF",
    ]


def test_an_unresolved_head_keeps_its_count_and_date(tmp_path: Path) -> None:
    """The bucket is counted, not discarded: a parser miss has to stay visible.

    Dropping these would make the next recorder regression invisible — the counts
    and the last-used date are what say whether the misses are all historical.
    """
    _write_counters(tmp_path, {"PYEOF": {"count": 33, "last_used": "2026-07-16"}})

    report = usage.build_report(tmp_path, [])

    assert report is not None
    assert report.unresolved == (usage.UsageEntry("PYEOF", 33, "2026-07-16"),)


def test_a_command_on_path_is_a_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A head that resolves on PATH is what the table is for."""
    bin_dir = tmp_path / "bin"
    _install_executable(bin_dir, "somereporter")
    monkeypatch.setenv("PATH", str(bin_dir))
    _write_counters(tmp_path, {"somereporter": {"count": 4, "last_used": "2026-08-04"}})

    report = usage.build_report(tmp_path, [])

    assert report is not None
    assert [entry.name for entry in report.tools] == ["somereporter"]
    assert report.unresolved == ()


def test_a_repo_local_executable_is_a_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`markdownlint-cli2` lives in node_modules/.bin and is only reached through npx.

    PATH alone filed its 168 recorded runs as parser noise — a real tool pushed to
    the wrong side of the very question the report exists to answer.
    """
    _install_executable(tmp_path / "node_modules" / ".bin", "markdownlint-cli2")
    monkeypatch.setenv("PATH", str(tmp_path / "nothing-here"))
    _write_counters(tmp_path, {"markdownlint-cli2": {"count": 168, "last_used": "2026-08-02"}})

    report = usage.build_report(tmp_path, [])

    assert report is not None
    assert [entry.name for entry in report.tools] == ["markdownlint-cli2"]


def test_a_catalog_command_is_a_tool_where_it_is_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tool the catalog teaches stays a tool on a machine that lacks the binary.

    Otherwise the culling report answers its own question: `xh` would read as a
    parser miss on every checkout that has not installed `xh`.
    """
    monkeypatch.setenv("PATH", str(tmp_path / "nothing-here"))
    _write_counters(tmp_path, {"xh": {"count": 3, "last_used": "2026-08-02"}})

    without = usage.build_report(tmp_path, [], set())
    with_catalog = usage.build_report(tmp_path, [], {"xh"})

    assert without is not None and with_catalog is not None
    assert [entry.name for entry in without.unresolved] == ["xh"]
    assert [entry.name for entry in with_catalog.tools] == ["xh"]


def test_the_report_leaves_the_counter_file_exactly_as_it_found_it(tmp_path: Path) -> None:
    """Never reset the counters to clear the noise: the history is the fixture."""
    path = _write_counters(
        tmp_path,
        {
            "PYEOF": {"count": 33, "last_used": "2026-07-16"},
            "skill:tool-br": {"count": 2, "last_used": "2026-08-02"},
        },
    )
    before = (path.read_bytes(), path.stat().st_mtime_ns)

    usage.build_report(tmp_path, ["tool-br"])

    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_the_skills_half_is_untouched_by_the_tool_split(tmp_path: Path) -> None:
    """The skills finding was sound before this change and has to stay sound after."""
    _write_counters(
        tmp_path,
        {
            "PYEOF": {"count": 33, "last_used": "2026-07-16"},
            "skill:tool-br": {"count": 2, "last_used": "2026-08-02"},
        },
    )

    report = usage.build_report(tmp_path, ["tool-br", "tool-yq"])

    assert report is not None
    assert [entry.name for entry in report.skills] == ["tool-br"]
    assert report.never_used_skills == ("tool-yq",)
    assert [entry.name for entry in report.unresolved] == ["PYEOF"]


def test_no_report_without_a_counter_file(tmp_path: Path) -> None:
    """No data is not an empty report: the hook may simply never have run here."""
    assert usage.build_report(tmp_path, ["tool-br"]) is None


# --- What the catalog vouches for -----------------------------------------------


def test_catalog_commands_reads_the_heads_of_shell_fences() -> None:
    """The commands a skill teaches are the names its shell examples start with."""
    instructions = "\n".join([
        "# tool-ripgrep",
        "",
        "```bash",
        "rg --json 'pattern' path/",
        "fd -e py | xargs rg -n todo",
        "```",
    ])

    assert usage.catalog_commands([instructions]) == frozenset({"rg", "fd"})


def test_catalog_commands_ignores_prose_comments_and_untagged_fences() -> None:
    """An untagged fence holds sample output, whose first words are prose.

    Admitting those would re-open the hole from the other end: `the`, `new` and
    `result` all sit in the recorded counters already.
    """
    instructions = "\n".join([
        "Run the tool with care.",
        "",
        "```",
        "the run finished with 2 results",
        "```",
        "",
        "```console",
        "# comment about the next line",
        "$ yq '.a' file.yaml",
        "```",
    ])

    assert usage.catalog_commands([instructions]) == frozenset({"yq"})


def test_catalog_commands_takes_the_basename_of_a_path() -> None:
    """A script invoked by path is recorded by basename, so it has to match by one."""
    instructions = "\n".join(["```sh", ".scripts/wired_or_deleted.py --check", "```"])

    assert usage.catalog_commands([instructions]) == frozenset({"wired_or_deleted.py"})
