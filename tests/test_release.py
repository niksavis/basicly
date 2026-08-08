"""Release automation tests (basicly-kjc5.12).

The fixture is a **real** git repo — the whole feature is about git state (a clean
tree, an existing tag, a commit, an annotated tag), and a stubbed git cannot
disagree with itself about any of that. The two thin subprocess wrappers over
already-tested tools (`basicly build`, the changelog generator) are substituted
where a test is not about them, and exercised directly where it is.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from basicly import capability_proof, release, tracker_usage, usage, verify
from basicly import commit as commit_mod
from basicly import policy as policy_mod
from basicly.capability_proof import CAPABILITY_VERIFY_CHECK
from basicly.config import PolicyConfig

CURRENT = "0.5.1"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A minimal repo carrying every file a release touches, plus a previous tag."""
    root = tmp_path / "repo"
    (root / "src" / "basicly").mkdir(parents=True)
    (root / "site").mkdir()
    (root / ".scripts").mkdir()
    (root / "src" / "basicly" / "__init__.py").write_text(
        f'"""basicly."""\n\n__version__ = "{CURRENT}"\n', encoding="utf-8"
    )
    (root / "README.md").write_text(
        f"Install with `basicly@v{CURRENT}`.\nPin `@v{CURRENT}` for reproducible installs.\n",
        encoding="utf-8",
    )
    (root / "site" / "index.html").write_text(
        f"<pre>uvx --from git+https://x/basicly@v{CURRENT} basicly install</pre>\n",
        encoding="utf-8",
    )
    (root / ".scripts" / "bootstrap.sh").write_text(
        f"# curl ... | sh -s -- --ref v{CURRENT} --technologies python\n", encoding="utf-8"
    )
    (root / ".scripts" / "bootstrap.ps1").write_text(
        f"# ./bootstrap.ps1 -Ref v{CURRENT}\n", encoding="utf-8"
    )
    (root / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n", encoding="utf-8")
    # A stand-in generator: the real one has its own suite (test_release_changelog),
    # so what matters here is that release invokes it with the tag and date.
    (root / release.CHANGELOG_SCRIPT).write_text(
        "import sys\n"
        "tag = sys.argv[sys.argv.index('--tag') + 1]\n"
        "date = sys.argv[sys.argv.index('--date') + 1]\n"
        "from pathlib import Path\n"
        "p = Path('CHANGELOG.md')\n"
        "p.write_text(p.read_text() + f'\\n## {tag} - {date}\\n')\n",
        encoding="utf-8",
    )
    (root / ".beads").mkdir()
    (root / ".beads" / "issues.jsonl").write_text(
        '{"id": "fx-1", "title": "release track"}\n', encoding="utf-8"
    )
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: seed")
    _git(root, "tag", "-a", f"v{CURRENT}", "-m", f"v{CURRENT}")
    return root


@pytest.fixture
def no_regen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the projection rebuild: the fixture has no catalog to project."""
    monkeypatch.setattr(release, "_regenerate", lambda _root: None)


def test_plan_reports_the_tag_the_date_and_every_pin_site(repo: Path) -> None:
    """The plan is the whole diff, computed before anything is written."""
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    assert (plan.current_version, plan.version) == (CURRENT, "0.6.0")
    assert (plan.current_tag, plan.tag) == ("v0.5.1", "v0.6.0")
    assert plan.date == "2026-07-26"
    assert {site.path.as_posix(): site.occurrences for site in plan.pins} == {
        "README.md": 2,
        "site/index.html": 1,
        ".scripts/bootstrap.sh": 1,
        ".scripts/bootstrap.ps1": 1,
    }


def test_plan_refuses_a_version_that_is_not_semver(repo: Path) -> None:
    """A malformed version must fail before it can reach a tag name."""
    with pytest.raises(SystemExit, match=r"version must be X\.Y\.Z"):
        release.plan_release(repo, "0.6", date="2026-07-26")


def test_a_dry_run_reports_every_step_and_writes_nothing(repo: Path) -> None:
    """The pre-flight has to be the same checks, not a different code path."""
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    result = release.run_release(repo, plan, issue_id="fx-1", dry_run=True)

    assert not result.refused and not result.tagged and result.dry_run
    assert any("0.5.1 -> 0.6.0" in step for step in result.steps)
    assert any("README.md (2)" in step for step in result.steps)
    assert any("## v0.6.0 - 2026-07-26" in step for step in result.steps)
    # Nothing moved: same version, clean tree, no new tag.
    assert release.read_version(repo) == CURRENT
    assert _git(repo, "status", "--porcelain").strip() == ""
    assert "v0.6.0" not in _git(repo, "tag", "--list")


@pytest.mark.usefixtures("no_regen")
def test_a_release_produces_the_version_the_changelog_and_an_annotated_tag(repo: Path) -> None:
    """The three artefacts the acceptance criterion names, in one real run."""
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    result = release.run_release(repo, plan, issue_id="fx-1")

    assert not result.refused and result.tagged
    assert release.read_version(repo) == "0.6.0"
    assert "## v0.6.0 - 2026-07-26" in (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    # Pins rewritten everywhere, with no stale tag left behind.
    for rel in release.PIN_FILES:
        text = (repo / rel).read_text(encoding="utf-8")
        assert "v0.6.0" in text and f"v{CURRENT}" not in text
    # An *annotated* tag (a lightweight one has no tag object to cat-file).
    assert _git(repo, "cat-file", "-t", "v0.6.0").strip() == "tag"
    assert "v0.6.0 (2026-07-26)" in _git(repo, "cat-file", "-p", "v0.6.0")
    # One commit, carrying the issue id the commit-msg hook requires.
    assert _git(repo, "log", "--format=%s", "-1").strip() == release.COMMIT_SUBJECT
    assert "fx-1" in _git(repo, "log", "--format=%B", "-1")
    assert _git(repo, "status", "--porcelain").strip() == ""


@pytest.mark.usefixtures("no_regen")
def test_a_release_never_pushes(repo: Path) -> None:
    """The irreversible half stays a human step, and the run says so."""
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")
    calls: list[list[str]] = []
    real_git = release._git

    def spy(root: Path, args: list[str], **kwargs: object):
        calls.append(args)
        return real_git(root, args, **kwargs)  # type: ignore[arg-type]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(release, "_git", spy)
        result = release.run_release(repo, plan, issue_id="fx-1")

    assert not any(args[:1] == ["push"] for args in calls)
    assert any("NOT pushed" in step for step in result.steps)


@pytest.mark.parametrize(
    ("version", "expected"),
    [("0.4.0", "must move forward"), ("0.5.1", "must move forward")],
)
def test_a_version_that_does_not_move_forward_is_refused(
    repo: Path, version: str, expected: str
) -> None:
    """A release that renames the current tree is not a release."""
    plan = release.plan_release(repo, version, date="2026-07-26")

    result = release.run_release(repo, plan, issue_id="fx-1")

    assert result.refused and any(expected in reason for reason in result.refusals)


def test_a_dirty_tree_is_refused_before_anything_is_written(repo: Path) -> None:
    """Never tag a tree nobody can reconstruct from the commit."""
    (repo / "stray.txt").write_text("dirt\n", encoding="utf-8")
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    result = release.run_release(repo, plan, issue_id="fx-1")

    assert result.refused and any("not clean" in reason for reason in result.refusals)
    assert release.read_version(repo) == CURRENT


def test_an_existing_tag_is_refused(repo: Path) -> None:
    """Re-cutting a published tag would move it under consumers who pinned it."""
    _git(repo, "tag", "-a", "v0.6.0", "-m", "v0.6.0")
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    result = release.run_release(repo, plan, issue_id="fx-1")

    assert result.refused and any("already exists" in r for r in result.refusals)


def test_every_refusal_is_reported_from_one_run(repo: Path) -> None:
    """One run names every problem, so a human fixes them in one pass."""
    (repo / "stray.txt").write_text("dirt\n", encoding="utf-8")
    plan = release.plan_release(repo, "0.4.0", date="2026-07-26")

    result = release.run_release(repo, plan, issue_id="fx-1")

    assert len(result.refusals) >= 2


# --- One changelog fragment per lane, assembled at release (basicly-4746) ---
#
# The mechanism only earns its cost if the collision it removes is *impossible* rather
# than detected, so the fan-out test below carries its positive control: three lanes
# editing one `### Fixed` anchor must still conflict, or the harness is proving nothing.


@pytest.fixture
def real_generator(repo: Path) -> Path:
    """Swap the stand-in generator for the real one and commit it.

    Assembly is judged end to end here: what a consumer reads is the *dated* section,
    and the fragments only reach it through the generator's promotion of
    ``[Unreleased]``. A stand-in that skips the promotion would let a broken fold pass.
    """
    source = Path(__file__).resolve().parents[1] / release.CHANGELOG_SCRIPT
    (repo / release.CHANGELOG_SCRIPT).write_text(
        source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: use the real changelog generator")
    return repo


def _write_fragments(repo: Path, bodies: dict[str, str], *, commit: bool = True) -> None:
    """Write ``changelog.d`` entries the way a lane does, and land them on the branch."""
    directory = repo / release.FRAGMENT_DIR
    directory.mkdir(exist_ok=True)
    for name, body in bodies.items():
        (directory / name).write_text(body, encoding="utf-8")
    if commit:
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "feat: lane entries")


def _dated_section(repo: Path, tag: str) -> str:
    """The body of *tag*'s dated section — what the release workflow publishes."""
    text = (repo / release.CHANGELOG_FILE).read_text(encoding="utf-8")
    after = text.split(f"## {tag} - ", 1)[1]
    return after.split("\n## ", 1)[0]


@pytest.mark.usefixtures("no_regen")
def test_a_release_assembles_every_fragment_into_the_dated_section_and_deletes_them(
    real_generator: Path,
) -> None:
    """The acceptance criterion's second half: every fragment publishes, none survives."""
    repo = real_generator
    _write_fragments(
        repo,
        {
            "lane-2.fixed.md": "- second fixed entry\n",
            "lane-1.fixed.md": "- first fixed entry\n",
            "lane-3.added.md": "- an added entry\n",
        },
    )
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    result = release.run_release(repo, plan, issue_id="fx-1")

    assert not result.refused and result.tagged
    section = _dated_section(repo, "v0.6.0")
    for entry in ("- first fixed entry", "- second fixed entry", "- an added entry"):
        assert entry in section
    # Deterministic order: category first (Keep a Changelog's own sequence), then
    # filename — never the directory listing, which differs by filesystem.
    assert section.index("### Added") < section.index("### Fixed")
    assert section.index("- first fixed entry") < section.index("- second fixed entry")
    # One heading per category: a second `### Fixed` is a duplicate-sibling lint
    # failure and a section no reader can scan.
    assert section.count("### Fixed") == 1
    # Consumed, in this run's own commit — a fragment left behind republishes next time.
    assert not list((repo / release.FRAGMENT_DIR).glob("*.md"))
    deleted = _git(repo, "show", "--name-status", "--format=", "HEAD")
    assert deleted.count("D\tchangelog.d/") == 3
    assert _git(repo, "status", "--porcelain").strip() == ""


@pytest.mark.usefixtures("no_regen")
def test_a_curated_unreleased_body_publishes_alongside_the_fragments(
    real_generator: Path,
) -> None:
    """The transition promise: editing the changelog by hand is never broken."""
    repo = real_generator
    (repo / release.CHANGELOG_FILE).write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Fixed\n\n- what a human wrote by hand\n",
        encoding="utf-8",
    )
    _write_fragments(repo, {"lane-9.fixed.md": "- what a lane recorded\n"})
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    result = release.run_release(repo, plan, issue_id="fx-1")

    assert not result.refused
    section = _dated_section(repo, "v0.6.0")
    # The curated section keeps its position and gains the fragment, rather than the
    # fragment opening a second `### Fixed` beside it.
    assert section.count("### Fixed") == 1
    assert section.index("- what a human wrote by hand") < section.index("- what a lane recorded")


