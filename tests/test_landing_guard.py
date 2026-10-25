from __future__ import annotations

import io
import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

from basicly import checkout, landing_guard, tracker
from tests import flipped_tracker
from tests.test_merge import _add_source, _git_here, _lane_worktree, _seed_repo


@pytest.fixture
def landing(tmp_path: Path):
    repo = _seed_repo(tmp_path)
    session = _lane_worktree(tmp_path, repo)
    _add_source(session.path, "b")
    return repo, session, landing_guard.capture(repo, session, "full")


def test_guarded_publication_keeps_exact_parents_tree_and_existing_hooks(landing) -> None:
    repo, session, subject = landing
    normal = "from pathlib import Path; Path('.git/normal-hook-ran').write_text('ran')"
    _git_here(repo, "config", "hook.existing-msg.event", "commit-msg")
    _git_here(
        repo, "config", "hook.existing-msg.command", shlex.join([sys.executable, "-c", normal])
    )
    subject = landing_guard.capture(repo, session, "full")
    result = landing_guard.guarded_merge(
        repo, subject, ["merge", "--no-ff", subject.candidate, "-m", "guarded merge"]
    )
    assert result.returncode == 0, result.stderr
    assert _git_here(repo, "show", "-s", "--format=%P%n%T", "HEAD").splitlines() == [
        f"{subject.destination} {subject.candidate}",
        subject.tree,
    ]
    assert (repo / ".git/normal-hook-ran").read_text() == "ran"
    assert not _git_here(repo, "status", "--porcelain")


@pytest.mark.parametrize(
    "change", ["branch", "destination", "source", "untracked", "runtime", "policy"]
)
def test_changes_after_capture_are_refused_before_publication(landing, change: str) -> None:
    repo, session, subject = landing
    if change == "branch":
        _git_here(session.path, "commit", "--allow-empty", "-m", "candidate moved")
    elif change == "destination":
        _git_here(repo, "commit", "--allow-empty", "-m", "destination moved")
    elif change == "source":
        (session.path / "sources/a.txt").write_text("changed")
    elif change == "untracked":
        (session.path / "untracked.txt").write_text("new input")
    elif change == "runtime":
        path = session.path / ".venv/Lib/site-packages"
        path.mkdir(parents=True)
        (path / "runtime.py").write_text("changed")
    else:
        (session.path / "basicly.toml").write_text("[verify]\nfail_fast=false\n")
    before = _git_here(repo, "rev-parse", "HEAD")
    assert landing_guard.changed(repo, subject)
    result = landing_guard.guarded_merge(
        repo, subject, ["merge", "--no-ff", subject.candidate, "-m", "must refuse"]
    )
    assert result.returncode != 0
    assert _git_here(repo, "rev-parse", "HEAD") == before


