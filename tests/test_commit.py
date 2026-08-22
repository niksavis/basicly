"""Tests for mechanical commit-envelope assembly (basicly-kjc5.42)."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

from basicly import commit, run_record, tracker_paths
from tests import flipped_tracker

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
    """Answers the reads assembly makes (staged churn, current branch), recording calls.

    Anything else raises (basicly-tcmy.22). A blanket ``_Proc(0)`` fallback made
    this stub answer "success, no output" to any read the production code later
    started making — and "no staged churn" or "no branch" is a *meaningful* answer
    to a commit envelope, so a new read would have been silently mis-answered by
    every test in this file at once instead of failing in one.
    """

    def __init__(
        self,
        numstat: str = "",
        branch: str = "harness/basicly-kjc5-42",
        commit_result: _Proc | None = None,
    ) -> None:
        self.numstat = numstat
        self.branch = branch
        self.commit_result = commit_result
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], **_kwargs: object) -> _Proc:
        self.calls.append(args)
        if args[:2] == ["diff", "--cached"] and "--numstat" in args:
            return _Proc(0, self.numstat)
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return _Proc(0, f"{self.branch}\n")
        if args[0] == "commit" and self.commit_result is not None:
            return self.commit_result
        raise AssertionError(f"unstubbed git subcommand {args[0]!r}: git {' '.join(args)}")


def test_an_unstubbed_git_subcommand_fails_the_test_naming_itself() -> None:
    """The stub's own contract (basicly-tcmy.22), so the fallback cannot come back."""
    with pytest.raises(AssertionError, match=r"unstubbed git subcommand 'bisect': git bisect"):
        _FakeGit()(["bisect", "start"])


def _tracker(tmp_path: Path, *records: dict) -> Path:
    """Seed *tmp_path*'s ledger with *records*, and return the repo root.

    Written through the kit rather than hand-authored as JSON lines: the fold is what
    every reader sees, and a hand-written log that the fold rejects would be describing
    a tracker that cannot exist.
    """
    flipped_tracker.seed_records(tmp_path, records)
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
        (".basicly/ledger/events-0001.jsonl", "ledger"),
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
    """A harness worktree shares the base tracker, exactly as the commit-msg hook does."""
    base = _tracker(tmp_path / "base", BOUND_TASK)
    worktree = _tracker(tmp_path / "wt")
    (worktree / tracker_paths.LEDGER_DIR_NAME / tracker_paths.REDIRECT_NAME).write_text(
        f"{base}\n", encoding="utf-8"
    )

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
    git = _FakeGit(commit_result=_Proc(0))
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


# --- model provenance (basicly-kjc5.60) -------------------------------------


def _dispatch(repo_root: Path, bead: str, **provenance: object) -> None:
    """Record one dispatch for *bead* the way the engine's own writer does.

    Goes through ``run_record`` rather than hand-writing the JSON so the fields the
    trailer reads stay the fields a real dispatch persists.
    """
    entry = run_record.build_record(
        agent="claude",
        handoff=False,
        returncode=0,
        duration_s=1.0,
        command=("claude", "-p", run_record.REDACTED_PROMPT),
        **provenance,  # type: ignore[arg-type]
    )
    run_record.record(repo_root, bead, entry)


def test_resolved_model_is_stamped_as_the_model_trailer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The dispatch's resolved model reaches the message as a final-paragraph trailer."""
    repo_root = _tracker(tmp_path, BOUND_TASK)
    _dispatch(repo_root, "basicly-kjc5.42", phase="build", model="claude-haiku-4-5")
    monkeypatch.setattr(commit, "git", _FakeGit("9\t1\tsrc/basicly/commit.py\n"))

    envelope = commit.assemble(repo_root, "carry the resolved model")

    assert envelope.model == "claude-haiku-4-5"
    assert envelope.message == (
        "feat(commit): carry the resolved model (basicly-kjc5.42)\n\n"
        "Harness-Model: claude-haiku-4-5\n"
    )
    # The gate reads the first line only, so the trailer must not change its verdict.
    assert _hook_module().validate(envelope.message)


def test_the_pinned_value_is_stamped_verbatim_not_the_observed_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A surface spelling is carried character for character, and observed never wins.

    Copilot's dotted id and the dated build the adapter reports back are the same
    model under different spellings; the trailer states what was pinned.
    """
    repo_root = _tracker(tmp_path, BOUND_TASK)
    _dispatch(
        repo_root,
        "basicly-kjc5.42",
        phase="lane",
        model="claude-haiku-4.5",
        model_tier="fast",
        model_source="agent-tier",
        tier_honoured=True,
        observed_models=("claude-haiku-4-5-20251001",),
    )
    monkeypatch.setattr(commit, "git", _FakeGit("9\t1\tsrc/basicly/commit.py\n"))

    envelope = commit.assemble(repo_root, "carry the resolved model")

    assert envelope.trailers == ("Harness-Model: claude-haiku-4.5",)


def test_a_dispatch_that_asked_for_no_model_carries_no_trailer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No tier and no pin means nothing was resolved because nothing was demanded.

    The ordinary state of a repo that declares no tier, so it must commit exactly
    as it did before the trailer existed — not be refused.
    """
    repo_root = _tracker(tmp_path, BOUND_TASK)
    _dispatch(repo_root, "basicly-kjc5.42", phase="build")
    monkeypatch.setattr(commit, "git", _FakeGit("9\t1\tsrc/basicly/commit.py\n"))

    envelope = commit.assemble(repo_root, "carry the resolved model")

    assert envelope.model is None
    assert envelope.trailers == ()
    assert envelope.message == "feat(commit): carry the resolved model (basicly-kjc5.42)"