def test_fragments_are_ordered_by_category_then_filename(repo: Path) -> None:
    """Ordering is computed, never inherited from the directory listing."""
    _write_fragments(
        repo,
        {
            "zz.added.md": "- z\n",
            "aa.fixed.md": "- a\n",
            "aa.added.md": "- a\n",
            "zz.security.md": "- z\n",
        },
        commit=False,
    )

    fragments, misnamed = release.scan_fragments(repo)

    assert not misnamed
    assert [fragment.path.name for fragment in fragments] == [
        "aa.added.md",
        "zz.added.md",
        "aa.fixed.md",
        "zz.security.md",
    ]


def test_the_fragment_directorys_readme_is_not_a_lane_entry(repo: Path) -> None:
    """The directory documents itself without publishing itself."""
    _write_fragments(
        repo, {"README.md": "# Changelog fragments\n", "aa.fixed.md": "- a\n"}, commit=False
    )

    fragments, misnamed = release.scan_fragments(repo)

    assert not misnamed
    assert [fragment.path.name for fragment in fragments] == ["aa.fixed.md"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("no-category.md", "not named <bead-id>.<category>.md"),
        ("lane-1.improved.md", "not named <bead-id>.<category>.md"),
        (".fixed.md", "not named <bead-id>.<category>.md"),
    ],
)
def test_a_fragment_nothing_can_place_refuses_the_release(
    repo: Path, name: str, expected: str
) -> None:
    """Never tidied away: a dropped fragment is a release note nobody notices is gone."""
    _write_fragments(repo, {name: "- an entry that would vanish\n"})
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    result = release.run_release(repo, plan, issue_id="fx-1")

    assert result.refused and any(expected in reason for reason in result.refusals)
    assert any(name in reason for reason in result.refusals)
    assert release.read_version(repo) == CURRENT