def test_destination_movement_in_the_merge_call_is_rejected_by_the_locked_guard(
    landing, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _session, subject = landing
    assert not landing_guard.changed(repo, subject)
    original = checkout.git
    moved = []

    def publish(args, **kwargs):
        if "merge" in args:
            original(["commit", "--allow-empty", "-m", "destination raced"], cwd=repo)
            moved.append(original(["rev-parse", "HEAD"], cwd=repo).stdout.strip())
        return original(args, **kwargs)

    monkeypatch.setattr(checkout, "git", publish)
    result = landing_guard.guarded_merge(
        repo, subject, ["merge", "--no-ff", subject.candidate, "-m", "must refuse race"]
    )
    assert result.returncode != 0
    assert _git_here(repo, "rev-parse", "HEAD") == moved[0]


def test_changed_branch_after_precheck_cannot_substitute_the_merged_candidate(
    landing, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, session, subject = landing
    original = checkout.git

    def publish(args, **kwargs):
        if "merge" in args:
            original(["commit", "--allow-empty", "-m", "candidate raced"], cwd=session.path)
        return original(args, **kwargs)

    monkeypatch.setattr(checkout, "git", publish)
    result = landing_guard.guarded_merge(
        repo, subject, ["merge", "--no-ff", subject.candidate, "-m", "must refuse substitution"]
    )
    assert result.returncode != 0
    assert _git_here(repo, "rev-parse", "HEAD") == subject.destination


def test_unverified_destination_blocks_other_landings_and_close_until_reverified(
    tmp_path: Path,
) -> None:
    repo = _seed_repo(tmp_path)
    flipped_tracker.flipped_repo(repo)
    flipped_tracker.seed(repo, "basicly-owner", title="failed landing")
    landing_guard.record_verification(repo, "basicly-owner", passed=False)
    assert "basicly-owner" in landing_guard.blocker(repo, "basicly-other")
    assert landing_guard.blocker(repo, "basicly-owner") == ""
    assert landing_guard.blocker(repo, "basicly-owner", allow_owner=False)
    landing_guard.record_verification(
        repo, "basicly-owner", passed=True, proof=landing_guard.destination_inputs(repo, "full")
    )
    assert not landing_guard.blocker(repo, "basicly-other")
    fact = tracker.read_artifacts(repo, "basicly-owner")["landing-verification"]
    assert isinstance(fact, dict)
    assert fact["status"] == "verified"


def test_disabled_reference_event_refuses_before_checkout_mutation(landing) -> None:
    repo, _session, subject = landing
    _git_here(repo, "config", "hook.reference-transaction.enabled", "false")
    with pytest.raises(landing_guard.GuardUnavailableError, match="disabled"):
        landing_guard.guarded_merge(
            repo, subject, ["merge", "--no-ff", subject.candidate, "-m", "must refuse"]
        )
    assert _git_here(repo, "rev-parse", "HEAD") == subject.destination
    assert not _git_here(repo, "status", "--porcelain")


def test_disabling_the_reference_event_after_probe_cannot_bypass_publication_guard(
    landing, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _session, subject = landing
    original = checkout.git

    def publish(args, **kwargs):
        if "merge" in args:
            original(["config", "hook.reference-transaction.enabled", "false"], cwd=repo)
        return original(args, **kwargs)

    monkeypatch.setattr(checkout, "git", publish)
    result = landing_guard.guarded_merge(
        repo, subject, ["merge", "--no-ff", subject.candidate, "-m", "must refuse"]
    )
    assert result.returncode != 0
    assert _git_here(repo, "rev-parse", "HEAD") == subject.destination


@pytest.mark.parametrize("value", [{}, [], {"components": None}, {"base": 3}])
def test_malformed_subject_is_refused_without_publishing(
    landing, monkeypatch: pytest.MonkeyPatch, value, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, _session, subject = landing
    directory = checkout.git_common_dir(repo) / "basicly-landing"
    directory.mkdir()
    path = directory / ("a" * 32 + ".json")
    path.write_text(json.dumps(value))
    monkeypatch.chdir(repo)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert landing_guard.main([str(path), "prepared"]) == 1
    assert "Landing publication refused" in capsys.readouterr().err
    assert _git_here(repo, "rev-parse", "HEAD") == subject.destination


def test_interrupted_publication_keeps_a_durable_unverified_barrier(
    landing, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _session, original = landing
    flipped_tracker.flipped_repo(repo)
    flipped_tracker.seed(repo, "basicly-owner", title="interrupted landing")
    subject = landing_guard.capture(repo, _session, "full", record="basicly-owner")
    directory = checkout.git_common_dir(repo) / "basicly-landing"
    directory.mkdir()
    path = directory / ("b" * 32 + ".json")
    path.write_text(json.dumps(asdict(subject)))
    tree = _git_here(repo, "rev-parse", f"{subject.candidate}^{{tree}}")
    proposed = _git_here(
        repo,
        "commit-tree",
        tree,
        "-p",
        subject.destination,
        "-p",
        subject.candidate,
        "-m",
        "publication interrupted",
    )
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(f"{subject.destination} {proposed} refs/heads/main\n")
    )
    write = Path.write_text

    def interrupted(target, *args, **kwargs):
        if target.suffix == ".receipt":
            raise OSError("publication interrupted")
        return write(target, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", interrupted)
    assert landing_guard.main([str(path), "prepared"]) == 1
    assert "basicly-owner" in landing_guard.blocker(repo, "basicly-other")
    assert _git_here(repo, "rev-parse", "HEAD") == original.destination


@pytest.mark.parametrize("origin", ["destination", "candidate"])
def test_self_hosted_engine_update_uses_a_stable_candidate_engine(
    tmp_path: Path, origin: str
) -> None:
    repo = _seed_repo(tmp_path)
    package = repo / "src/basicly"
    shutil.copytree(
        Path(landing_guard.__file__).parent,
        package,
        ignore=shutil.ignore_patterns("__pycache__", "lefthook_assets"),
    )
    shutil.copytree(
        Path(landing_guard.__file__).resolve().parents[2] / ".basicly/core",
        repo / ".basicly/core",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    _git_here(repo, "add", "src", ".basicly/core")
    _git_here(repo, "commit", "-m", "seed engine")
    lane = _lane_worktree(tmp_path, repo)
    (lane.path / "src/basicly/verify_receipt.py").write_text(
        (package / "verify_receipt.py").read_text() + "\nSELF_HOSTED_GENERATION = 2\n"
    )
    _git_here(lane.path, "commit", "-am", "update engine")
    runtime = tmp_path / "runtime"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(runtime)], check=True)
    site = (
        next(runtime.glob("lib/python*/site-packages"))
        if os.name != "nt"
        else runtime / "Lib/site-packages"
    )
    shutil.copytree(
        Path(yaml.__file__).parent.parent,
        site,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("*.pth", "__pycache__"),
    )
    interpreter = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    source = repo if origin == "destination" else lane.path
    program = (
        "from basicly import landing_guard;from types import SimpleNamespace;"
        "from pathlib import Path;"
        f"s=SimpleNamespace(base='main',branch='harness/feat',path=Path({str(lane.path)!r}));"
        "r=Path.cwd();s=landing_guard.capture(r,s,'full');"
        "p=landing_guard.guarded_merge(r,s,['merge','--no-ff',s.candidate,'-m','self update']);"
        "print('published',p.returncode);raise SystemExit(p.returncode)"
    )
    result = subprocess.run(
        [str(interpreter), "-c", program],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(source / "src"),
            "PATH": str(interpreter.parent) + os.pathsep + os.environ["PATH"],
        },
    )
    if origin == "candidate":
        assert result.returncode == 0, result.stdout + result.stderr
        assert _git_here(repo, "rev-parse", "HEAD^2") == _git_here(lane.path, "rev-parse", "HEAD")
    else:
        assert result.returncode != 0
        assert "rerun from the candidate worktree" in result.stderr
        assert not _git_here(repo, "status", "--porcelain")
