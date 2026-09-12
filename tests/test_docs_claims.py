from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from tests.doc_blocks import block_body, cells

REPO = Path(__file__).resolve().parents[1]
ARCHITECTURE_MD = "docs/architecture/architecture.md"
CLI_MD = "docs/reference/cli.md"
SKILLS_README = ".basicly/core/skills/README.md"
HOOKS_README = ".basicly/core/hooks/README.md"


def _load_module():
    script_path = REPO / ".scripts" / "docs_claims.py"
    spec = importlib.util.spec_from_file_location("docs_claims", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


claims = _load_module()


def _run(root: Path, mode: str) -> int:
    return claims.main([mode, "--root", str(root)])


def test_check_passes_on_the_committed_tree(capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(REPO, "--check") == 0
    captured = capsys.readouterr()
    assert captured.err == "", captured.err
    assert "current" in captured.out


def test_check_names_the_block_and_file_when_a_generated_block_drifts(
    work_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    path = work_repo / ARCHITECTURE_MD
    text = path.read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if "`AGENTS.md` (codex)" in line)
    path.write_text(text.replace(row, row.replace("|", "| 99999 |", 1)), encoding="utf-8")

    assert _run(work_repo, "--check") == 1
    err = capsys.readouterr().err
    assert ARCHITECTURE_MD in err
    assert "[always-on-sizes]" in err


def test_fix_regenerates_a_drifted_block_and_the_check_then_passes(work_repo: Path) -> None:
    path = work_repo / SKILLS_README
    original = path.read_text(encoding="utf-8")
    path.write_text(original.replace("| `tool-jq` |", "| `tool-nope` |"), encoding="utf-8")
    assert _run(work_repo, "--check") == 1

    assert _run(work_repo, "--fix") == 0
    assert path.read_text(encoding="utf-8") == original
    assert _run(work_repo, "--check") == 0


def test_fix_scoped_to_one_block_leaves_the_other_documents_untouched(work_repo: Path) -> None:

    skills = work_repo / SKILLS_README
    hooks = work_repo / claims.HOOKS_README
    current = skills.read_text(encoding="utf-8")
    skills.write_text(current.replace("| `tool-jq` |", "| `tool-nope` |"), encoding="utf-8")
    drifted = hooks.read_text(encoding="utf-8").replace(
        "| `pre-push-script` |", "| `pre-push-nope` |"
    )
    hooks.write_text(drifted, encoding="utf-8")

    assert claims.main(["--fix", "--block", "catalog-skills", "--root", str(work_repo)]) == 0

    assert skills.read_text(encoding="utf-8") == current
    assert hooks.read_text(encoding="utf-8") == drifted


def test_an_unknown_block_name_is_refused_rather_than_checking_nothing(work_repo: Path) -> None:
    with pytest.raises(SystemExit):
        claims.main(["--check", "--block", "plan-currrent-state", "--root", str(work_repo)])


def test_check_reports_a_missing_marker_pair_instead_of_skipping_it(
    work_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = work_repo / HOOKS_README
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("<!-- docs-claims:begin catalog-hooks -->", ""), encoding="utf-8")

    assert _run(work_repo, "--check") == 1
    assert "[catalog-hooks]" in capsys.readouterr().err


def test_fix_preserves_a_crlf_file_line_by_line(work_repo: Path) -> None:

    path = work_repo / HOOKS_README
    crlf = path.read_text(encoding="utf-8").replace("\n", "\r\n")
    path.write_bytes(crlf.replace("`pre-push`", "`stale-stage`").encode("utf-8"))

    assert _run(work_repo, "--fix") == 0
    repaired = path.read_bytes()
    assert b"\r\n" in repaired
    assert repaired.replace(b"\r\n", b"\n").count(b"\n") == crlf.count("\r\n")


def test_always_on_table_measures_each_surface_against_its_target_cap() -> None:

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
    rows = block_body((REPO / SKILLS_README).read_text(encoding="utf-8"), "catalog-skills")
    named = {cells(row)[0].strip("`") for row in rows if row.startswith("| `")}
    on_disk = {
        source.parent.name for source in (REPO / ".basicly/core/skills").glob("*/skill.yaml")
    }

    assert named == on_disk


def test_hooks_readme_names_exactly_the_hooks_in_the_manifest() -> None:
    hooks_dir = REPO / ".basicly" / "core" / "hooks"
    rows = block_body((hooks_dir / "README.md").read_text(encoding="utf-8"), "catalog-hooks")
    named = {cells(row)[0].strip("`") for row in rows if row.startswith("| `")}
    manifest = yaml.safe_load((hooks_dir / "hooks.yaml").read_text(encoding="utf-8"))["hooks"]

    assert named == {hook["id"] for hook in manifest}
    assert all((hooks_dir / hook["script"]).exists() for hook in manifest)


def test_check_fails_when_a_shipped_subcommand_leaves_the_command_tables(
    work_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = work_repo / CLI_MD
    text = path.read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith("| `basicly decompose`"))
    path.write_text(text.replace(f"{row}\n", ""), encoding="utf-8")

    assert _run(work_repo, "--check") == 1
    err = capsys.readouterr().err
    assert "[cli-commands]" in err
    assert "decompose" in err


def test_the_command_tables_cover_every_registered_subcommand() -> None:
    assert claims._cli_commands_covered(REPO) == []


def test_the_command_tables_cover_every_subcommand_of_every_group() -> None:
    assert claims._cli_subcommands_covered(REPO) == []


@pytest.mark.parametrize(
    ("fragment", "parent", "dropped"),
    [
        (
            r"\|merge-queue",
            "worktree",
            "merge-queue",
        ),
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
    path = work_repo / CLI_MD
    text = path.read_text(encoding="utf-8")
    assert fragment in text, "the fixture no longer matches the document it mutates"
    path.write_text(text.replace(fragment, "", 1), encoding="utf-8")

    assert _run(work_repo, "--check") == 1
    err = capsys.readouterr().err
    assert "[cli-subcommands]" in err
    assert f"'{parent}' subcommands missing" in err
    assert dropped in err


def test_fix_cannot_repair_a_missing_subcommand_and_says_so(work_repo: Path) -> None:

    path = work_repo / CLI_MD
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace(r"\|bg-isolation", "", 1), encoding="utf-8")

    assert _run(work_repo, "--fix") == 1


def test_the_work_tracker_skill_states_the_engines_own_work_types() -> None:
    assert claims.work_types.skill_work_types(REPO) == []


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("`feature`, `task`;", "`feature`, `task`, `docs`, `question`;", "config.WORK_TYPES"),
        ("`chore`, `task`; `epic`", "`chore`, `task`, `feature`; `epic`", "loop._LEAF_TYPES"),
    ],
)
def test_check_fails_when_the_skill_states_a_type_the_engine_rejects(
    work_repo: Path, capsys: pytest.CaptureFixture[str], old: str, new: str, expected: str
) -> None:
    path = work_repo / ".basicly/core/skills/work-tracker/skill.yaml"
    text = path.read_text(encoding="utf-8")
    assert old in text, "the fixture no longer matches the skill it mutates"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")

    assert _run(work_repo, "--check") == 1
    err = capsys.readouterr().err
    assert "[skill-work-types]" in err
    assert expected in err


def test_the_claim_follows_a_renamed_skill_source_instead_of_a_path(work_repo: Path) -> None:

    skills = work_repo / ".basicly/core/skills"
    (skills / "work-tracker").rename(skills / "work-ledger")
    assert claims.work_types.skill_work_types(work_repo) == []

    source = skills / "work-ledger" / "skill.yaml"
    stated = source.read_text(encoding="utf-8")
    source.write_text(
        stated.replace("`feature`, `task`;", "`feature`, `docs`;", 1), encoding="utf-8"
    )

    assert claims.work_types.skill_work_types(work_repo) != []


def test_prose_reworded_past_the_anchor_fails_loudly_rather_than_asserting_nothing(
    work_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    path = work_repo / ".basicly/core/skills/work-tracker/skill.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("the leaf types", "the buildable kinds", 1), encoding="utf-8")

    assert _run(work_repo, "--check") == 1
    err = capsys.readouterr().err
    assert "[skill-work-types]" in err
    assert "anchor 'leaf types' not found" in err
