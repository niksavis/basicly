"""Release automation tests (basicly-kjc5.12).

The fixture is a **real** git repo — the whole feature is about git state (a clean
tree, an existing tag, a commit, an annotated tag), and a stubbed git cannot
disagree with itself about any of that. The two thin subprocess wrappers over
already-tested tools (`basicly build`, the changelog generator) are substituted
where a test is not about them, and exercised directly where it is.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from basicly import commit as commit_mod
from basicly import policy as policy_mod
from basicly import release
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
    (root / "docs").mkdir()
    (root / ".scripts").mkdir()
    (root / "src" / "basicly" / "__init__.py").write_text(
        f'"""basicly."""\n\n__version__ = "{CURRENT}"\n', encoding="utf-8"
    )
    (root / "README.md").write_text(
        f"Install with `basicly@v{CURRENT}`.\nPin `@v{CURRENT}` for reproducible installs.\n",
        encoding="utf-8",
    )
    (root / "docs" / "index.html").write_text(
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
        "docs/index.html": 1,
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
