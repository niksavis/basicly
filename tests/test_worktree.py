from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from basicly import cli, loop_state, tracker, worktree
from basicly.tracker_paths import LEDGER_DIR_NAME as LEDGER_DIR
from tests import flipped_tracker

real_provision_deps = worktree.provision_deps


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(repo: Path) -> Path:
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    return repo


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    return _init_repo(tmp_path / "repo")


@pytest.fixture
def other_repo(tmp_path: Path) -> Path:
    return _init_repo(tmp_path / "elsewhere")


@pytest.fixture(autouse=True)
def _stub_provisioning(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setattr(worktree, "provision_deps", lambda *_a, **_kw: ["deps: stubbed"])
    monkeypatch.setattr(worktree, "install_worktree_hooks", lambda _wt: "hooks: stubbed")


def test_create_makes_sibling_worktree_on_harness_branch(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(git_repo)
    session = worktree.create("feature-x")

    expected = git_repo.parent / "repo.worktrees" / "feature-x"
    assert session.path == expected
    assert expected.is_dir()
    assert session.branch == "harness/feature-x"
    assert session.base == "main"

    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=expected,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert branch == "harness/feature-x"


def test_create_persists_loadable_session(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(git_repo)
    worktree.create("feat")

    loaded = worktree.load_session("feat", git_repo)
    assert loaded is not None
    assert loaded.name == "feat"
    assert loaded.branch == "harness/feat"
    assert [s.name for s in worktree.list_sessions(git_repo)] == ["feat"]


def test_create_copies_env_local_when_present(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (git_repo / ".env.local").write_text("SECRET=1\n", encoding="utf-8")
    monkeypatch.chdir(git_repo)
    session = worktree.create("withenv")

    copied = session.path / ".env.local"
    assert copied.read_text(encoding="utf-8") == "SECRET=1\n"


def _seed_ledger(git_repo: Path, *lines: str) -> Path:
    ledger = git_repo / LEDGER_DIR
    ledger.mkdir(parents=True)
    (ledger / "events-0001.jsonl").write_text("".join(lines), encoding="utf-8")
    _git(git_repo, "add", f"{LEDGER_DIR.as_posix()}/events-0001.jsonl")
    _git(git_repo, "commit", "-m", "track the ledger")
    return ledger


def test_create_never_rewrites_the_checked_out_tracker(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    ledger = _seed_ledger(git_repo, '{"record":"x-1"}\n')
    (ledger / "events-0001.jsonl").write_text(
        '{"record":"x-1"}\n{"record":"x-2"}\n', encoding="utf-8"
    )

    monkeypatch.chdir(git_repo)
    session = worktree.create("fresh-issue")

    checked_out = session.path / LEDGER_DIR / "events-0001.jsonl"
    assert checked_out.read_text(encoding="utf-8") == '{"record":"x-1"}\n'
    redirect = session.path / LEDGER_DIR / worktree.tracker_paths.REDIRECT_NAME
    assert redirect.read_text(encoding="utf-8").strip() == str(git_repo)


def test_create_redirects_the_ledger_at_the_base_checkout(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    _seed_ledger(git_repo, '{"record":"x-1"}\n')

    monkeypatch.chdir(git_repo)
    session = worktree.create("shared-tracker")

    redirect = session.path / LEDGER_DIR / worktree.tracker_paths.REDIRECT_NAME
    assert redirect.read_text(encoding="utf-8").strip() == str(git_repo)
    assert worktree.tracker_paths.tracker_root(session.path) == git_repo


def test_a_base_with_no_ledger_gets_no_redirect(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.chdir(git_repo)
    session = worktree.create("no-tracker")

    assert not (session.path / LEDGER_DIR).exists()


def test_create_leaves_matching_tracker_untouched(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    beads = git_repo / ".beads"
    beads.mkdir()
    (beads / "issues.jsonl").write_text('{"id":"x-1"}\n', encoding="utf-8")
    (beads / ".gitignore").write_text("redirect\n", encoding="utf-8")
    _git(git_repo, "add", ".beads/issues.jsonl", ".beads/.gitignore")
    _git(git_repo, "commit", "-m", "track beads")

    monkeypatch.chdir(git_repo)
    session = worktree.create("clean-tracker")

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=session.path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert status == ""


def test_create_rejects_duplicate_name(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(git_repo)
    worktree.create("dup")
    with pytest.raises(SystemExit, match="already exists"):
        worktree.create("dup")


def test_create_provisions_against_repo_root_not_process_cwd(
    git_repo: Path, other_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.chdir(other_repo)
    session = worktree.create("lane-1", repo_root=git_repo)

    assert session.path == git_repo.parent / "repo.worktrees" / "lane-1"
    assert session.path.is_dir()
    assert _branches(git_repo) == {"main", "harness/lane-1"}
    assert [s.name for s in worktree.list_sessions(git_repo)] == ["lane-1"]

    assert _branches(other_repo) == {"main"}
    assert set(worktree.registered_worktrees(other_repo)) == {other_repo}
    assert not (other_repo.parent / "elsewhere.worktrees").exists()
    assert worktree.list_sessions(other_repo) == []


def test_cleanup_targets_repo_root_not_process_cwd(
    git_repo: Path, other_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(other_repo)
    session = worktree.create("lane-1", repo_root=git_repo)

    worktree.cleanup("lane-1", repo_root=git_repo)

    assert not session.path.exists()
    assert _branches(git_repo) == {"main"}
    assert worktree.load_session("lane-1", git_repo) is None
    assert _branches(other_repo) == {"main"}


def _branches(repo: Path) -> set[str]:
    out = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {line.strip() for line in out.splitlines() if line.strip()}


def test_cleanup_removes_worktree_branch_and_metadata(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(git_repo)
    session = worktree.create("gone")
    assert session.path.is_dir()

    worktree.cleanup("gone")

    assert not session.path.exists()
    assert "harness/gone" not in _branches(git_repo)
    assert "main" in _branches(git_repo)
    assert worktree.load_session("gone", git_repo) is None


def test_cleanup_drops_record_when_branch_already_gone(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.chdir(git_repo)
    session = worktree.create("recovered")
    shutil.rmtree(session.path)
    _git(git_repo, "worktree", "prune")
    _git(git_repo, "branch", "-D", "harness/recovered")
    assert worktree.load_session("recovered", git_repo) is not None

    worktree.cleanup("recovered", force=True)

    assert worktree.load_session("recovered", git_repo) is None


def test_cleanup_reinstalls_base_checkout_hooks(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    reinstalls: list[Path] = []
    monkeypatch.setattr(
        worktree,
        "install_worktree_hooks",
        lambda target: (reinstalls.append(Path(target)), "hooks: recorded")[1],
    )
    (git_repo / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")
    monkeypatch.chdir(git_repo)
    worktree.create("hooked")
    reinstalls.clear()

    worktree.cleanup("hooked")

    assert reinstalls == [git_repo]


def test_cleanup_skips_hook_reinstall_without_precommit_config(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reinstalls: list[Path] = []
    monkeypatch.setattr(
        worktree,
        "install_worktree_hooks",
        lambda target: (reinstalls.append(Path(target)), "hooks: recorded")[1],
    )
    monkeypatch.chdir(git_repo)
    worktree.create("plain")
    reinstalls.clear()

    worktree.cleanup("plain")

    assert reinstalls == []


def test_cleanup_reclaims_stale_session(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(git_repo)
    session = worktree.create("stale")
    shutil.rmtree(session.path)

    assert [s.name for s in worktree.stale_sessions(git_repo)] == ["stale"]
    worktree.cleanup("stale")
    assert worktree.load_session("stale", git_repo) is None
    assert "harness/stale" not in _branches(git_repo)


def test_cleanup_keeps_unmerged_branch_without_force(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(git_repo)
    session = worktree.create("wip")
    (session.path / "extra.txt").write_text("work\n", encoding="utf-8")
    for args in (["add", "extra.txt"], ["commit", "-m", "feat: wip (basicly-x)"]):
        subprocess.run(["git", *args], cwd=session.path, capture_output=True, text=True, check=True)

    worktree.cleanup("wip")
    assert not session.path.exists()
    assert "harness/wip" in _branches(git_repo)

    worktree.cleanup("wip", force=True)
    assert "harness/wip" not in _branches(git_repo)


def _land_by_replay(git_repo: Path, branch: str) -> None:

    (git_repo / "sibling.txt").write_text("sibling\n", encoding="utf-8")
    _git(git_repo, "add", "sibling.txt")
    _git(git_repo, "commit", "-m", "feat: a sibling lane lands first")
    _git(git_repo, "cherry-pick", branch)


def _commit_in(worktree_path: Path, name: str) -> None:
    (worktree_path / name).write_text(f"{name}\n", encoding="utf-8")
    _git(worktree_path, "add", name)
    _git(worktree_path, "commit", "-m", f"feat: {name}")


def test_cleanup_reclaims_a_rebase_merged_branch_whose_content_base_holds(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.chdir(git_repo)
    session = worktree.create("landed")
    _commit_in(session.path, "extra.txt")
    _land_by_replay(git_repo, "harness/landed")

    worktree.cleanup("landed")

    assert "harness/landed" not in _branches(git_repo)
    assert worktree.load_session("landed", git_repo) is None


def test_cleanup_refuses_a_rebase_merged_branch_whose_content_base_lacks(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    monkeypatch.chdir(git_repo)
    session = worktree.create("partly")
    _commit_in(session.path, "landed.txt")
    _land_by_replay(git_repo, "harness/partly")
    _commit_in(session.path, "stranded.txt")

    worktree.cleanup("partly")

    assert "harness/partly" in _branches(git_repo)
    assert worktree.load_session("partly", git_repo) is not None
    printed = capsys.readouterr().out
    assert "does not hold 1 path(s) it changed" in printed
    assert "stranded.txt" in printed


def test_unlanded_paths_ignores_what_a_sibling_lane_landed_after_the_fork(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.chdir(git_repo)
    session = worktree.create("scoped")
    _commit_in(session.path, "mine.txt")
    _land_by_replay(git_repo, "harness/scoped")

    assert worktree.unlanded_paths(git_repo, "main", "harness/scoped") == ()
    assert worktree.unlanded_paths(git_repo, "main", "harness/nope") is None


def _ghost(name: str) -> None:
    shutil.rmtree(worktree.create(name).path)


def test_a_stale_slot_does_not_count_toward_the_concurrency_cap(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.chdir(git_repo)
    worktree.create("live")
    _ghost("ghost")

    assert worktree.cap_refusal(2, git_repo) == ""
    assert worktree.cap_refusal(1, git_repo).startswith(
        "worktree concurrency cap reached (1/1 live)"
    )


def test_a_full_cap_still_refuses_when_no_stale_slot_is_there_to_discount(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.chdir(git_repo)
    worktree.create("a")
    worktree.create("b")

    assert worktree.cap_refusal(2, git_repo).startswith(
        "worktree concurrency cap reached (2/2 live)"
    )
    assert worktree.cap_refusal(3, git_repo) == ""


def test_a_refusal_names_the_stale_slot_records_and_the_command_that_reclaims_them(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.chdir(git_repo)
    worktree.create("live")
    _ghost("ghost-a")
    _ghost("ghost-b")

    refusal = worktree.cap_refusal(1, git_repo)

    assert "worktree concurrency cap reached (1/1 live)" in refusal
    assert "ghost-a, ghost-b" in refusal
    assert "basicly worktree cleanup <name> --force" in refusal


def test_provision_deps_selects_commands_by_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(worktree, "run", lambda args, **_kw: calls.append(args))

    both = tmp_path / "both"
    both.mkdir()
    (both / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (both / "package.json").write_text("{}\n", encoding="utf-8")
    notes = real_provision_deps(both)
    assert calls == [["uv", "sync"], ["npm", "install"]]
    assert notes == [".venv: uv sync", "node_modules: npm install"]

    calls.clear()
    empty = tmp_path / "empty"
    empty.mkdir()
    assert real_provision_deps(empty) == []
    assert calls == []

    calls.clear()
    lock_only = tmp_path / "lock"
    lock_only.mkdir()
    (lock_only / "uv.lock").write_text("", encoding="utf-8")
    assert real_provision_deps(lock_only) == [".venv: uv sync"]
    assert calls == [["uv", "sync"]]


def _node_project(root: Path, lock: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text("{}\n", encoding="utf-8")
    (root / "package-lock.json").write_text(lock, encoding="utf-8")
    return root


def _donor_with_node_modules(root: Path, lock: str) -> Path:
    donor = _node_project(root, lock)
    (donor / "node_modules" / "pkg").mkdir(parents=True)
    (donor / "node_modules" / "pkg" / "index.js").write_text("ok\n", encoding="utf-8")
    (donor / "node_modules" / ".bin").mkdir()
    try:
        (donor / "node_modules" / ".bin" / "cli").symlink_to(Path("..") / "pkg" / "index.js")
    except OSError, NotImplementedError:  # pragma: no cover - unprivileged Windows
        pytest.skip("symlinks not available on this platform")
    return donor


def test_provision_deps_copies_node_modules_from_a_lockfile_identical_donor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    calls: list[list[str]] = []
    monkeypatch.setattr(worktree, "run", lambda args, **_kw: calls.append(args))
    donor = _donor_with_node_modules(tmp_path / "donor", "lock-v1\n")
    lane = _node_project(tmp_path / "lane", "lock-v1\n")

    notes = real_provision_deps(lane, [donor])

    assert calls == []
    assert (lane / "node_modules" / "pkg" / "index.js").read_text(encoding="utf-8") == "ok\n"
    link = lane / "node_modules" / ".bin" / "cli"
    assert link.is_symlink()
    assert not Path(link.readlink()).is_absolute()
    assert notes == ["node_modules: copied from donor (identical package-lock.json)"]


def test_provision_deps_installs_when_no_donor_lockfile_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(worktree, "run", lambda args, **_kw: calls.append(args))
    changed = _donor_with_node_modules(tmp_path / "changed", "lock-v2\n")
    bare = _node_project(tmp_path / "bare", "lock-v1\n")
    lane = _node_project(tmp_path / "lane", "lock-v1\n")

    assert real_provision_deps(lane, [changed, bare]) == ["node_modules: npm install"]
    assert calls == [["npm", "install"]]
    assert not (lane / "node_modules").exists()

    calls.clear()
    unlocked = tmp_path / "unlocked"
    unlocked.mkdir()
    (unlocked / "package.json").write_text("{}\n", encoding="utf-8")
    assert real_provision_deps(unlocked, [changed]) == ["node_modules: npm install"]
    assert calls == [["npm", "install"]]


def test_provision_deps_discards_a_failed_copy_and_installs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    calls: list[list[str]] = []
    monkeypatch.setattr(worktree, "run", lambda args, **_kw: calls.append(args))
    donor = _donor_with_node_modules(tmp_path / "donor", "lock-v1\n")
    lane = _node_project(tmp_path / "lane", "lock-v1\n")

    def _die(_src: Path, dst: Path, **_kw: object) -> None:
        Path(dst).mkdir()
        (Path(dst) / "half").write_text("partial\n", encoding="utf-8")
        raise OSError("disk full")

    monkeypatch.setattr(worktree.shutil, "copytree", _die)

    notes = real_provision_deps(lane, [donor])

    assert calls == [["npm", "install"]]
    assert not (lane / "node_modules").exists()
    assert notes == ["node_modules: npm install (copy from donor failed: disk full)"]


def test_create_offers_the_base_checkout_and_live_siblings_as_donors(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(git_repo)
    seen: list[list[Path]] = []

    def _record(_wt: Path, donors: object = ()) -> list[str]:
        seen.append(list(donors))  # type: ignore[arg-type]
        return ["deps: stubbed"]

    monkeypatch.setattr(worktree, "provision_deps", _record)
    worktree.create("first")
    worktree.create("second")

    base = worktree.main_checkout(git_repo)
    first = worktree.load_session("first", git_repo)
    assert first is not None
    assert seen == [[base], [base, first.path]]


def test_create_and_cleanup_leave_base_head_untouched(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(git_repo)
    before = subprocess.run(
        ["git", "rev-parse", "main"], cwd=git_repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    worktree.create("cycle")
    worktree.cleanup("cycle")

    after = subprocess.run(
        ["git", "rev-parse", "main"], cwd=git_repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert after == before


def test_cleanup_rejects_unknown_name(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(git_repo)
    with pytest.raises(SystemExit, match="no worktree named"):
        worktree.cleanup("nope")


def test_cleanup_missing_ok_accepts_an_already_removed_worktree(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.chdir(git_repo)
    worktree.create("gone")
    worktree.cleanup("gone")

    worktree.cleanup("gone", missing_ok=True)

    with pytest.raises(SystemExit, match="no worktree named"):
        worktree.cleanup("gone")


def test_cli_worktree_create_list_cleanup(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(git_repo)

    assert cli.main(["worktree", "create", "cli-a"]) == 0
    assert (git_repo.parent / "repo.worktrees" / "cli-a").is_dir()
    assert cli.main(["worktree", "list"]) == 0

    assert cli.main(["worktree", "cleanup", "cli-a"]) == 0
    assert worktree.load_session("cli-a", git_repo) is None


def test_cli_worktree_enforces_concurrency_cap(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (git_repo / "basicly.toml").write_text("[worktree]\nconcurrency = 1\n", encoding="utf-8")
    monkeypatch.chdir(git_repo)

    assert cli.main(["worktree", "create", "first"]) == 0
    assert cli.main(["worktree", "create", "second"]) == 1
    assert worktree.load_session("second", git_repo) is None


def test_cli_worktree_uses_configured_base_branch(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subprocess.run(
        ["git", "branch", "develop"], cwd=git_repo, capture_output=True, text=True, check=True
    )
    (git_repo / "basicly.toml").write_text(
        '[worktree]\nbase_branch = "develop"\n', encoding="utf-8"
    )
    monkeypatch.chdir(git_repo)

    assert cli.main(["worktree", "create", "on-develop"]) == 0
    session = worktree.load_session("on-develop", git_repo)
    assert session is not None
    assert session.base == "develop"


def test_cleanup_refuses_a_worktree_with_uncommitted_work(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(git_repo)
    session = worktree.create("dirty")
    (session.path / "wip.txt").write_text("not committed", encoding="utf-8")

    with pytest.raises(SystemExit, match="uncommitted changes"):
        worktree.cleanup("dirty")
    assert session.path.exists()

    worktree.cleanup("dirty", force=True)
    assert not session.path.exists()


def test_a_failed_status_query_holds_the_worktree_instead_of_clearing_it() -> None:

    verdict = worktree.classify_worktree_tree(128, "")
    assert verdict.may_remove is False
    assert verdict.indeterminate is True
    assert "exit 128" in verdict.holds


def test_an_unparsable_status_line_holds_the_worktree() -> None:
    verdict = worktree.classify_worktree_tree(0, "?\n")
    assert verdict.may_remove is False
    assert verdict.indeterminate is True
    assert "cannot parse" in verdict.holds


def test_real_pending_work_holds_the_worktree_and_is_not_called_indeterminate() -> None:
    verdict = worktree.classify_worktree_tree(0, "?? wip.txt\n M src/app.py\n")
    assert verdict.may_remove is False
    assert verdict.indeterminate is False
    assert "wip.txt" in verdict.holds and "src/app.py" in verdict.holds


def test_a_clean_tree_and_expected_noise_may_be_removed() -> None:
    assert worktree.classify_worktree_tree(0, "").may_remove is True
    noise = f"?? .venv/\n?? node_modules/\n?? {LEDGER_DIR.as_posix()}/redirect\n"
    verdict = worktree.classify_worktree_tree(0, noise)
    assert verdict.may_remove is True and verdict.holds == ""


def test_cleanup_refuses_when_git_cannot_report_the_worktree_state(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(git_repo)
    session = worktree.create("unknowable")

    real_git = worktree.git

    def flaky_git(args, **kwargs):
        if args[:2] == ["status", "--porcelain"]:
            return subprocess.CompletedProcess(args, 128, "", "fatal: index.lock exists")
        return real_git(args, **kwargs)

    monkeypatch.setattr(worktree, "git", flaky_git)

    with pytest.raises(SystemExit, match="git status could not be read"):
        worktree.cleanup("unknowable")
    assert session.path.exists()
    assert worktree.load_session("unknowable", git_repo) is not None


def test_an_unanswerable_branch_check_keeps_the_session_record(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.chdir(git_repo)
    worktree.create("orphanrisk")

    real_git = worktree.git

    def flaky_git(args, **kwargs):
        if args[:1] == ["branch"]:
            return subprocess.CompletedProcess(args, 1, "", "error: not fully merged")
        if args[:1] == ["show-ref"]:
            return subprocess.CompletedProcess(args, 128, "", "fatal: bad repository")
        return real_git(args, **kwargs)

    monkeypatch.setattr(worktree, "git", flaky_git)

    worktree.cleanup("orphanrisk")

    assert worktree.load_session("orphanrisk", git_repo) is not None


def test_cleanup_ignores_dep_dirs_and_the_tracker_redirect(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_ledger(git_repo, '{"record":"x-1"}\n')
    monkeypatch.chdir(git_repo)
    session = worktree.create("depsonly")
    (session.path / ".venv").mkdir(exist_ok=True)
    (session.path / ".venv" / "marker.txt").write_text("x", encoding="utf-8")
    assert (session.path / LEDGER_DIR / "redirect").is_file()

    worktree.cleanup("depsonly")
    assert not session.path.exists()


@pytest.fixture
def tracked_repo(git_repo: Path) -> Path:
    return flipped_tracker.flipped_repo(git_repo)


def test_cli_create_binds_the_record_it_provisioned_for(
    tracked_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    flipped_tracker.seed(tracked_repo, "wt-1", issue_type="task")
    flipped_tracker.seed(tracked_repo, "wt-control", issue_type="task")
    monkeypatch.chdir(tracked_repo)

    assert cli.main(["worktree", "create", "wt-1"]) == 0

    state = loop_state.read_node_state(tracked_repo, "wt-1")
    assert state.worktree == loop_state.WorktreeBinding("wt-1", "harness/wt-1")
    assert state.phase == "build"
    assert loop_state.read_node_state(tracked_repo, "wt-control").phase == "intake"


def test_cli_create_refuses_a_record_that_already_carries_a_binding(
    tracked_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    held = loop_state.format_worktree_ref("wt-held", "harness/wt-held")
    flipped_tracker.seed(tracked_repo, "wt-2", external_ref=held)
    monkeypatch.chdir(tracked_repo)

    assert cli.main(["worktree", "create", "wt-2"]) == 1

    assert worktree.load_session("wt-2", tracked_repo) is None
    state = loop_state.read_node_state(tracked_repo, "wt-2")
    assert state.worktree == loop_state.WorktreeBinding("wt-held", "harness/wt-held")


def test_cli_create_binds_nothing_for_a_name_that_is_not_a_record(
    tracked_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.chdir(tracked_repo)

    assert cli.main(["worktree", "create", "not-a-record"]) == 0

    assert worktree.load_session("not-a-record", tracked_repo) is not None
    assert tracker.read_record(tracked_repo, "not-a-record") is None
    assert flipped_tracker.ledger_events(tracked_repo) == []