def test_an_empty_fragment_refuses_the_release(repo: Path) -> None:
    """An empty file is a lane that meant to say something; assembling it says nothing."""
    _write_fragments(repo, {"lane-1.fixed.md": "\n\n"})
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    result = release.run_release(repo, plan, issue_id="fx-1")

    assert result.refused and any("is empty" in reason for reason in result.refusals)


def test_a_changelog_with_nowhere_to_fold_the_fragments_is_refused(repo: Path) -> None:
    """Fail closed rather than invent a heading: the promotion reads that body."""
    (repo / release.CHANGELOG_FILE).write_text("# Changelog\n", encoding="utf-8")
    _write_fragments(repo, {"lane-1.fixed.md": "- an entry\n"})
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    result = release.run_release(repo, plan, issue_id="fx-1")

    assert result.refused
    assert any(release.UNRELEASED_HEADING in reason for reason in result.refusals)


def test_a_dry_run_names_the_fragments_in_assembly_order_and_deletes_none(repo: Path) -> None:
    """The pre-flight shows the section's order before the section exists."""
    _write_fragments(repo, {"lane-2.fixed.md": "- b\n", "lane-1.added.md": "- a\n"})
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    result = release.run_release(repo, plan, issue_id="fx-1", dry_run=True)

    assert not result.refused
    assembled = [step for step in result.steps if step.startswith("assemble ")]
    assert assembled == [
        "assemble 2 changelog fragment(s) from changelog.d/ and delete them: "
        "lane-1.added.md, lane-2.fixed.md"
    ]
    assert len(list((repo / release.FRAGMENT_DIR).glob("*.md"))) == 2
    assert _git(repo, "status", "--porcelain").strip() == ""


