"""Tests for sibling git-worktree isolation (create, provision, cleanup)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from basicly import cli, loop_state, tracker, worktree
from basicly.tracker_paths import LEDGER_DIR_NAME as LEDGER_DIR
from tests import flipped_tracker

# Bound at import to the real function, so it stays reachable past the autouse
# stub below (which rebinds ``worktree.provision_deps`` for the other tests).
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
    """Initialize *repo* as a git repo with one commit on ``main``."""
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
    """A real git repo (named ``repo``) with one commit on ``main``."""
    return _init_repo(tmp_path / "repo")


@pytest.fixture
def other_repo(tmp_path: Path) -> Path:
    """A second repo, standing in for the checkout the process happens to be in."""
    return _init_repo(tmp_path / "elsewhere")


@pytest.fixture(autouse=True)
def _stub_provisioning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the slow, network-bound dep install and hook activation by default.

    ``create``'s git/session/path logic is deterministic and cheap to test; the
    real ``uv sync`` / ``npm install`` / hook install are exercised by dogfood.
    """
    monkeypatch.setattr(worktree, "provision_deps", lambda *_a, **_kw: ["deps: stubbed"])
    monkeypatch.setattr(worktree, "install_worktree_hooks", lambda _wt: "hooks: stubbed")


def test_create_makes_sibling_worktree_on_harness_branch(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``create`` adds a sibling worktree checked out on ``harness/<name>``."""
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
    """A created worktree writes a session record that round-trips on load."""
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
    """A present ``.env.local`` is copied into the new worktree."""
    (git_repo / ".env.local").write_text("SECRET=1\n", encoding="utf-8")
    monkeypatch.chdir(git_repo)
    session = worktree.create("withenv")

    copied = session.path / ".env.local"
    assert copied.read_text(encoding="utf-8") == "SECRET=1\n"


def _seed_ledger(git_repo: Path, *lines: str) -> Path:
    """Commit an event log in *git_repo* and return its ledger directory."""
    ledger = git_repo / LEDGER_DIR
    ledger.mkdir(parents=True)
    (ledger / "events-0001.jsonl").write_text("".join(lines), encoding="utf-8")
    _git(git_repo, "add", f"{LEDGER_DIR.as_posix()}/events-0001.jsonl")
    _git(git_repo, "commit", "-m", "track the ledger")
    return ledger


def test_create_never_rewrites_the_checked_out_tracker(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uncommitted base tracker state must NOT be copied over the worktree's file.

    Fresh ids reach the worktree through the ``redirect``, which the engine and the
    commit-msg hook both follow; overwriting the checked-out event log with the base
    working-tree version leaves the worktree permanently dirty and blocks the landing
    rebase (basicly-h61t rework 1).
    """
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
    """One store per repository: the worktree's ledger redirects at the base checkout's.

    A lane that wrote into its own checked-out ledger would lose every write at
    teardown, which is what happened to the usage spool (basicly-vkh0.8).
    """
    _seed_ledger(git_repo, '{"record":"x-1"}\n')

    monkeypatch.chdir(git_repo)
    session = worktree.create("shared-tracker")

    redirect = session.path / LEDGER_DIR / worktree.tracker_paths.REDIRECT_NAME
    assert redirect.read_text(encoding="utf-8").strip() == str(git_repo)
    assert worktree.tracker_paths.tracker_root(session.path) == git_repo


def test_a_base_with_no_ledger_gets_no_redirect(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control on the two above: a repository with no tracker needs no redirect.

    Writing one anyway creates an untracked `.basicly/` the teardown then reads as
    uncommitted work, which held every cleanup in a repo that has no tracker at all.
    """
    monkeypatch.chdir(git_repo)
    session = worktree.create("no-tracker")

    assert not (session.path / LEDGER_DIR).exists()


def test_create_leaves_matching_tracker_untouched(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A committed, unchanged tracker file is not rewritten (worktree stays clean).

    The fixture tracks ``.beads/.gitignore`` the way ``br init`` writes it, so
    the provisioning-written ``redirect`` file stays invisible to git.
    """
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
    """Creating a second worktree with a taken name is rejected."""
    monkeypatch.chdir(git_repo)
    worktree.create("dup")
    with pytest.raises(SystemExit, match="already exists"):
        worktree.create("dup")


def test_create_provisions_against_repo_root_not_process_cwd(
    git_repo: Path, other_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``create`` provisions into *repo_root*, never the repo the process stands in.

    Regression: a driver run from another checkout provisioned its worktrees and
    ``harness/*`` branches there, silently ignoring the repo root it was handed.
    """
    monkeypatch.chdir(other_repo)
    session = worktree.create("lane-1", repo_root=git_repo)

    assert session.path == git_repo.parent / "repo.worktrees" / "lane-1"
    assert session.path.is_dir()
    assert _branches(git_repo) == {"main", "harness/lane-1"}
    assert [s.name for s in worktree.list_sessions(git_repo)] == ["lane-1"]

    # The repo the process was standing in gained nothing.
    assert _branches(other_repo) == {"main"}
    assert set(worktree.registered_worktrees(other_repo)) == {other_repo}
    assert not (other_repo.parent / "elsewhere.worktrees").exists()
    assert worktree.list_sessions(other_repo) == []


def test_cleanup_targets_repo_root_not_process_cwd(
    git_repo: Path, other_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``cleanup`` tears down in *repo_root* while the process stands elsewhere."""
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
    """Cleanup removes the dir, deletes the harness branch, and drops the record."""
    monkeypatch.chdir(git_repo)
    session = worktree.create("gone")
    assert session.path.is_dir()

    worktree.cleanup("gone")

    assert not session.path.exists()
    assert "harness/gone" not in _branches(git_repo)
    assert "main" in _branches(git_repo)  # base untouched
    assert worktree.load_session("gone", git_repo) is None


def test_cleanup_drops_record_when_branch_already_gone(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hand-recovered worktree (dir + branch already removed) leaves no orphan record.

    Regression (basicly-9niw): cleanup kept the session ``.json`` whenever the
    branch delete failed — including when the branch was already gone — and the
    stale record kept counting toward the concurrency cap.
    """
    monkeypatch.chdir(git_repo)
    session = worktree.create("recovered")
    # Simulate the manual recovery: remove the worktree and branch out-of-band.
    shutil.rmtree(session.path)
    _git(git_repo, "worktree", "prune")
    _git(git_repo, "branch", "-D", "harness/recovered")
    assert worktree.load_session("recovered", git_repo) is not None  # orphan present

    worktree.cleanup("recovered", force=True)

    assert worktree.load_session("recovered", git_repo) is None


def test_cleanup_reinstalls_base_checkout_hooks(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Create -> teardown reinstalls hooks against the base checkout.

    Worktrees share the common ``.git/hooks`` dir, so provisioning can leave
    shims embedding the worktree venv's pre-commit path; cleanup must re-run
    the hook install from the base checkout so a commit there succeeds
    immediately after teardown (regression: basicly-zrj.13.3).
    """
    reinstalls: list[Path] = []
    monkeypatch.setattr(
        worktree,
        "install_worktree_hooks",
        lambda target: (reinstalls.append(Path(target)), "hooks: recorded")[1],
    )
    (git_repo / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")
    monkeypatch.chdir(git_repo)
    worktree.create("hooked")
    reinstalls.clear()  # drop the provisioning-time install; assert on teardown only

    worktree.cleanup("hooked")

    assert reinstalls == [git_repo]


def test_cleanup_skips_hook_reinstall_without_precommit_config(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repo with no pre-commit wiring never gains hooks from a teardown."""
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
    """A record whose worktree dir vanished out-of-band is still reclaimable."""
    monkeypatch.chdir(git_repo)
    session = worktree.create("stale")
    # Remove the worktree behind git's back, leaving a dangling session record.
    shutil.rmtree(session.path)

    assert [s.name for s in worktree.stale_sessions(git_repo)] == ["stale"]
    worktree.cleanup("stale")
    assert worktree.load_session("stale", git_repo) is None
    assert "harness/stale" not in _branches(git_repo)


def test_cleanup_keeps_unmerged_branch_without_force(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unmerged branch survives a plain cleanup and is removed with force."""
    monkeypatch.chdir(git_repo)
    session = worktree.create("wip")
    (session.path / "extra.txt").write_text("work\n", encoding="utf-8")
    for args in (["add", "extra.txt"], ["commit", "-m", "feat: wip (basicly-x)"]):
        subprocess.run(["git", *args], cwd=session.path, capture_output=True, text=True, check=True)

    worktree.cleanup("wip")  # unmerged: dir gone, branch kept
    assert not session.path.exists()
    assert "harness/wip" in _branches(git_repo)

    worktree.cleanup("wip", force=True)  # reclaim: branch gone
    assert "harness/wip" not in _branches(git_repo)


def _land_by_replay(git_repo: Path, branch: str) -> None:
    """Land *branch*'s content on main the way the queue does when a sibling goes first.

    The landing replays the lane onto the base it finds, so a lane that queued behind
    another lands on a moved base and its commits arrive under new shas. Measured on
    git 2.x on 2026-08-20: base then holds every line while the original ref is *not* an
    ancestor, and ``git branch -d`` answers ``the branch '<branch>' is not fully
    merged``. That string is observed here, not composed — a fixture built to match a
    suspected message produces a fix for a failure that never happens.
    """
    (git_repo / "sibling.txt").write_text("sibling\n", encoding="utf-8")
    _git(git_repo, "add", "sibling.txt")
    _git(git_repo, "commit", "-m", "feat: a sibling lane lands first")
    _git(git_repo, "cherry-pick", branch)


def _commit_in(worktree_path: Path, name: str) -> None:
    """Commit one new file on the branch checked out in *worktree_path*."""
    (worktree_path / name).write_text(f"{name}\n", encoding="utf-8")
    _git(worktree_path, "add", name)
    _git(worktree_path, "commit", "-m", f"feat: {name}")


def test_cleanup_reclaims_a_rebase_merged_branch_whose_content_base_holds(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A correctly landed lane is reclaimed without ``--force``.

    ``git branch -d`` tests ancestry, and ancestry is not what makes a branch safe to
    discard: this lane's content is in base at every path it touched, and the ref is
    still refused as "not fully merged". Relaying that as *unmerged — re-run with force
    to reclaim* made the check wrong on every correct landing, and a check that is wrong
    every time teaches an operator to pass ``--force`` without reading it — which is the
    one flag that deletes work (basicly-8g719r).
    """
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
    """The signal that saved a commit has to survive the fix that stops the false alarm.

    On 2026-08-20 ``git diff <branch> main`` was non-empty on a tracked path, and that
    diff is the only reason a real commit was not discarded. So the content test must
    still refuse, and must name the path instead of offering a blanket ``--force``. The
    shape here is the one a resumed agent produces: one commit landed, a second made
    after the tip had been read and never landed at all.

    This is the test a permissive fix breaks. Making :func:`worktree.unlanded_paths`
    return ``()`` unconditionally leaves the reclaim test above green and fails only
    this one (basicly-8g719r).
    """
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
    """Only the paths the branch touched are compared, and git's silence fails closed.

    Diffing the whole tree would name every path a sibling lane has landed since the
    fork, which is the same wrong-every-time answer pointing at "refuse" instead of at
    "reclaim" — and being wrong toward refuse is what trains the bypass.
    """
    monkeypatch.chdir(git_repo)
    session = worktree.create("scoped")
    _commit_in(session.path, "mine.txt")
    _land_by_replay(git_repo, "harness/scoped")

    # sibling.txt is in base and not on the branch, so it must not be reported.
    assert worktree.unlanded_paths(git_repo, "main", "harness/scoped") == ()
    assert worktree.unlanded_paths(git_repo, "main", "harness/nope") is None


def _ghost(name: str) -> None:
    """Create a worktree and remove its checkout the way raw git does: silently."""
    shutil.rmtree(worktree.create(name).path)


def test_a_stale_slot_does_not_count_toward_the_concurrency_cap(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record whose checkout is gone occupies nothing, so it may not hold a slot.

    The cap counted recorded sessions, and both routes to a record outliving its
    checkout were hit on 2026-08-20: ``cleanup`` without ``--force`` keeps the record
    when the branch survives, and ``git worktree remove`` tells the engine nothing. The
    result was a refusal reading ``cap reached (5/5)`` with three worktrees on disk
    (basicly-gtoqu9).
    """
    monkeypatch.chdir(git_repo)
    worktree.create("live")
    _ghost("ghost")

    # Two records, one checkout: at a cap of two the old count refused here.
    assert worktree.cap_refusal(2, git_repo) == ""
    # And the slot the ghost is not holding is really free, not merely uncounted.
    assert worktree.cap_refusal(1, git_repo).startswith(
        "worktree concurrency cap reached (1/1 live)"
    )


def test_a_full_cap_still_refuses_when_no_stale_slot_is_there_to_discount(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discounting stale records must not discount the cap itself.

    The cap is the only thing bounding concurrent lanes, so the fix has to keep refusing
    when the checkouts are genuinely there. A ``cap_refusal`` that returned ``""``
    unconditionally would satisfy the test above and fail this one.
    """
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
    """A refusal that names only the cap makes raising the cap the cheapest reading.

    Which is what happened: an operator raised ``[worktree].concurrency`` instead of
    reclaiming a slot, because nothing in the message said there was one to reclaim.
    """
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
    """provision_deps runs uv sync / npm install only for manifests present."""
    calls: list[list[str]] = []
    monkeypatch.setattr(worktree, "run", lambda args, **_kw: calls.append(args))

    # Both ecosystems present -> both commands, in order.
    both = tmp_path / "both"
    both.mkdir()
    (both / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (both / "package.json").write_text("{}\n", encoding="utf-8")
    notes = real_provision_deps(both)
    assert calls == [["uv", "sync"], ["npm", "install"]]
    assert notes == [".venv: uv sync", "node_modules: npm install"]

    # Neither manifest -> no commands, no notes.
    calls.clear()
    empty = tmp_path / "empty"
    empty.mkdir()
    assert real_provision_deps(empty) == []
    assert calls == []

    # uv.lock alone still triggers uv sync (no pyproject needed).
    calls.clear()
    lock_only = tmp_path / "lock"
    lock_only.mkdir()
    (lock_only / "uv.lock").write_text("", encoding="utf-8")
    assert real_provision_deps(lock_only) == [".venv: uv sync"]
    assert calls == [["uv", "sync"]]


def _node_project(root: Path, lock: str) -> Path:
    """A checkout with a ``package.json`` and the given ``package-lock.json`` content."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text("{}\n", encoding="utf-8")
    (root / "package-lock.json").write_text(lock, encoding="utf-8")
    return root


def _donor_with_node_modules(root: Path, lock: str) -> Path:
    """A provisioned donor: npm's layout in miniature, relative ``.bin`` link included."""
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
    """A donor built from the same lockfile is copied instead of installed again.

    Measured 2026-08-26: ``npm install`` was 5.6s of the 6.2s a second worktree spent
    provisioning, against 0.4s to copy the same tree (basicly-oqspon).
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(worktree, "run", lambda args, **_kw: calls.append(args))
    donor = _donor_with_node_modules(tmp_path / "donor", "lock-v1\n")
    lane = _node_project(tmp_path / "lane", "lock-v1\n")

    notes = real_provision_deps(lane, [donor])

    assert calls == []
    assert (lane / "node_modules" / "pkg" / "index.js").read_text(encoding="utf-8") == "ok\n"
    link = lane / "node_modules" / ".bin" / "cli"
    assert link.is_symlink()
    assert not Path(link.readlink()).is_absolute()  # stays inside the lane's own tree
    assert notes == ["node_modules: copied from donor (identical package-lock.json)"]


def test_provision_deps_installs_when_no_donor_lockfile_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A differing lock, an unprovisioned donor and a missing lock each install as before."""
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
    """A copy that dies part-way is removed and installed over, and the note says why.

    A half-copied tree is the one state npm would install *over*, so it must not survive.
    """
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
    """Provisioning is offered the base checkout first, then every live sibling worktree."""
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
    """A full create->cleanup cycle never moves the base branch's HEAD."""
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
    """Cleanup of a name with no record and no worktree is rejected."""
    monkeypatch.chdir(git_repo)
    with pytest.raises(SystemExit, match="no worktree named"):
        worktree.cleanup("nope")


def test_cleanup_missing_ok_accepts_an_already_removed_worktree(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second teardown under missing_ok returns instead of raising.

    The ship advance tears down and then closes. A worktree an earlier teardown
    already removed once killed the advance before the close, leaving the
    shipped issue open at ship (basicly-e2mz.32).
    """
    monkeypatch.chdir(git_repo)
    worktree.create("gone")
    worktree.cleanup("gone")

    worktree.cleanup("gone", missing_ok=True)

    with pytest.raises(SystemExit, match="no worktree named"):
        worktree.cleanup("gone")


def test_cli_worktree_create_list_cleanup(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The worktree subcommands create, list, and clean up a session."""
    monkeypatch.chdir(git_repo)

    assert cli.main(["worktree", "create", "cli-a"]) == 0
    assert (git_repo.parent / "repo.worktrees" / "cli-a").is_dir()
    assert cli.main(["worktree", "list"]) == 0

    assert cli.main(["worktree", "cleanup", "cli-a"]) == 0
    assert worktree.load_session("cli-a", git_repo) is None


def test_cli_worktree_enforces_concurrency_cap(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Creating past [worktree].concurrency is refused with a non-zero exit."""
    (git_repo / "basicly.toml").write_text("[worktree]\nconcurrency = 1\n", encoding="utf-8")
    monkeypatch.chdir(git_repo)

    assert cli.main(["worktree", "create", "first"]) == 0
    assert cli.main(["worktree", "create", "second"]) == 1
    assert worktree.load_session("second", git_repo) is None


def test_cli_worktree_uses_configured_base_branch(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A create with no --base forks from [worktree].base_branch."""
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
    """Uncommitted changes block cleanup unless forced; force discards them."""
    monkeypatch.chdir(git_repo)
    session = worktree.create("dirty")
    (session.path / "wip.txt").write_text("not committed", encoding="utf-8")

    with pytest.raises(SystemExit, match="uncommitted changes"):
        worktree.cleanup("dirty")
    assert session.path.exists()

    worktree.cleanup("dirty", force=True)
    assert not session.path.exists()


# --- a transient git failure must never authorize a deletion (basicly-jr0l.47) --
#
# The classifier is pure, so each fail-closed branch is asserted directly rather
# than through a repo fixture. The cost of getting one wrong is committed work that
# no longer exists, which no later test can detect.


def test_a_failed_status_query_holds_the_worktree_instead_of_clearing_it() -> None:
    """The defect: a non-zero git status read as 'nothing to keep'.

    A lock held by a concurrent lane, an interrupted index write, or a filesystem
    hiccup makes this query fail. The old reader returned "" — no pending changes —
    and handed `git worktree remove --force` a tree it was never allowed to discard,
    losing the branch and its commits silently and unrecoverably.
    """
    verdict = worktree.classify_worktree_tree(128, "")
    assert verdict.may_remove is False
    assert verdict.indeterminate is True
    assert "exit 128" in verdict.holds


def test_an_unparsable_status_line_holds_the_worktree() -> None:
    """A status this cannot read may be the status that matters."""
    verdict = worktree.classify_worktree_tree(0, "?\n")
    assert verdict.may_remove is False
    assert verdict.indeterminate is True
    assert "cannot parse" in verdict.holds


def test_real_pending_work_holds_the_worktree_and_is_not_called_indeterminate() -> None:
    """Both refuse, but 'there is work here' is a different report from 'cannot tell'."""
    verdict = worktree.classify_worktree_tree(0, "?? wip.txt\n M src/app.py\n")
    assert verdict.may_remove is False
    assert verdict.indeterminate is False
    assert "wip.txt" in verdict.holds and "src/app.py" in verdict.holds


def test_a_clean_tree_and_expected_noise_may_be_removed() -> None:
    """The guard must not refuse the ordinary teardown it wraps."""
    assert worktree.classify_worktree_tree(0, "").may_remove is True
    noise = f"?? .venv/\n?? node_modules/\n?? {LEDGER_DIR.as_posix()}/redirect\n"
    verdict = worktree.classify_worktree_tree(0, noise)
    assert verdict.may_remove is True and verdict.holds == ""


def test_cleanup_refuses_when_git_cannot_report_the_worktree_state(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: an indeterminate query leaves the worktree and its branch intact."""
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
    # Nothing was discarded: the tree, the branch, and the record all survive.
    assert session.path.exists()
    assert worktree.load_session("unknowable", git_repo) is not None


def test_an_unanswerable_branch_check_keeps_the_session_record(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A show-ref that errors is not proof the branch is gone.

    Reading any non-zero exit as "absent" dropped the session record while the
    branch survived — an orphaned branch nothing points at, and the same fail-open
    class as the tree check.
    """
    monkeypatch.chdir(git_repo)
    worktree.create("orphanrisk")

    real_git = worktree.git

    def flaky_git(args, **kwargs):
        if args[:1] == ["branch"]:  # deletion refuses (unmerged)
            return subprocess.CompletedProcess(args, 1, "", "error: not fully merged")
        if args[:1] == ["show-ref"]:  # and the follow-up cannot answer
            return subprocess.CompletedProcess(args, 128, "", "fatal: bad repository")
        return real_git(args, **kwargs)

    monkeypatch.setattr(worktree, "git", flaky_git)

    worktree.cleanup("orphanrisk")

    assert worktree.load_session("orphanrisk", git_repo) is not None


def test_cleanup_ignores_dep_dirs_and_the_tracker_redirect(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provisioned dep dirs and the tracker's own redirect never count as dirt."""
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
    """``git_repo`` with an owned ledger, so a worktree name can also be a record id."""
    return flipped_tracker.flipped_repo(git_repo)


def test_cli_create_binds_the_record_it_provisioned_for(
    tracked_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hand path derives ``build``; a record with no worktree still derives ``intake``.

    ``derive_phase`` reads ``build`` off the binding alone, so a lane this verb provisioned
    used to sit at ``intake`` with work in flight and no advance could land it. The control
    is what makes the assertion discriminating: both records are open tasks, and only the
    provisioning tells them apart.
    """
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
    """A second create writes no second binding, and provisions nothing.

    Refused before provisioning: the held binding names a lane whose branch may still
    hold unlanded commits, and overwriting the ref is what makes those unreachable.
    """
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
    """Provisioning for no tracked record is unchanged, against a tracker that can answer.

    A repo with no ledger would pass this by not being asked; here the lookup runs, finds
    nothing, and the ledger stays empty — so an unconditional write would fail the test.
    """
    monkeypatch.chdir(tracked_repo)

    assert cli.main(["worktree", "create", "not-a-record"]) == 0

    assert worktree.load_session("not-a-record", tracked_repo) is not None
    assert tracker.read_record(tracked_repo, "not-a-record") is None
    assert flipped_tracker.ledger_events(tracked_repo) == []
