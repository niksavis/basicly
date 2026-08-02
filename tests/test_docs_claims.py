"""Tests for the derivable-documentation gate (basicly-tcmy.31).

The gate exists because correcting a stale doc figure by hand fixes today's copy
and guarantees tomorrow's drift. So the tests that matter are the ones a
*disabled* generation step would fail — a gate that cannot tell a drifted tree
from a current one is indistinguishable from no gate at all, which is this repo's
own named defect class.

``test_check_names_the_block_and_file_when_a_generated_block_drifts`` is that
control: stub ``_always_on_sizes`` (or any renderer) to return the bytes already
in the file and it goes red. Its siblings cover the other direction — ``--fix``
repairs the same drift with no hand editing, and the committed tree passes.

Every mutation runs against a per-test copy of the repo passed through ``--root``,
so nothing here writes to the checkout it is testing.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
ARCHITECTURE_MD = "docs/architecture/architecture.md"
SKILLS_README = ".basicly/core/skills/README.md"
HOOKS_README = ".basicly/core/hooks/README.md"


def _load_module():
    """Load the docs-claims script module from its path (it is not a package)."""
    script_path = REPO / ".scripts" / "docs_claims.py"
    spec = importlib.util.spec_from_file_location("docs_claims", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


claims = _load_module()


@pytest.fixture
def work_repo(tmp_path: Path) -> Path:
    """An isolated copy of the repo, so a mutation never touches real repo state."""
    work = tmp_path / "repo"
    shutil.copytree(REPO, work, ignore=shutil.ignore_patterns(".git", ".venv", "node_modules"))
    return work


def _run(root: Path, mode: str) -> int:
    """Invoke the script's entry point against *root*."""
    return claims.main([mode, "--root", str(root)])


def _block_body(text: str, name: str) -> list[str]:
    """The lines strictly between a marker pair, stripped of their indentation."""
    lines = text.splitlines()
    begin = next(i for i, line in enumerate(lines) if f"docs-claims:begin {name}" in line)
    end = next(i for i, line in enumerate(lines) if f"docs-claims:end {name}" in line)
    return [line.strip() for line in lines[begin + 1 : end] if line.strip()]


def _cells(row: str) -> list[str]:
    """The content cells of a markdown table row."""
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


# --------------------------------------------------------------- the gate binds


def test_check_passes_on_the_committed_tree(capsys: pytest.CaptureFixture[str]) -> None:
    """The positive control: every claim in the checkout is current."""
    assert _run(REPO, "--check") == 0
    captured = capsys.readouterr()
    assert captured.err == "", captured.err
    assert "current" in captured.out


def test_check_names_the_block_and_file_when_a_generated_block_drifts(
    work_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Drift in a generated block fails the check, naming both the block and the file.

    This is the gate's own tripwire: a generation step that returned the file's
    existing bytes instead of rendering from the tree would pass here, and this
    test is what refuses that.
    """
    path = work_repo / ARCHITECTURE_MD
    text = path.read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if "`AGENTS.md` (codex)" in line)
    path.write_text(text.replace(row, row.replace("|", "| 99999 |", 1)), encoding="utf-8")

    assert _run(work_repo, "--check") == 1
    err = capsys.readouterr().err
    assert ARCHITECTURE_MD in err
    assert "[always-on-sizes]" in err


def test_fix_regenerates_a_drifted_block_and_the_check_then_passes(work_repo: Path) -> None:
    """The same drift, repaired mechanically: no hand editing, and the check goes green."""
    path = work_repo / SKILLS_README
    original = path.read_text(encoding="utf-8")
    path.write_text(original.replace("| `tool-jq` |", "| `tool-nope` |"), encoding="utf-8")
    assert _run(work_repo, "--check") == 1

    assert _run(work_repo, "--fix") == 0
    assert path.read_text(encoding="utf-8") == original
    assert _run(work_repo, "--check") == 0


def test_check_reports_a_missing_marker_pair_instead_of_skipping_it(
    work_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A block whose markers were deleted is a loud failure, never a silent pass."""
    path = work_repo / HOOKS_README
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("<!-- docs-claims:begin catalog-hooks -->", ""), encoding="utf-8")

    assert _run(work_repo, "--check") == 1
    assert "[catalog-hooks]" in capsys.readouterr().err