def _lane(repo: Path, base: str, branch: str, writes: dict[str, str]) -> None:
    """Branch off *base*, write *writes*, commit, and return to *base* — one lane."""
    _git(repo, "checkout", "-q", "-b", branch, base)
    for relative, text in writes.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", f"feat: {branch}")
    _git(repo, "checkout", "-q", base)


def _land_serially(repo: Path, base: str, branches: list[str]) -> list[str]:
    """Rebase then fast-forward each branch in turn; return the ones that conflicted.

    The merge queue's own shape (``merge.merge_worktree`` rebases onto the base and
    merges), reduced to the part this change is about — whether the rebase applies.
    """
    conflicted: list[str] = []
    for branch in branches:
        rebase = subprocess.run(
            ["git", "rebase", base, branch], cwd=repo, capture_output=True, text=True, check=False
        )
        if rebase.returncode != 0:
            conflicted.append(branch)
            subprocess.run(["git", "rebase", "--abort"], cwd=repo, capture_output=True, check=False)
            _git(repo, "checkout", "-q", base)
            continue
        _git(repo, "checkout", "-q", base)
        _git(repo, "merge", "--ff-only", branch)
    return conflicted


def test_three_lanes_each_recording_a_changelog_entry_all_land_without_a_conflict(
    repo: Path,
) -> None:
    """The direct inverse of the run that blocked: three lanes, three files, no anchor.

    Attempt 1 of the unattended pass put three lanes at one `### Fixed` anchor over
    provably disjoint scopes; two landed and the third burned both rework retries on
    the rebase. Here each lane's filename carries its own bead id, so there is no
    shared file left to conflict on.
    """
    for lane in (1, 2, 3):
        _lane(
            repo,
            "main",
            f"harness/lane-{lane}",
            {
                f"changelog.d/lane-{lane}.fixed.md": f"- lane {lane} fixed something\n",
                f"src/basicly/lane_{lane}.py": f'"""Lane {lane}."""\n',
            },
        )

    conflicted = _land_serially(repo, "main", [f"harness/lane-{lane}" for lane in (1, 2, 3)])

    assert conflicted == []
    fragments, misnamed = release.scan_fragments(repo)
    assert not misnamed
    assert [fragment.path.name for fragment in fragments] == [
        "lane-1.fixed.md",
        "lane-2.fixed.md",
        "lane-3.fixed.md",
    ]


