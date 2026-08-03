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
import re
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
ARCHITECTURE_MD = "docs/architecture/architecture.md"
SKILLS_README = ".basicly/core/skills/README.md"
HOOKS_README = ".basicly/core/hooks/README.md"
IMPLEMENTATION_PLAN = "docs/plan/implementation-plan.md"


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


# ------------------------------------------------- the plan's own figures (uhiq.1)


def test_plan_current_state_matches_the_tree_it_claims_to_measure() -> None:
    """Every row of the plan's generated block is the real measurement.

    Read out of the committed document rather than out of the renderer: the claim is
    that the *plan* carries the number, not that the function agrees with itself.
    """
    rows = _block_body(
        (REPO / IMPLEMENTATION_PLAN).read_text(encoding="utf-8"), "plan-current-state"
    )
    stated = {
        _cells(row)[0]: _cells(row)[1] for row in rows if row.startswith("| ") and "---" not in row
    }
    stated.pop("Measure", None)

    modules = len(sorted((REPO / "src" / "basicly").glob("*.py")))
    test_files = sorted((REPO / "tests").glob("test_*.py"))
    assert stated["Engine modules (`src/basicly/*.py`)"] == str(modules)
    assert stated["Test files"] == str(len(test_files))

    checks = tomllib.loads((REPO / "basicly.toml").read_text(encoding="utf-8"))["verify"]["checks"]
    assert stated["`[[verify.checks]]` declared"] == str(len(checks))
    for mode in ("fast", "full", "staged"):
        expected = sum(1 for check in checks if mode in (check.get("modes") or []))
        assert stated[f"…of which run in `--mode {mode}`"] == str(expected)


def test_the_plan_states_no_verify_check_count_outside_the_generated_block() -> None:
    """A hand-written check count is wrong for at least one mode, so there may be none.

    The plan stated one fixed number for `verify --mode full` and a different one for
    what the config declares. Both were wrong, and no single sentence could have been
    right: the count is per-mode. That is the stale claim basicly-uhiq.1 removed.
    """
    text = (REPO / IMPLEMENTATION_PLAN).read_text(encoding="utf-8")
    body = (
        text.split("<!-- docs-claims:begin plan-current-state -->")[0]
        + text.split("<!-- docs-claims:end plan-current-state -->")[1]
    )

    offenders = re.findall(r"\b(?:an? )?(\w+)-check `?verify", body)
    assert offenders == [], f"hand-written verify check count outside the block: {offenders}"


def test_the_plan_indexes_every_document_that_survives_under_docs() -> None:
    """The plan is the index that makes "delete a fulfilled document" enforceable.

    Owner rule 2026-08-02: a document not listed here should not exist. So an
    unlisted document is either a plan defect or a deletion nobody performed, and
    both need a human — hence a test rather than prose.
    """
    plan = (REPO / IMPLEMENTATION_PLAN).read_text(encoding="utf-8")
    on_disk = {
        path.relative_to(REPO / "docs").as_posix()
        for path in (REPO / "docs").rglob("*.md")
        if path.name != "implementation-plan.md"
    }
    unlisted = sorted(name for name in on_disk if name.rsplit("/", 1)[-1] not in plan)

    assert unlisted == [], (
        f"documents under docs/ that the plan does not name — index them or delete them: {unlisted}"
    )


# ------------------------------------------------- nested subcommands (tcmy.9)


def test_the_command_tables_cover_every_subcommand_of_every_group() -> None:
    """The positive control for the nested claim, on the committed tree."""
    assert claims._cli_subcommands_covered(REPO) == []


@pytest.mark.parametrize(
    ("fragment", "parent", "dropped"),
    [
        # The defect tcmy.9 was filed for: `worktree ...` satisfied the top-level
        # claim while three of its six subcommands were undocumented.
        (
            " and `merge-queue` lands several serially in the given topological order;",
            "worktree",
            "merge-queue",
        ),
        # `merge` is a prefix of `merge-queue`. A plain substring test would credit
        # the missing one to the surviving one and report a clean tree.
        (
            "`merge` lands one finished worktree on its base (rebase, re-verify, `--no-ff`) and ",
            "worktree",
            "merge",
        ),
        (
            "`preflight` (read-only: clean base, live worktrees, runner, grant, budget, "
            "per-lane band table and forecast spend) and then ",
            "loop",
            "preflight",
        ),
    ],
)
def test_check_fails_when_a_group_stops_documenting_one_of_its_subcommands(
    work_repo: Path,
    capsys: pytest.CaptureFixture[str],
    fragment: str,
    parent: str,
    dropped: str,
) -> None:
    """A known-bad control per case, so a green sweep cannot be an empty one."""
    path = work_repo / ARCHITECTURE_MD
    text = path.read_text(encoding="utf-8")
    assert fragment in text, "the fixture no longer matches the document it mutates"
    path.write_text(text.replace(fragment, "", 1), encoding="utf-8")

    assert _run(work_repo, "--check") == 1
    err = capsys.readouterr().err
    assert "[cli-subcommands]" in err
    assert f"'{parent}' subcommands missing" in err
    assert dropped in err


def test_fix_cannot_repair_a_missing_subcommand_and_says_so(work_repo: Path) -> None:
    """``--fix`` must exit non-zero on a claim it cannot write.

    A ``fix_command`` that exited 0 here would leave the pre-commit fast set green
    on a real omission — a fail-open gate built by the gate that closes one.
    """
    path = work_repo / ARCHITECTURE_MD
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("`bg-isolation` sets Claude's", "sets Claude's", 1), "utf-8")

    assert _run(work_repo, "--fix") == 1


# ------------------------------------------------------- skill work types (tcmy.9)


def test_the_tool_br_skill_states_the_engines_own_work_types() -> None:
    """The positive control: both lists in the shipped skill match the engine."""
    assert claims._skill_work_types(REPO) == []


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        # The exact wording that shipped for months: `docs` and `question` are
        # rejected by `classify`, so a bead filed as either could never advance.
        ("`feature`, `task`;", "`feature`, `task`, `docs`, `question`;", "config.WORK_TYPES"),
        # A leaf list that quietly grows past what `loop` will build. The sentence
        # wraps in the YAML block scalar, so the fixture matches its second line.
        ("`chore`, `task`; `epic`", "`chore`, `task`, `feature`; `epic`", "loop._LEAF_TYPES"),
    ],
)
def test_check_fails_when_the_skill_states_a_type_the_engine_rejects(
    work_repo: Path, capsys: pytest.CaptureFixture[str], old: str, new: str, expected: str
) -> None:
    """Each list is checked against its own source of truth, and named on failure."""
    path = work_repo / ".basicly/core/skills/tool-br/skill.yaml"
    text = path.read_text(encoding="utf-8")
    assert old in text, "the fixture no longer matches the skill it mutates"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")

    assert _run(work_repo, "--check") == 1
    err = capsys.readouterr().err
    assert "[skill-work-types]" in err
    assert expected in err


def test_prose_reworded_past_the_anchor_fails_loudly_rather_than_asserting_nothing(
    work_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A vanished anchor must raise, not silently check an empty list.

    This is the failure mode that makes a checker worse than none: reword the
    sentence, the anchor stops matching, and the gate reports a clean tree forever.
    """
    path = work_repo / ".basicly/core/skills/tool-br/skill.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("the leaf types", "the buildable kinds", 1), encoding="utf-8")

    assert _run(work_repo, "--check") == 1
    err = capsys.readouterr().err
    assert "[skill-work-types]" in err
    assert "anchor 'leaf types' not found" in err
