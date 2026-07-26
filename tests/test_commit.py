"""Tests for mechanical commit-envelope assembly (basicly-kjc5.42)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from basicly import commit

HOOK_PATH = Path(__file__).resolve().parent.parent / ".basicly" / "core" / "hooks" / "commit-msg.py"


def _hook_module():
    """Load the commit-msg hook so the emitted subject is checked by the real gate."""
    spec = importlib.util.spec_from_file_location("commit_msg_hook", HOOK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeGit:
    """Answers the reads assembly makes (staged churn, current branch), recording calls."""

    def __init__(self, numstat: str = "", branch: str = "harness/basicly-kjc5-42") -> None:
        self.numstat = numstat
        self.branch = branch
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], **_kwargs: object) -> _Proc:
        self.calls.append(args)
        if args[:2] == ["diff", "--cached"] and "--numstat" in args:
            return _Proc(0, self.numstat)
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return _Proc(0, f"{self.branch}\n")
        return _Proc(0)


def _tracker(tmp_path: Path, *records: dict) -> Path:
    """Write a beads JSONL the way br exports one, and return the repo root."""
    beads = tmp_path / ".beads"
    beads.mkdir(parents=True, exist_ok=True)
    (beads / "issues.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    return tmp_path


BOUND_TASK = {
    "id": "basicly-kjc5.42",
    "issue_type": "task",
    "status": "open",
    "external_ref": "worktree:basicly-kjc5-42:harness/basicly-kjc5-42",
}


# --- the emitted envelope ---------------------------------------------------


def test_assembled_subject_derives_every_part_and_passes_the_hook(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Type, scope, and bead id come from state; the real hook accepts the result."""
    repo_root = _tracker(tmp_path, BOUND_TASK)
    monkeypatch.setattr(
        commit, "git", _FakeGit("40\t2\tsrc/basicly/commit.py\n1\t0\tsrc/basicly/cli.py\n")
    )

    envelope = commit.assemble(repo_root, "assemble the commit envelope from state")

    assert envelope.subject == (
        "feat(commit): assemble the commit envelope from state (basicly-kjc5.42)"
    )
    hook = _hook_module()
    assert hook.validate(envelope.subject)


def test_body_and_breaking_marker_are_carried_into_the_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The authored body stays out of the validated subject; '!' marks the break."""
    repo_root = _tracker(tmp_path, BOUND_TASK)
    monkeypatch.setattr(commit, "git", _FakeGit("3\t0\tsrc/basicly/loop.py\n"))

    envelope = commit.assemble(
        repo_root, "drop the legacy flag", breaking=True, body="Removes --old in 0.6.0.\n"
    )

    assert envelope.subject == "feat(loop)!: drop the legacy flag (basicly-kjc5.42)"
    assert envelope.message == (
        "feat(loop)!: drop the legacy flag (basicly-kjc5.42)\n\nRemoves --old in 0.6.0.\n"
    )
    assert _hook_module().validate(envelope.message)


# --- description rules (rejected before any commit) -------------------------


@pytest.mark.parametrize(
    ("description", "named"),
    [
        ("Assemble the envelope", "'A'"),
        ("restamp the install to 0.6.0", "'.'"),
        ("fix test_cli mutation", "'_'"),
        ("assemble the envelope;", "';'"),
    ],
)
def test_out_of_charset_description_is_rejected_naming_the_character(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, description: str, named: str
) -> None:
    """Every charset rejection names the offending character."""
    repo_root = _tracker(tmp_path, BOUND_TASK)
    git = _FakeGit("3\t0\tsrc/basicly/loop.py\n")
    monkeypatch.setattr(commit, "git", git)

    with pytest.raises(ValueError, match="disallowed character") as excinfo:
        commit.assemble(repo_root, description)

    assert named in str(excinfo.value)
    assert git.calls == [], "the description is checked before any git or tracker read"


def test_short_and_hyphen_terminated_descriptions_are_rejected() -> None:
    """The remaining subject rules the hook enforces are refused here too."""
    with pytest.raises(ValueError, match="at least 3 characters"):
        commit.check_description("ab")
    with pytest.raises(ValueError, match="end with a letter or digit"):
        commit.check_description("assemble the envelope-")


def test_charset_matches_the_commit_msg_hook_exactly() -> None:
    """A drift tripwire: the engine's charset verdict equals the hook's, character by character.

    The rules are duplicated on purpose (the hook must run standalone, without
    basicly installed), so the duplication needs a gate rather than a comment.
    """
    hook = _hook_module()
    samples = [
        "assemble the envelope",
        "Assemble the envelope",
        "restamp to 0.6.0",
        "fix test_cli",
        "add a b2b flag",
        "ship it;",
        "über the top",
    ]
    for sample in samples:
        assert commit.disallowed_description_chars(sample) == hook.disallowed_description_chars(
            sample
        ), sample


# --- derivation rules ------------------------------------------------------


@pytest.mark.parametrize(
    ("work_type", "paths", "expected"),
    [
        ("bug", ("src/basicly/loop.py",), "fix"),
        ("task", ("src/basicly/loop.py",), "feat"),
        ("feature", ("src/basicly/loop.py",), "feat"),
        ("chore", ("src/basicly/loop.py",), "chore"),
        ("task", ("docs/architecture/architecture.md", "README.md"), "docs"),
        ("task", ("site/index.html",), "docs"),
        ("task", ("tests/test_loop.py",), "test"),
        ("chore", (".github/workflows/ci.yml",), "ci"),
        ("task", ("docs/architecture/architecture.md", "src/basicly/loop.py"), "feat"),
    ],
)
def test_type_follows_the_work_class_refined_by_the_paths(
    work_type: str, paths: tuple[str, ...], expected: str
) -> None:
    """The bead's class decides; an all-docs, all-test, or all-ci diff overrides it."""
    assert commit.derive_type(work_type, paths) == expected


