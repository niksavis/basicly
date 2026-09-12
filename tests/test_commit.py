from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

from basicly import commit, run_record, tracker_paths
from tests import flipped_tracker

HOOK_PATH = Path(__file__).resolve().parent.parent / ".basicly" / "core" / "hooks" / "commit-msg.py"


def _hook_module():
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
    with pytest.raises(AssertionError, match=r"unstubbed git subcommand 'bisect': git bisect"):
        _FakeGit()(["bisect", "start"])


def _tracker(tmp_path: Path, *records: dict) -> Path:

    flipped_tracker.seed_records(tmp_path, records)
    return tmp_path


BOUND_TASK = {
    "id": "basicly-kjc5.42",
    "issue_type": "task",
    "status": "open",
    "external_ref": "worktree:basicly-kjc5-42:harness/basicly-kjc5-42",
}


def test_assembled_subject_derives_every_part_and_passes_the_hook(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    repo_root = _tracker(tmp_path, BOUND_TASK)
    git = _FakeGit("3\t0\tsrc/basicly/loop.py\n")
    monkeypatch.setattr(commit, "git", git)

    with pytest.raises(ValueError, match="disallowed character") as excinfo:
        commit.assemble(repo_root, description)

    assert named in str(excinfo.value)
    assert git.calls == [], "the description is checked before any git or tracker read"


def test_short_and_hyphen_terminated_descriptions_are_rejected() -> None:
    with pytest.raises(ValueError, match="at least 3 characters"):
        commit.check_description("ab")
    with pytest.raises(ValueError, match="end with a letter or digit"):
        commit.check_description("assemble the envelope-")


def test_charset_matches_the_commit_msg_hook_exactly() -> None:

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
    assert commit.derive_type(work_type, paths) == expected


def test_unknown_work_class_blocks_instead_of_guessing_a_type() -> None:
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
    assert commit.scope_candidate(path) == expected


def test_scope_follows_churn_and_ignores_the_companion_test() -> None:
    weights = {"src/basicly/commit.py": 60, "src/basicly/cli.py": 5, "tests/test_commit.py": 90}
    assert commit.derive_scope(weights) == "commit"


def test_scope_of_a_test_only_change_comes_from_the_tests() -> None:
    assert commit.derive_scope({"tests/test_loop.py": 12}) == "loop"


def test_scope_ties_break_alphabetically_and_scopeless_paths_yield_none() -> None:
    assert commit.derive_scope({"src/basicly/cli.py": 7, "src/basicly/loop.py": 7}) == "cli"
    assert commit.derive_scope({"pyproject.toml": 4}) is None


def test_staged_weights_count_one_plus_churn_and_tolerate_binaries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(commit, "git", _FakeGit("4\t2\tsrc/basicly/loop.py\n-\t-\tdocs/logo.png\n"))
    assert commit.staged_weights(tmp_path) == {"src/basicly/loop.py": 7, "docs/logo.png": 1}


def test_bead_comes_from_the_branch_binding_the_loop_recorded(tmp_path: Path) -> None:
    repo_root = _tracker(
        tmp_path,
        {"id": "basicly-other", "external_ref": "worktree:other:harness/other"},
        BOUND_TASK,
    )
    assert commit.bead_under_work(repo_root, "harness/basicly-kjc5-42") == "basicly-kjc5.42"


def test_a_reused_branch_prefers_the_open_bead(tmp_path: Path) -> None:
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
    repo_root = _tracker(tmp_path, BOUND_TASK)
    with pytest.raises(ValueError, match="--issue"):
        commit.bead_under_work(repo_root, "main")


def test_unknown_bead_is_refused_before_the_hook_sees_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = _tracker(tmp_path, BOUND_TASK)
    monkeypatch.setattr(commit, "git", _FakeGit("3\t0\tsrc/basicly/loop.py\n"))

    with pytest.raises(ValueError, match="unknown bead id"):
        commit.assemble(repo_root, "reference a bead that does not exist", bead="basicly-nope")


def test_tracker_read_follows_the_worktree_redirect(tmp_path: Path) -> None:
    base = _tracker(tmp_path / "base", BOUND_TASK)
    worktree = _tracker(tmp_path / "wt")
    (worktree / tracker_paths.LEDGER_DIR_NAME / tracker_paths.REDIRECT_NAME).write_text(
        f"{base}\n", encoding="utf-8"
    )

    assert commit.bead_under_work(worktree, "harness/basicly-kjc5-42") == "basicly-kjc5.42"


def test_explicit_overrides_replace_the_derived_parts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    repo_root = _tracker(tmp_path, BOUND_TASK)
    monkeypatch.setattr(commit, "git", _FakeGit("3\t0\tsrc/basicly/loop.py\n"))

    with pytest.raises(ValueError, match="unknown commit type"):
        commit.assemble(repo_root, "do the thing", commit_type="feature")
    with pytest.raises(ValueError, match="lowercase-kebab-case"):
        commit.assemble(repo_root, "do the thing", scope="Loop State")


def test_run_commit_passes_the_message_to_git_with_hooks_in_the_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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


def _dispatch(repo_root: Path, bead: str, **provenance: object) -> None:

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
    repo_root = _tracker(tmp_path, BOUND_TASK)
    _dispatch(repo_root, "basicly-kjc5.42", phase="build", model="claude-haiku-4-5")
    monkeypatch.setattr(commit, "git", _FakeGit("9\t1\tsrc/basicly/commit.py\n"))

    envelope = commit.assemble(repo_root, "carry the resolved model")

    assert envelope.model == "claude-haiku-4-5"
    assert envelope.message == (
        "feat(commit): carry the resolved model (basicly-kjc5.42)\n\n"
        "Harness-Model: claude-haiku-4-5\n"
    )
    assert _hook_module().validate(envelope.message)


def test_the_pinned_value_is_stamped_verbatim_not_the_observed_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

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
    repo_root = _tracker(tmp_path, BOUND_TASK)
    monkeypatch.setattr(commit, "git", _FakeGit("9\t1\tsrc/basicly/commit.py\n"))

    envelope = commit.assemble(repo_root, "carry the resolved model")

    assert envelope.trailers == ()


def test_a_decider_dispatch_does_not_supply_the_work_commits_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = _tracker(tmp_path, BOUND_TASK)
    _dispatch(repo_root, "basicly-kjc5.42", phase="build", model="claude-opus-4-5")
    _dispatch(repo_root, "basicly-kjc5.42", phase="decide", model="claude-haiku-4-5")
    monkeypatch.setattr(commit, "git", _FakeGit("9\t1\tsrc/basicly/commit.py\n"))

    envelope = commit.assemble(repo_root, "carry the resolved model")

    assert envelope.model == "claude-opus-4-5"


def test_a_worktree_reads_the_base_checkouts_run_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    base = tmp_path / "base"
    base.mkdir()
    _dispatch(base, "basicly-kjc5.42", phase="build", model="claude-opus-4-5")
    worktree = _tracker(tmp_path / "worktree", BOUND_TASK)
    monkeypatch.setattr(commit, "main_checkout", lambda _path: base)
    monkeypatch.setattr(commit, "git", _FakeGit("9\t1\tsrc/basicly/commit.py\n"))

    envelope = commit.assemble(worktree, "carry the resolved model")

    assert envelope.model == "claude-opus-4-5"


def test_the_body_and_the_trailers_are_separate_paragraphs() -> None:
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
    repo = tmp_path / "wt"
    repo.mkdir()
    _git(repo, "init", "-b", "harness/basicly-yvx9")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    _tracker(repo, SALVAGED_BUG)
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

    (killed_worktree / "notes.txt").write_text("work\n", encoding="utf-8")

    commit.salvage(killed_worktree, "basicly-yvx9", reason="runner_timeout after 1800s")

    body = " ".join(_git(killed_worktree, "log", "-1", "--pretty=%b").stdout.split())
    assert "runner_timeout after 1800s" in body
    assert "No agent signed this off" in body


def test_a_clean_worktree_salvages_nothing_and_says_so(killed_worktree: Path) -> None:
    salvaged = commit.salvage(killed_worktree, "basicly-yvx9", reason="runner_timeout after 1800s")

    assert (salvaged.status, salvaged.committed) == ("empty", False)
    assert "no uncommitted work" in salvaged.detail
    assert _subject(killed_worktree) == "init", "nothing was committed"


def test_a_rejected_salvage_leaves_the_work_where_the_kill_left_it(
    monkeypatch: pytest.MonkeyPatch, killed_worktree: Path
) -> None:

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
    salvaged = commit.salvage(tmp_path, "basicly-yvx9", reason="runner_timeout after 1800s")

    assert (salvaged.status, salvaged.committed) == ("refused", False)
