"""Tests for the read-cost measure: what a declared scope costs a lane to read.

Moved out of ``test_decompose`` with the module they cover. These are the cases that
need a tree on disk — every one builds a repo and asserts what
:func:`basicly.read_cost.scope_read_cost` charges for it — while what the estimator
then *does* with the number stays beside the estimator.
"""

from __future__ import annotations

from pathlib import Path

from basicly import read_cost


def _write(repo: Path, rel: str, chars: int) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * chars, encoding="utf-8")


def test_instruction_overhead_tokenizes_agents_md(tmp_path: Path) -> None:
    """Overhead is the projected AGENTS.md at chars/4; absent contributes zero."""
    assert read_cost.instruction_overhead(tmp_path) == 0
    _write(tmp_path, "AGENTS.md", 8_000)
    assert read_cost.instruction_overhead(tmp_path) == 2_000


def test_scope_read_cost_sums_matching_files_once(tmp_path: Path) -> None:
    """Matching files sum at chars/4, deduped across overlapping globs."""
    _write(tmp_path, "src/a.py", 400)
    _write(tmp_path, "src/b.py", 200)
    _write(tmp_path, "docs/c.md", 999)
    cost = read_cost.scope_read_cost(tmp_path, ("src/*.py", "src/a.py"))
    assert cost == (400 + 200) // 4


def test_scope_read_cost_recursive_glob_and_greenfield(tmp_path: Path) -> None:
    """`**` spans directories; a glob matching nothing contributes zero."""
    _write(tmp_path, "src/pkg/deep/mod.py", 800)
    assert read_cost.scope_read_cost(tmp_path, ("src/**/*.py",)) == 200
    assert read_cost.scope_read_cost(tmp_path, ("brand/new/file.py",)) == 0


def test_a_large_file_is_sized_at_what_a_lane_reads_out_of_it(tmp_path: Path) -> None:
    """AC: a small change to a large module is not sized at the whole module.

    The defect basicly-fcls names. A scope of `src/basicly/cli.py` used to cost every
    one of its 45,556 tokens, while the harness's own always-on `tool-usage` guidance
    told the same agent to read only the ranges it needs — and 78% of the `Read` calls
    across 24 measured lanes did exactly that.
    """
    _write(tmp_path, "src/big.py", read_cost.SCOPE_FILE_READ_CAP * 4 * 10)

    cost = read_cost.scope_read_cost(tmp_path, ("src/big.py",))
    assert cost == read_cost.SCOPE_FILE_READ_CAP
    assert cost < read_cost._text_tokens("x" * (read_cost.SCOPE_FILE_READ_CAP * 4 * 10))


def test_a_file_under_the_read_cap_still_costs_all_of_itself(tmp_path: Path) -> None:
    """The cap is a ceiling on one file, never a flat per-file price.

    Below the transition a lane really does read the whole file — the measured median
    fraction is 1.000 for every size band under ~4,000 tokens — so charging the cap
    for a 200-token fragment would over-size the small end as badly as the whole-file
    measure over-sized the large end.
    """
    _write(tmp_path, "src/small.py", 800)
    assert read_cost.scope_read_cost(tmp_path, ("src/small.py",)) == 200


def test_the_read_cap_applies_per_file_so_a_wider_scope_still_costs_more(
    tmp_path: Path,
) -> None:
    """Three large modules must outsize one, or the estimate stops ranking lanes.

    A cap on the *total* would price a lane touching the whole package identically to
    one touching a single module, which is the failure mode that makes a sizing band
    useless rather than merely wrong.
    """
    for name in ("a", "b", "c"):
        _write(tmp_path, f"src/{name}.py", read_cost.SCOPE_FILE_READ_CAP * 4 * 3)

    assert read_cost.scope_read_cost(tmp_path, ("src/a.py",)) == read_cost.SCOPE_FILE_READ_CAP
    assert read_cost.scope_read_cost(tmp_path, ("src/*.py",)) == 3 * read_cost.SCOPE_FILE_READ_CAP


def test_scope_read_cost_keeps_dot_directory_scopes(tmp_path: Path) -> None:
    """A dot-directory glob keeps its leading dot; only a literal ./ prefix strips."""
    _write(tmp_path, ".claude/rules/python.md", 400)
    assert read_cost.scope_read_cost(tmp_path, (".claude/rules/*.md",)) == 100
    assert read_cost.scope_read_cost(tmp_path, ("./.claude/rules/*.md",)) == 100
    _write(tmp_path, "src/a.py", 40)
    assert read_cost.scope_read_cost(tmp_path, ("./src/a.py",)) == 10


def test_scope_read_cost_excludes_dependency_and_cache_trees(tmp_path: Path) -> None:
    """A virtualenv, dependency tree or cache under a glob is not the lane's work."""
    _write(tmp_path, "src/a.py", 40)
    _write(tmp_path, ".venv/lib/dep.py", 4000)
    _write(tmp_path, "node_modules/pkg/index.py", 4000)
    _write(tmp_path, "src/__pycache__/a.cpython-314.pyc", 4000)
    _write(tmp_path, ".git/hooks/thing.py", 4000)
    # Only src/a.py is the project's own file, so the recursive glob costs 10 —
    # not the 4010 the same glob measured before the exclusion.
    assert read_cost.scope_read_cost(tmp_path, ("**/*.py",)) == 10


def test_scope_read_cost_keeps_project_authored_dot_directories(tmp_path: Path) -> None:
    """Exclusion is by directory name, so .basicly and .claude are still counted."""
    _write(tmp_path, ".basicly/core/skills/s/skill.yaml", 400)
    _write(tmp_path, ".claude/rules/python.md", 400)
    assert read_cost.scope_read_cost(tmp_path, (".basicly/**",)) == 100
    assert read_cost.scope_read_cost(tmp_path, (".claude/**",)) == 100


def test_scope_read_cost_reads_a_file_named_like_an_excluded_dir(tmp_path: Path) -> None:
    """The name check covers directories only, so a file called venv is still read."""
    _write(tmp_path, "src/venv", 40)
    assert read_cost.scope_read_cost(tmp_path, ("src/**",)) == 10


def test_scope_read_cost_skips_unglobbable_patterns(tmp_path: Path) -> None:
    """An anchored or engine-rejected pattern is skipped, never fatal."""
    _write(tmp_path, "etc/conf.py", 40)
    # A leading slash is relativized; a drive-anchored pattern must not raise
    # (on POSIX "c:" is an ordinary segment, on Windows the glob engine rejects
    # it and the guard skips it).
    assert read_cost.scope_read_cost(tmp_path, ("/etc/conf.py",)) == 10
    assert read_cost.scope_read_cost(tmp_path, ("c:/nowhere/*.py",)) == 0