def test_unknown_work_class_blocks_instead_of_guessing_a_type() -> None:
    """An unmappable work class names the override rather than picking a type."""
    with pytest.raises(ValueError, match="pass --type"):
        commit.derive_type("epic", ("src/basicly/loop.py",))


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/basicly/loop.py", "loop"),
        ("src/basicly/loop_state.py", "loop-state"),
        ("src/basicly/renderers/common.py", "renderers"),
        ("src/basicly/__init__.py", "basicly"),
        ("tests/test_loop_state.py", "loop-state"),
        ("tests/test_git_hooks/test_commit_msg.py", "git-hooks"),
        (".basicly/core/hooks/commit-msg.py", "hooks"),
        (".basicly/core/skills/tool-br/skill.yaml", "skills"),
        (".basicly-local/fragments/x.fragment.yaml", "fragments"),
        ("docs/design/factory-design.md", "factory-design"),
        ("site/index.html", "site"),
        ("site/assets/logo.svg", "site"),
        (".github/workflows/ci.yml", "ci"),
        (".beads/issues.jsonl", "beads"),
        ("pyproject.toml", None),
    ],
)
def test_scope_candidate_per_path(path: str, expected: str | None) -> None:
    """Each path class argues for one scope, or for none."""
    assert commit.scope_candidate(path) == expected


def test_scope_follows_churn_and_ignores_the_companion_test() -> None:
    """The heaviest non-test area wins; a regression test rides its subject's scope."""
    weights = {"src/basicly/commit.py": 60, "src/basicly/cli.py": 5, "tests/test_commit.py": 90}
    assert commit.derive_scope(weights) == "commit"


def test_scope_of_a_test_only_change_comes_from_the_tests() -> None:
    """With nothing but tests staged, the tests are the only thing that can argue."""
    assert commit.derive_scope({"tests/test_loop.py": 12}) == "loop"


def test_scope_ties_break_alphabetically_and_scopeless_paths_yield_none() -> None:
    """Equal weights resolve stably; a change nothing claims gets no scope."""
    assert commit.derive_scope({"src/basicly/cli.py": 7, "src/basicly/loop.py": 7}) == "cli"
    assert commit.derive_scope({"pyproject.toml": 4}) is None