def test_an_unhonoured_tier_refuses_the_envelope_instead_of_an_empty_trailer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A tier that pinned nothing is a demanded provenance nobody can answer."""
    repo_root = _tracker(tmp_path, BOUND_TASK)
    _dispatch(
        repo_root,
        "basicly-kjc5.42",
        phase="build",
        model_tier="fast",
        model_source="agent-tier",
        tier_honoured=False,
    )
    monkeypatch.setattr(commit, "git", _FakeGit("9\t1\tsrc/basicly/commit.py\n"))

    with pytest.raises(ValueError, match="no model was pinned") as excinfo:
        commit.assemble(repo_root, "carry the resolved model")

    message = str(excinfo.value)
    assert "'fast'" in message and "agent-tier" in message
    assert "Harness-Model" in message, "the refusal names the trailer it would have emitted"


def test_no_dispatch_record_at_all_carries_no_trailer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A commit made outside a dispatch has no model provenance to claim."""
    repo_root = _tracker(tmp_path, BOUND_TASK)
    monkeypatch.setattr(commit, "git", _FakeGit("9\t1\tsrc/basicly/commit.py\n"))

    envelope = commit.assemble(repo_root, "carry the resolved model")

    assert envelope.trailers == ()


def test_a_decider_dispatch_does_not_supply_the_work_commits_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A decision answered mid-build is the newest record but wrote none of the code."""
    repo_root = _tracker(tmp_path, BOUND_TASK)
    _dispatch(repo_root, "basicly-kjc5.42", phase="build", model="claude-opus-4-5")
    _dispatch(repo_root, "basicly-kjc5.42", phase="decide", model="claude-haiku-4-5")
    monkeypatch.setattr(commit, "git", _FakeGit("9\t1\tsrc/basicly/commit.py\n"))

    envelope = commit.assemble(repo_root, "carry the resolved model")

    assert envelope.model == "claude-opus-4-5"


def test_a_worktree_reads_the_base_checkouts_run_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The dispatch was recorded in the main checkout; the commit happens in the worktree."""
    base = tmp_path / "base"
    base.mkdir()
    _dispatch(base, "basicly-kjc5.42", phase="build", model="claude-opus-4-5")
    worktree = _tracker(tmp_path / "worktree", BOUND_TASK)
    monkeypatch.setattr(commit, "main_checkout", lambda _path: base)
    monkeypatch.setattr(commit, "git", _FakeGit("9\t1\tsrc/basicly/commit.py\n"))

    envelope = commit.assemble(worktree, "carry the resolved model")

    assert envelope.model == "claude-opus-4-5"


def test_the_body_and_the_trailers_are_separate_paragraphs() -> None:
    """An authored body keeps the trailers as the message's own last paragraph."""
    envelope = commit.Envelope(
        type="feat",
        scope="commit",
        description="carry the resolved model",
        bead="basicly-kjc5.42",
        body="Reads the recorded provenance.",
        model="claude-haiku-4-5",
    )

    assert envelope.message == (
        "feat(commit): carry the resolved model (basicly-kjc5.42)\n\n"
        "Reads the recorded provenance.\n\n"
        "Harness-Model: claude-haiku-4-5\n"
    )


# --- salvaging a killed dispatch's worktree (basicly-yvx9) ------------------


SALVAGED_BUG = {
    "id": "basicly-yvx9",
    "issue_type": "bug",
    "status": "in_progress",
    "external_ref": "worktree:basicly-yvx9:harness/basicly-yvx9",
}


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def killed_worktree(tmp_path: Path) -> Path:
    """A real git repo standing in for the worktree a killed runner left behind."""
    repo = tmp_path / "wt"
    repo.mkdir()
    _git(repo, "init", "-b", "harness/basicly-yvx9")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    _tracker(repo, SALVAGED_BUG)
    # The tracker export is committed like this repo's own, so the fixture starts
    # from a genuinely clean tree: anything dirty in a test is the killed run's work.
    _git(repo, "add", "--all")
    _git(repo, "commit", "-m", "init")
    return repo


def _subject(repo: Path) -> str:
    return _git(repo, "log", "-1", "--pretty=%s").stdout.strip()


def _dirty(repo: Path) -> str:
    return _git(repo, "status", "--porcelain").stdout.strip()