def test_the_shared_anchor_the_fragments_replace_does_still_conflict(repo: Path) -> None:
    """The positive control: a harness that never conflicts proves nothing about the fix."""
    anchor = "# Changelog\n\n## [Unreleased]\n\n### Fixed\n\n- an existing entry\n"
    (repo / release.CHANGELOG_FILE).write_text(anchor, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: seed the anchor")
    for lane in (1, 2, 3):
        _lane(
            repo,
            "main",
            f"harness/anchor-{lane}",
            {
                "CHANGELOG.md": anchor.replace(
                    "### Fixed\n\n", f"### Fixed\n\n- lane {lane} fixed something\n"
                ),
                f"src/basicly/anchor_{lane}.py": f'"""Lane {lane}."""\n',
            },
        )

    conflicted = _land_serially(repo, "main", [f"harness/anchor-{lane}" for lane in (1, 2, 3)])

    assert conflicted == ["harness/anchor-2", "harness/anchor-3"]


# --- Exercised-or-unproven: no tag for a capability nothing ever ran (basicly-irrm) ---
#
# The refusal is proved by *planting* an unexercised capability, and each planting test
# carries its positive control: a gate that refuses a planted zero and also refuses an
# exercised one is not reading the ledger at all.


def _declare_check(repo: Path, name: str, command: list[str]) -> None:
    """Declare one `[[verify.checks]]` capability in the fixture, tree left clean."""
    rendered = ", ".join(f'"{arg}"' for arg in command)
    (repo / "basicly.toml").write_text(
        f'[[verify.checks]]\nname = "{name}"\ncommand = [{rendered}]\nmodes = ["full"]\n',
        encoding="utf-8",
    )
    _commit_fixture(repo)


def _record_tool_executions(repo: Path, counts: dict[str, int]) -> None:
    """Plant the `tool-usage` hook's counters, in the shape the hook writes them."""
    _plant_counters(repo, usage.USAGE_FILE, counts)


def _record_check_runs(repo: Path, counts: dict[str, int]) -> None:
    """Plant the verify engine's own ledger, in the shape `run_check` writes it."""
    _plant_counters(repo, usage.VERIFY_CHECKS_FILE, counts)


def _plant_counters(repo: Path, relative: Path, counts: dict[str, int]) -> None:
    counter_file = repo / relative
    counter_file.parent.mkdir(parents=True, exist_ok=True)
    counter_file.write_text(
        json.dumps({
            key: {"count": count, "last_used": "2026-07-26"} for key, count in counts.items()
        }),
        encoding="utf-8",
    )
    _commit_fixture(repo)


def _commit_fixture(repo: Path) -> None:
    """Commit whatever a helper just planted.

    The real `.basicly/usage/` self-ignores, so planting there never dirties this repo;
    the fixture has no such ignore, and an uncommitted file would trip the clean-tree
    refusal and mask the one under test.
    """
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: plant a capability")


@pytest.mark.usefixtures("no_regen")
def test_a_planted_unexercised_capability_refuses_the_release(repo: Path) -> None:
    """The acceptance criterion: a declared capability nothing ever ran blocks the tag."""
    _declare_check(repo, "planted", ["never-run-tool"])
    # A counter created and never incremented is not an execution, so the zero must
    # refuse exactly as an absent key does.
    _record_check_runs(repo, {"other-check": 12, "planted": 0})
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    result = release.run_release(repo, plan, issue_id="fx-1")

    assert result.refused
    assert any(f"{CAPABILITY_VERIFY_CHECK} 'planted'" in r for r in result.refusals)
    # Refused before the first byte: no bump, no tag, nothing to undo.
    assert release.read_version(repo) == CURRENT
    assert _git(repo, "status", "--porcelain").strip() == ""
    assert "v0.6.0" not in _git(repo, "tag", "--list")


def test_an_exercised_capability_does_not_block_the_tag(repo: Path) -> None:
    """The positive control: with an execution recorded, the same shape passes."""
    _declare_check(repo, "planted", ["recorded-tool"])
    _record_check_runs(repo, {"planted": 1})
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    result = release.run_release(repo, plan, issue_id="fx-1", dry_run=True)

    assert not result.refused, result.refusals


def test_a_check_the_engine_ran_and_passed_is_exercised(repo: Path) -> None:
    """The bug (basicly-3yi3): a check verify had just watched pass still blocked the tag.

    End to end through the real runner rather than a planted ledger — the defect was
    precisely that the component executing a check wrote no record anywhere, so a test
    that plants the record cannot see it. `vulture` is declared here for the same reason
    it broke the real release: it exists only as a check, so nothing ever types it.
    """
    interpreter = Path(sys.executable).as_posix()  # POSIX form: TOML would eat backslashes
    _declare_check(repo, "vulture", [interpreter, "-c", ""])

    report = verify.run_verify(repo, "full")
    assert report.passed and [r.name for r in report.results] == ["vulture"]

    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")
    result = release.run_release(repo, plan, issue_id="fx-1", dry_run=True)

    assert not result.refused, result.refusals


def test_a_wrapper_executable_is_not_accepted_as_the_witness(repo: Path) -> None:
    """`uv` running 6091 times says nothing about the check hiding behind it.

    The gate's other failure mode, and the same cause: a witness that counts who typed
    a word stays healthy for a check that was deleted outright.
    """
    _declare_check(repo, "wired-or-deleted", ["uv", "run", "python", ".scripts/x.py"])
    _record_tool_executions(repo, {"uv": 6091, "python": 900})
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    result = release.run_release(repo, plan, issue_id="fx-1", dry_run=True)

    assert result.refused
    assert any(f"{CAPABILITY_VERIFY_CHECK} 'wired-or-deleted'" in r for r in result.refusals)


def test_the_checks_own_binary_running_elsewhere_is_not_a_witness(repo: Path) -> None:
    """Same rule with the strongest rival evidence: the committed tracker ledger.

    `br gate report` recorded there is a real, subcommand-precise execution — of the
    tool, by something that is not this check. Until basicly-3yi3 that passed the gate,
    which is how a declared check could be witnessed by work it never did.
    """
    _declare_check(repo, "tracker-gate", ["br", "gate", "report"])
    (repo / tracker_usage.LEDGER_FILE).parent.mkdir(parents=True, exist_ok=True)
    (repo / tracker_usage.LEDGER_FILE).write_text(
        '{"binary":"br","subcommand":"gate report","site":"engine","ok":true}\n', encoding="utf-8"
    )
    _commit_fixture(repo)
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    result = release.run_release(repo, plan, issue_id="fx-1", dry_run=True)

    assert result.refused
    assert any(f"{CAPABILITY_VERIFY_CHECK} 'tracker-gate'" in r for r in result.refusals)


@pytest.mark.usefixtures("no_regen")
def test_a_declared_capability_with_no_ledger_at_all_is_refused(repo: Path) -> None:
    """Absence of a record is not evidence of an execution, so the gate fails closed."""
    _declare_check(repo, "planted", ["ruff"])
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    result = release.run_release(repo, plan, issue_id="fx-1")

    assert result.refused
    assert any("no execution ledger" in r and "unproven" in r for r in result.refusals)
    assert release.read_version(repo) == CURRENT


def test_a_repo_that_declares_no_capability_is_not_refused(repo: Path) -> None:
    """A consumer with no `[verify]` section published no claim for this gate to hold."""
    assert capability_proof.shipped_capabilities(repo) == ()
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    assert not release.run_release(repo, plan, issue_id="fx-1", dry_run=True).refused


# --- The generated artefacts must survive this repo's own gates ---------------
#
# These exist because the feature first shipped a commit message no gate would
# accept, and 18 green tests said it was fine: the fixture repo installs no hooks,
# so the one string the whole feature must get right was never judged by the thing
# that judges it. Assert against the real validators, not a fixture's opinion.


def test_the_release_commit_subject_passes_the_commit_msg_gate() -> None:
    """A dot in the description is rejected — and a version is full of dots.

    `commit.check_description` is the same authority the hook uses, so this cannot
    drift away from the gate the way a hand-copied rule would.
    """
    scope, description = release.COMMIT_SUBJECT.split(": ", 1)

    commit_mod.check_description(description)  # raises ValueError if a gate refuses
    assert scope.startswith(("chore(", "docs(", "feat(", "fix("))
    assert "." not in description


def test_a_release_refuses_before_writing_when_its_own_subject_would_be_rejected(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-flight owns this, so a bad subject can never strand a bumped tree."""
    monkeypatch.setattr(release, "COMMIT_SUBJECT", "chore(release): prepare v9.9.9")
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    result = release.run_release(repo, plan, issue_id="fx-1")

    assert result.refused
    assert any("commit-msg gate" in reason for reason in result.refusals)
    assert release.read_version(repo) == CURRENT


def test_an_unknown_bead_id_is_refused_before_writing(repo: Path) -> None:
    """The beads gate rejects an unknown id.

    Finding that out at commit time strands the bump, the regeneration and the
    changelog on disk.
    """
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    result = release.run_release(repo, plan, issue_id="fx-nope")

    assert result.refused and any("unknown bead id" in r for r in result.refusals)
    assert release.read_version(repo) == CURRENT


def test_a_bad_date_is_refused_because_the_heading_format_is_load_bearing(repo: Path) -> None:
    """The release workflow keys on `## vX.Y.Z - YYYY-MM-DD` exactly."""
    plan = release.plan_release(repo, "0.6.0", date="not-a-date")

    result = release.run_release(repo, plan, issue_id="fx-1")

    assert result.refused and any("YYYY-MM-DD" in r for r in result.refusals)


def test_a_release_from_a_linked_worktree_is_refused(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tags live in the common git dir, so this would tag unmerged code repo-wide."""
    monkeypatch.setattr(release.worktree, "is_linked_checkout", lambda _root: True)
    monkeypatch.setattr(release.worktree, "current_branch", lambda _root: "harness/x")
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    result = release.run_release(repo, plan, issue_id="fx-1")

    assert result.refused and any("linked worktree" in r for r in result.refusals)


def test_a_failure_after_the_first_write_restores_the_tree(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A half-released repo would push the operator toward `git reset --hard`."""
    monkeypatch.setattr(release, "_regenerate", lambda _root: None)

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("generator exploded")

    monkeypatch.setattr(release, "_write_changelog", boom)
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    with pytest.raises(SystemExit, match="tree was restored"):
        release.run_release(repo, plan, issue_id="fx-1")

    assert release.read_version(repo) == CURRENT
    assert _git(repo, "status", "--porcelain").strip() == ""
    assert "v0.6.0" not in _git(repo, "tag", "--list")


def test_a_stale_pin_left_behind_is_caught_rather_than_shipped(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pin the regex misses would ship a stale install command silently."""
    monkeypatch.setattr(release, "_regenerate", lambda _root: None)
    # A rewrite that does nothing: exactly what an under-matching pattern looks like.
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")
    assert plan.pins, "the plan must find pins for this test to mean anything"
    monkeypatch.setattr(release, "_pin_re", lambda _v: re.compile(r"(?!x)x"))

    with pytest.raises(SystemExit, match="tree was restored"):
        release.run_release(repo, plan, issue_id="fx-1")

    assert _git(repo, "status", "--porcelain").strip() == ""


def test_the_pin_pattern_matches_prose_and_not_a_longer_version() -> None:
    """`v0.5.1` must not match inside `v0.5.10`, but must match before a full stop."""
    pattern = release._pin_re("0.5.1")

    assert pattern.search("pin @v0.5.1 for reproducible installs")
    assert pattern.search("released as v0.5.1.")  # sentence-final period is prose
    assert pattern.search("--ref v0.5.1 --technologies python")
    assert not pattern.search("upgrade to v0.5.10 instead")
    assert not pattern.search("v0.5.11")


# --- D3: autonomous invocation needs a green L3 grant -------------------------


def _patch_grant(
    monkeypatch: pytest.MonkeyPatch,
    level: str | None,
    violations: tuple[str, ...] = (),
    *,
    halted: bool = False,
) -> None:
    grant = None if level is None else release.policy.Grant(level=level, token_budget=1000)
    monkeypatch.setattr(release.policy, "active_grant", lambda _r, _root: grant)
    monkeypatch.setattr(release.policy, "lights_out_violations", lambda *_a, **_k: violations)
    monkeypatch.setattr(
        release.policy,
        "spend_status",
        lambda *_a, **_k: policy_mod.SpendStatus(
            grant=None, spent_tokens=0, halted=halted, detail="ceiling reached" if halted else ""
        ),
    )
    monkeypatch.setattr(
        release.policy,
        "load_policy",
        lambda _r: PolicyConfig(required_gates=("verify",), max_rework=2),
    )


@pytest.mark.parametrize(
    ("level", "expected"),
    [(None, "no active autonomy grant"), ("L1", "is L1"), ("L2", "is L2")],
)
def test_an_autonomous_release_is_refused_below_l3(
    repo: Path, monkeypatch: pytest.MonkeyPatch, level: str | None, expected: str
) -> None:
    """A release reaches every consumer, so nothing short of L3 delegates it."""
    _patch_grant(monkeypatch, level)
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    result = release.run_release(
        repo, plan, issue_id="fx-1", autonomous=True, root_issue="epic", dry_run=True
    )

    assert result.refused and any(expected in reason for reason in result.refusals)


def test_an_autonomous_release_is_refused_when_l3_preconditions_are_not_green(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One escalation anywhere in the session drops the release back to a human."""
    _patch_grant(monkeypatch, "L3", violations=("rework escalation on epic.2 (gate verify: 2/2)",))
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    result = release.run_release(
        repo, plan, issue_id="fx-1", autonomous=True, root_issue="epic", dry_run=True
    )

    assert result.refused
    assert any("preconditions not green" in reason for reason in result.refusals)


def test_an_autonomous_release_needs_a_root_to_check_the_grant_against(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting the root must refuse, never silently skip the D3 check."""
    _patch_grant(monkeypatch, "L3")
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    result = release.run_release(repo, plan, issue_id="fx-1", autonomous=True, dry_run=True)

    assert result.refused and any("session root issue" in r for r in result.refusals)


def test_a_green_l3_grant_permits_an_autonomous_release(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The positive half: L3 with green preconditions is not refused."""
    _patch_grant(monkeypatch, "L3")
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    result = release.run_release(
        repo, plan, issue_id="fx-1", autonomous=True, root_issue="epic", dry_run=True
    )

    assert not result.refused


def test_an_interactive_release_does_not_consult_the_grant(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A human at a terminal is the authorization; the D3 check is for delegation."""
    monkeypatch.setattr(
        release.policy,
        "active_grant",
        lambda *_a: pytest.fail("interactive release must not consult the grant ledger"),
    )
    plan = release.plan_release(repo, "0.6.0", date="2026-07-26")

    assert not release.run_release(repo, plan, issue_id="fx-1", dry_run=True).refused


# --- The regeneration must read the repo being released ------------------------


def test_the_projection_rebuild_forces_the_target_repo_onto_pythonpath(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise the headers are stamped with the *installed* copy's version.

    Found by exercising a release in a clone: the bump landed, every generated
    header still named the previous release, and the run reported success. A fresh
    interpreter is necessary (cli binds __version__ at import) but not sufficient —
    it still imports whichever basicly is installed.
    """
    seen: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object):
        seen["cmd"] = cmd
        seen["env"] = kwargs.get("env")
        seen["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(release.subprocess, "run", fake_run)

    release._regenerate(repo)

    env = seen["env"]
    assert isinstance(env, dict)
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(repo / "src")
    assert seen["cwd"] == repo