def test_staged_weights_count_one_plus_churn_and_tolerate_binaries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Churn parsing keeps binary rows (which report '-') at the flat weight."""
    monkeypatch.setattr(commit, "git", _FakeGit("4\t2\tsrc/basicly/loop.py\n-\t-\tdocs/logo.png\n"))
    assert commit.staged_weights(tmp_path) == {"src/basicly/loop.py": 7, "docs/logo.png": 1}


# --- resolving the bead under work -----------------------------------------


def test_bead_comes_from_the_branch_binding_the_loop_recorded(tmp_path: Path) -> None:
    """The worktree binding on the bead identifies it — no branch-name convention."""
    repo_root = _tracker(
        tmp_path,
        {"id": "basicly-other", "external_ref": "worktree:other:harness/other"},
        BOUND_TASK,
    )
    assert commit.bead_under_work(repo_root, "harness/basicly-kjc5-42") == "basicly-kjc5.42"


def test_a_reused_branch_prefers_the_open_bead(tmp_path: Path) -> None:
    """A closed bead keeps its binding, so the open one is the bead under work."""
    repo_root = _tracker(
        tmp_path,
        {
            "id": "basicly-old",
            "status": "closed",
            "external_ref": "worktree:basicly-kjc5-42:harness/basicly-kjc5-42",
        },
        BOUND_TASK,
    )
    assert commit.bead_under_work(repo_root, "harness/basicly-kjc5-42") == "basicly-kjc5.42"


def test_unbound_branch_blocks_and_names_the_override(tmp_path: Path) -> None:
    """Committing from a branch no bead is bound to asks for --issue, never guesses."""
    repo_root = _tracker(tmp_path, BOUND_TASK)
    with pytest.raises(ValueError, match="--issue"):
        commit.bead_under_work(repo_root, "main")


def test_unknown_bead_is_refused_before_the_hook_sees_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An id absent from the JSONL the gate reads is refused with that file named."""
    repo_root = _tracker(tmp_path, BOUND_TASK)
    monkeypatch.setattr(commit, "git", _FakeGit("3\t0\tsrc/basicly/loop.py\n"))

    with pytest.raises(ValueError, match="unknown bead id"):
        commit.assemble(repo_root, "reference a bead that does not exist", bead="basicly-nope")


def test_tracker_read_follows_the_worktree_redirect(tmp_path: Path) -> None:
    """A harness worktree shares the base tracker, exactly as the beads hook resolves it."""
    base = _tracker(tmp_path / "base", BOUND_TASK)
    worktree = tmp_path / "wt"
    (worktree / ".beads").mkdir(parents=True)
    (worktree / ".beads" / "issues.jsonl").write_text("", encoding="utf-8")
    (worktree / ".beads" / "redirect").write_text(f"{base / '.beads'}\n", encoding="utf-8")

    assert commit.bead_under_work(worktree, "harness/basicly-kjc5-42") == "basicly-kjc5.42"


# --- overrides -------------------------------------------------------------


def test_explicit_overrides_replace_the_derived_parts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Each derived part can be overridden; the bead override skips branch resolution."""
    repo_root = _tracker(tmp_path, BOUND_TASK)
    monkeypatch.setattr(commit, "git", _FakeGit("3\t0\tsrc/basicly/loop.py\n"))

    envelope = commit.assemble(
        repo_root,
        "extract the envelope helper",
        bead="basicly-kjc5.42",
        commit_type="refactor",
        scope="cli",
    )

    assert envelope.subject == "refactor(cli): extract the envelope helper (basicly-kjc5.42)"


def test_invalid_overrides_are_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An override the hook would reject is refused here, before any commit."""
    repo_root = _tracker(tmp_path, BOUND_TASK)
    monkeypatch.setattr(commit, "git", _FakeGit("3\t0\tsrc/basicly/loop.py\n"))

    with pytest.raises(ValueError, match="unknown commit type"):
        commit.assemble(repo_root, "do the thing", commit_type="feature")
    with pytest.raises(ValueError, match="lowercase-kebab-case"):
        commit.assemble(repo_root, "do the thing", scope="Loop State")


# --- handing the message to git --------------------------------------------


def test_run_commit_passes_the_message_to_git_with_hooks_in_the_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The commit runs through plain `git commit -m` — no --no-verify, no bypass."""
    git = _FakeGit("")
    monkeypatch.setattr(commit, "git", git)
    envelope = commit.Envelope(
        type="feat", scope="commit", description="assemble the envelope", bead="basicly-kjc5.42"
    )

    result = commit.run_commit(tmp_path, envelope)

    assert result.committed is True
    assert git.calls == [["commit", "-m", envelope.message]]


def test_run_commit_reports_a_hook_rejection_without_retrying(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A rejecting hook stays a rejection, with its output carried back."""

    class _Rejecting:
        def __call__(self, _args, **_kwargs):
            return _Proc(1, "", "ERROR: Commit message does not follow conventional commit format.")

    monkeypatch.setattr(commit, "git", _Rejecting())
    envelope = commit.Envelope(
        type="feat", scope="commit", description="assemble the envelope", bead="basicly-kjc5.42"
    )

    result = commit.run_commit(tmp_path, envelope)

    assert result.committed is False
    assert "does not follow conventional commit format" in result.output
