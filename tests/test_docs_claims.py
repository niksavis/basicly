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

The boundary is the gate's behaviour against the *content* of any one document it
generates into: ``test_plan_claims`` owns the implementation plan's own figures.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from tests.doc_blocks import block_body, cells

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


def test_fix_scoped_to_one_block_leaves_the_other_documents_untouched(work_repo: Path) -> None:
    """What the landing rebuild needs (basicly-3w51): repair the conflicted path only.

    The merge queue runs this against a stopped rebase, so a run that also rewrote a
    second document would leave it modified outside the conflict it was resolving.
    """
    plan = work_repo / IMPLEMENTATION_PLAN
    skills = work_repo / SKILLS_README
    current = plan.read_text(encoding="utf-8")
    plan.write_text(current.replace("| Test files |", "| Test files (stale) |"), encoding="utf-8")
    drifted = skills.read_text(encoding="utf-8").replace("| `tool-jq` |", "| `tool-nope` |")
    skills.write_text(drifted, encoding="utf-8")

    assert claims.main(["--fix", "--block", "plan-current-state", "--root", str(work_repo)]) == 0

    assert plan.read_text(encoding="utf-8") == current
    assert skills.read_text(encoding="utf-8") == drifted


def test_an_unknown_block_name_is_refused_rather_than_checking_nothing(work_repo: Path) -> None:
    """A typo'd name would otherwise select no block and report every claim current."""
    with pytest.raises(SystemExit):
        claims.main(["--check", "--block", "plan-currrent-state", "--root", str(work_repo)])


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
    rows = block_body((REPO / ARCHITECTURE_MD).read_text(encoding="utf-8"), "always-on-sizes")
    surfaces = [cells(row) for row in rows if row.startswith("| `")]
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
    rows = block_body((REPO / SKILLS_README).read_text(encoding="utf-8"), "catalog-skills")
    named = {cells(row)[0].strip("`") for row in rows if row.startswith("| `")}
    on_disk = {
        source.parent.name for source in (REPO / ".basicly/core/skills").glob("*/skill.yaml")
    }

    assert named == on_disk


def test_hooks_readme_names_exactly_the_hooks_in_the_manifest() -> None:
    """Both directions against ``hooks.yaml``, and every script it points at exists."""
    hooks_dir = REPO / ".basicly" / "core" / "hooks"
    rows = block_body((hooks_dir / "README.md").read_text(encoding="utf-8"), "catalog-hooks")
    named = {cells(row)[0].strip("`") for row in rows if row.startswith("| `")}
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
            r"\|merge-queue",
            "worktree",
            "merge-queue",
        ),
        # `merge` is a prefix of `merge-queue`. A plain substring test would credit
        # the missing one to the surviving one and report a clean tree.
        (
            r"merge\|",
            "worktree",
            "merge",
        ),
        (
            r"watch\|",
            "loop",
            "watch",
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
    path.write_text(text.replace(r"\|bg-isolation", "", 1), encoding="utf-8")

    assert _run(work_repo, "--fix") == 1


# ------------------------------------------------------- skill work types (tcmy.9)


def test_the_work_tracker_skill_states_the_engines_own_work_types() -> None:
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
    path = work_repo / ".basicly/core/skills/work-tracker/skill.yaml"
    text = path.read_text(encoding="utf-8")
    assert old in text, "the fixture no longer matches the skill it mutates"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")

    assert _run(work_repo, "--check") == 1
    err = capsys.readouterr().err
    assert "[skill-work-types]" in err
    assert expected in err


def test_the_claim_follows_a_renamed_skill_source_instead_of_a_path(work_repo: Path) -> None:
    """The gate keys on the claim's own words, so a rename cannot turn it red.

    The source moved once already — `tool-br` to `work-tracker` (basicly-vkh0.42.3) —
    and the path literal here named a file the rename had deleted, so the rename could
    not land without editing the script (basicly-vkh0.42.9). The second half is what
    stops the repair being "stop checking": renamed *and* drifted still fails.
    """
    skills = work_repo / ".basicly/core/skills"
    (skills / "work-tracker").rename(skills / "work-ledger")
    assert claims._skill_work_types(work_repo) == []

    source = skills / "work-ledger" / "skill.yaml"
    stated = source.read_text(encoding="utf-8")
    source.write_text(
        stated.replace("`feature`, `task`;", "`feature`, `docs`;", 1), encoding="utf-8"
    )

    assert claims._skill_work_types(work_repo) != []


def test_prose_reworded_past_the_anchor_fails_loudly_rather_than_asserting_nothing(
    work_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A vanished anchor must raise, not silently check an empty list.

    This is the failure mode that makes a checker worse than none: reword the
    sentence, the anchor stops matching, and the gate reports a clean tree forever.
    """
    path = work_repo / ".basicly/core/skills/work-tracker/skill.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("the leaf types", "the buildable kinds", 1), encoding="utf-8")

    assert _run(work_repo, "--check") == 1
    err = capsys.readouterr().err
    assert "[skill-work-types]" in err
    assert "anchor 'leaf types' not found" in err