def test_fix_preserves_a_crlf_file_line_by_line(work_repo: Path) -> None:
    """Repairing one block must not rewrite every other line of a CRLF checkout.

    The line ending is test *data*, not a property of the host, so this asserts the
    same thing on all three platforms.
    """
    path = work_repo / HOOKS_README
    crlf = path.read_text(encoding="utf-8").replace("\n", "\r\n")
    path.write_bytes(crlf.replace("`pre-push`", "`stale-stage`").encode("utf-8"))

    assert _run(work_repo, "--fix") == 0
    repaired = path.read_bytes()
    assert b"\r\n" in repaired
    assert repaired.replace(b"\r\n", b"\n").count(b"\n") == crlf.count("\r\n")


# ------------------------------------------------------------- the claims hold


def test_always_on_table_measures_each_surface_against_its_target_cap() -> None:
    """Each row's chars/cap/headroom are the real file length and the target's own cap.

    Read out of the committed table rather than out of the renderer: the point is
    that the *document* carries the measurement, not that the function is
    self-consistent.
    """
    rows = _block_body((REPO / ARCHITECTURE_MD).read_text(encoding="utf-8"), "always-on-sizes")
    surfaces = [_cells(row) for row in rows if row.startswith("| `")]
    assert surfaces, "the always-on block rendered no surface rows"

    caps = {
        target["name"]: target["max_size_warning"]
        for path in (REPO / ".basicly" / "core" / "targets").glob("*.yaml")
        for target in [yaml.safe_load(path.read_text(encoding="utf-8"))]
    }
    for surface, chars, cap, headroom in surfaces:
        path, _, target = surface.partition(" ")
        measured = len((REPO / path.strip("`")).read_text(encoding="utf-8"))
        assert int(chars) == measured, f"{surface}: table says {chars}, file is {measured}"
        assert int(cap) == caps[target.strip("()")]
        assert int(headroom) == int(cap) - int(chars)


def test_skills_readme_names_exactly_the_skill_sources_on_disk() -> None:
    """Both directions: no skill named that does not exist, none on disk unnamed."""
    rows = _block_body((REPO / SKILLS_README).read_text(encoding="utf-8"), "catalog-skills")
    named = {_cells(row)[0].strip("`") for row in rows if row.startswith("| `")}
    on_disk = {
        source.parent.name for source in (REPO / ".basicly/core/skills").glob("*/skill.yaml")
    }

    assert named == on_disk


def test_hooks_readme_names_exactly_the_hooks_in_the_manifest() -> None:
    """Both directions against ``hooks.yaml``, and every script it points at exists."""
    hooks_dir = REPO / ".basicly" / "core" / "hooks"
    rows = _block_body((hooks_dir / "README.md").read_text(encoding="utf-8"), "catalog-hooks")
    named = {_cells(row)[0].strip("`") for row in rows if row.startswith("| `")}
    manifest = yaml.safe_load((hooks_dir / "hooks.yaml").read_text(encoding="utf-8"))["hooks"]

    assert named == {hook["id"] for hook in manifest}
    assert all((hooks_dir / hook["script"]).exists() for hook in manifest)


def test_check_fails_when_a_shipped_subcommand_leaves_the_command_tables(
    work_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An omitted subcommand fails the gate and is named in the failure."""
    path = work_repo / ARCHITECTURE_MD
    text = path.read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith("| `basicly decompose`"))
    path.write_text(text.replace(f"{row}\n", ""), encoding="utf-8")

    assert _run(work_repo, "--check") == 1
    err = capsys.readouterr().err
    assert "[cli-commands]" in err
    assert "decompose" in err


def test_the_command_tables_cover_every_registered_subcommand() -> None:
    """The positive half of the same claim, on the committed tree."""
    assert claims._cli_commands_covered(REPO) == []
