from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import usage

if TYPE_CHECKING:
    import pytest


def _write_counters(repo: Path, counters: dict[str, dict]) -> Path:
    path = repo / usage.USAGE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(counters, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _install_executable(directory: Path, name: str) -> None:

    directory.mkdir(parents=True, exist_ok=True)
    posix = directory / name
    posix.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    posix.chmod(0o755)
    (directory / f"{name}.cmd").write_text("@echo off\r\n", encoding="utf-8")


def test_a_heredoc_terminator_or_keyword_is_never_a_tool(tmp_path: Path) -> None:

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

    _write_counters(tmp_path, {"PYEOF": {"count": 33, "last_used": "2026-07-16"}})

    report = usage.build_report(tmp_path, [])

    assert report is not None
    assert report.unresolved == (usage.UsageEntry("PYEOF", 33, "2026-07-16"),)


def test_a_command_on_path_is_a_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    _install_executable(bin_dir, "somereporter")
    monkeypatch.setenv("PATH", str(bin_dir))
    _write_counters(tmp_path, {"somereporter": {"count": 4, "last_used": "2026-08-04"}})

    report = usage.build_report(tmp_path, [])

    assert report is not None
    assert [entry.name for entry in report.tools] == ["somereporter"]
    assert report.unresolved == ()


def test_a_repo_local_executable_is_a_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    _install_executable(tmp_path / "node_modules" / ".bin", "markdownlint-cli2")
    monkeypatch.setenv("PATH", str(tmp_path / "nothing-here"))
    _write_counters(tmp_path, {"markdownlint-cli2": {"count": 168, "last_used": "2026-08-02"}})

    report = usage.build_report(tmp_path, [])

    assert report is not None
    assert [entry.name for entry in report.tools] == ["markdownlint-cli2"]


def test_a_catalog_command_is_a_tool_where_it_is_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.setenv("PATH", str(tmp_path / "nothing-here"))
    _write_counters(tmp_path, {"xh": {"count": 3, "last_used": "2026-08-02"}})

    without = usage.build_report(tmp_path, [], set())
    with_catalog = usage.build_report(tmp_path, [], {"xh"})

    assert without is not None and with_catalog is not None
    assert [entry.name for entry in without.unresolved] == ["xh"]
    assert [entry.name for entry in with_catalog.tools] == ["xh"]


def test_the_report_leaves_the_counter_file_exactly_as_it_found_it(tmp_path: Path) -> None:
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
    assert usage.build_report(tmp_path, ["tool-br"]) is None


def test_catalog_commands_reads_the_heads_of_shell_fences() -> None:
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
    instructions = "\n".join(["```sh", ".scripts/wired_or_deleted.py --check", "```"])

    assert usage.catalog_commands([instructions]) == frozenset({"wired_or_deleted.py"})