def test_the_killed_worktree_becomes_a_commit_the_landing_can_judge(
    killed_worktree: Path,
) -> None:
    """The whole point (basicly-yvx9): the tree on disk survives the kill as a commit.

    Exercised against a real git repo rather than a stub, because the claim is that
    ``git`` accepts what the salvage assembles — the message included. A stub that
    returns success would assert only that the code called ``commit``.
    """
    source = killed_worktree / "src" / "basicly"
    source.mkdir(parents=True)
    (source / "loop.py").write_text("VALUE = 1\n", encoding="utf-8")

    salvaged = commit.salvage(killed_worktree, "basicly-yvx9", reason="runner_timeout after 1800s")

    assert salvaged.committed is True
    assert _subject(killed_worktree) == (
        "fix(loop): salvage the work a killed runner left uncommitted (basicly-yvx9)"
    )
    assert _dirty(killed_worktree) == "", "the rescued work is committed, not merely staged"
    assert _hook_module().validate(_subject(killed_worktree))


def test_the_salvage_commit_says_a_kill_produced_it(killed_worktree: Path) -> None:
    """A reader of the history must see the kill, not an agent signing work off.

    The bead is explicit that rescuing the diff must not turn a timeout into a
    silent success: the body is where that stays true for anyone who arrives at
    this commit later, without the run record that does not survive a clone.
    """
    (killed_worktree / "notes.txt").write_text("work\n", encoding="utf-8")

    commit.salvage(killed_worktree, "basicly-yvx9", reason="runner_timeout after 1800s")

    # Unwrapped before matching: the body is wrapped for a reader, and where the
    # line breaks fall is not what this asserts.
    body = " ".join(_git(killed_worktree, "log", "-1", "--pretty=%b").stdout.split())
    assert "runner_timeout after 1800s" in body
    assert "No agent signed this off" in body


def test_a_clean_worktree_salvages_nothing_and_says_so(killed_worktree: Path) -> None:
    """No uncommitted work is not a failure — and must not become an empty commit."""
    salvaged = commit.salvage(killed_worktree, "basicly-yvx9", reason="runner_timeout after 1800s")

    assert (salvaged.status, salvaged.committed) == ("empty", False)
    assert "no uncommitted work" in salvaged.detail
    assert _subject(killed_worktree) == "init", "nothing was committed"


def test_a_rejected_salvage_leaves_the_work_where_the_kill_left_it(
    monkeypatch: pytest.MonkeyPatch, killed_worktree: Path
) -> None:
    """The hooks stay the floor: a refused commit must not also lose the tree.

    The rejection is injected rather than provoked with a real hook, so the
    assertion is about this function's contract on any platform — a shell hook
    script is not portable test data.
    """
    (killed_worktree / "notes.txt").write_text("work\n", encoding="utf-8")
    monkeypatch.setattr(
        commit, "run_commit", lambda *_a: commit.CommitResult(1, "prep\nhook refused: markdownlint")
    )

    salvaged = commit.salvage(killed_worktree, "basicly-yvx9", reason="runner_timeout after 1800s")

    assert (salvaged.status, salvaged.committed) == ("refused", False)
    assert "hook refused: markdownlint" in salvaged.detail
    assert _dirty(killed_worktree) != "", "the killed run's work is still on disk"
    assert _subject(killed_worktree) == "init"


def test_a_rejected_salvage_names_the_hook_that_failed_not_the_one_that_ran_last(
    monkeypatch: pytest.MonkeyPatch, killed_worktree: Path
) -> None:
    """The reported defect: the reason was the chain's last line (basicly-fi1i7z).

    `protect-generated-commit` runs last in this repo and passed, so the salvage reported a
    *passing* check as its rejection reason — worse than silence, because a reader who
    trusts it goes and audits a check that did not fail. The chain below is trimmed from a
    real `pre-commit run` in this repo; the mid-chain shape is what makes it bind.
    """
    chain = (
        "markdownlint.............................................................Failed\n"
        "- hook id: markdownlint\n"
        "- exit code: 1\n"
        "\n"
        "note.md:1:1 error MD018/no-missing-space-atx No space after hash\n"
        "\n"
        "protect-generated-commit.................................................Passed\n"
    )
    (killed_worktree / "notes.txt").write_text("work\n", encoding="utf-8")
    monkeypatch.setattr(commit, "run_commit", lambda *_a: commit.CommitResult(1, chain))

    salvaged = commit.salvage(killed_worktree, "basicly-yvx9", reason="runner_timeout after 1800s")

    assert salvaged.status == "refused"
    assert "markdownlint" in salvaged.detail
    assert "MD018" in salvaged.detail
    assert "protect-generated-commit" not in salvaged.detail
    assert "Passed" not in salvaged.detail


def test_a_worktree_that_is_not_a_repo_is_refused_rather_than_raising(tmp_path: Path) -> None:
    """The caller is already handling a killed run; a failed rescue is not a crash."""
    salvaged = commit.salvage(tmp_path, "basicly-yvx9", reason="runner_timeout after 1800s")

    assert (salvaged.status, salvaged.committed) == ("refused", False)
