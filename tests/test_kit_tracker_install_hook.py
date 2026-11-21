from __future__ import annotations

import io
import itertools
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.kit_deployment_helpers import KIT_RELATIVE, REPO_ROOT, _load, events

VENDORED = Path(".basicly") / "kit" / "tracker"
LEDGER = Path(".basicly") / "ledger"

FOREIGN = '#!/bin/sh\necho "somebody else was here"\n'

_loaded = itertools.count()


def _consumer(root: Path) -> Path:

    shutil.copytree(
        REPO_ROOT / KIT_RELATIVE, root / VENDORED, ignore=shutil.ignore_patterns("__pycache__")
    )
    (root / LEDGER).mkdir(parents=True, exist_ok=True)
    return root / VENDORED


def _module(root: Path):
    return _load(_consumer(root) / "install_hook.py", f"kit_hook_test_{next(_loaded)}")


@pytest.fixture
def repo(tmp_path: Path):
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def hook(repo: Path):
    return _module(repo)


def _install(hook, root: Path) -> str:
    stream = io.StringIO()
    assert (
        hook.install(root, ledger=root / LEDGER, dry_run=False, interpreter="RUNNER", stream=stream)
        == 0
    )
    return stream.getvalue()


def _uninstall(hook, root: Path) -> str:
    stream = io.StringIO()
    assert hook.uninstall(root, dry_run=False, stream=stream) == 0
    return stream.getvalue()


def _hook_file(hook, root: Path) -> Path:
    return root / ".git" / "hooks" / hook.HOOK_NAME


def test_a_fresh_checkout_gets_a_read_only_compaction_reminder(hook, repo: Path) -> None:
    _install(hook, repo)

    text = _hook_file(hook, repo).read_text(encoding="utf-8")
    assert text.startswith(hook.SHEBANG)
    assert "compact" in text
    assert hook.BEGIN in text and hook.END in text


def test_the_hook_carries_no_absolute_path_or_interpreter_invocation(hook, repo: Path) -> None:
    _install(hook, repo)

    text = _hook_file(hook, repo).read_text(encoding="utf-8")
    assert str(repo) not in text, "an absolute path breaks the next clone"
    assert "RUNNER" in text
    assert "tracker_run" not in text
    assert "printf" in text
    assert "outside Git hooks" in text
    assert "git commit" not in text


def test_the_hook_does_nothing_unless_the_ledger_is_the_only_dirt(hook, repo: Path) -> None:
    _install(hook, repo)

    text = _hook_file(hook, repo).read_text(encoding="utf-8")
    assert ":(exclude).basicly/ledger" in text, (
        "without this guard the hook rewrites a developer's tree on every pull: it "
        "deletes shards and modifies the trunk log they never touched"
    )


def test_an_existing_hook_is_added_to_rather_than_replaced(hook, repo: Path) -> None:
    _hook_file(hook, repo).write_text(FOREIGN, encoding="utf-8")

    _install(hook, repo)

    text = _hook_file(hook, repo).read_text(encoding="utf-8")
    assert "somebody else was here" in text
    assert hook.BEGIN in text


def test_a_second_install_changes_nothing(hook, repo: Path) -> None:
    _install(hook, repo)
    once = _hook_file(hook, repo).read_text(encoding="utf-8")

    said = _install(hook, repo)

    assert _hook_file(hook, repo).read_text(encoding="utf-8") == once
    assert "already reports" in said


def test_uninstall_removes_only_the_block_it_wrote(hook, repo: Path) -> None:
    _hook_file(hook, repo).write_text(FOREIGN, encoding="utf-8")
    _install(hook, repo)

    _uninstall(hook, repo)

    assert _hook_file(hook, repo).read_text(encoding="utf-8") == FOREIGN


def test_uninstall_removes_a_hook_that_held_nothing_else(hook, repo: Path) -> None:
    _install(hook, repo)

    _uninstall(hook, repo)

    assert not _hook_file(hook, repo).exists()


def test_a_hook_this_kit_never_wrote_is_left_alone(hook, repo: Path) -> None:
    _hook_file(hook, repo).write_text(FOREIGN, encoding="utf-8")

    said = _uninstall(hook, repo)

    assert _hook_file(hook, repo).read_text(encoding="utf-8") == FOREIGN
    assert "left alone" in said


def test_a_declared_hooks_path_is_honoured(tmp_path: Path) -> None:
    git = tmp_path / ".git"
    git.mkdir()
    (git / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n\thooksPath = .githooks\n", encoding="utf-8"
    )
    hook = _module(tmp_path)

    assert hook.hooks_dir(tmp_path) == tmp_path / ".githooks", (
        "a repository that moved its hooks would otherwise get a file nothing runs"
    )


def test_a_linked_worktree_resolves_to_the_real_git_directory(tmp_path: Path) -> None:
    real = tmp_path / "main" / ".git"
    (real / "hooks").mkdir(parents=True)
    linked = tmp_path / "wt"
    linked.mkdir()
    (linked / ".git").write_text(f"gitdir: {real / 'worktrees' / 'wt'}\n", encoding="utf-8")
    hook = _module(linked)

    found = hook.hooks_dir(linked)

    assert found is not None
    assert found.name == "hooks"


def test_a_directory_that_is_not_a_checkout_is_reported_not_raised(tmp_path: Path) -> None:
    hook = _module(tmp_path)
    stream = io.StringIO()

    assert (
        hook.install(
            tmp_path,
            ledger=tmp_path / LEDGER,
            dry_run=False,
            interpreter="RUNNER",
            stream=stream,
        )
        == 0
    )

    assert "not a git checkout" in stream.getvalue()


def test_a_dry_run_writes_nothing(hook, repo: Path) -> None:
    stream = io.StringIO()

    assert (
        hook.install(
            repo,
            ledger=repo / "ledger",
            dry_run=True,
            interpreter="RUNNER",
            stream=stream,
        )
        == 0
    )

    assert not _hook_file(hook, repo).exists()
    assert "would write" in stream.getvalue()


def test_a_host_command_replaces_the_kit_call(hook, repo: Path) -> None:
    stream = io.StringIO()

    assert (
        hook.install(
            repo,
            ledger=repo / LEDGER,
            dry_run=False,
            interpreter="RUNNER",
            stream=stream,
            command="basicly tracker fold",
        )
        == 0
    )

    written = _hook_file(hook, repo).read_text(encoding="utf-8")
    assert "basicly tracker fold" in written
    assert "RUNNER" not in written
    assert "git commit" not in written


def test_a_host_command_is_reported_without_execution(hook, repo: Path) -> None:
    hook.install(
        repo,
        ledger=repo / LEDGER,
        dry_run=False,
        interpreter="RUNNER",
        stream=io.StringIO(),
        command="basicly tracker fold",
    )

    written = _hook_file(hook, repo).read_text(encoding="utf-8")
    assert "pending shards are unchanged" in written
    assert "printf" in written
    assert "`" not in written


def test_install_creates_a_missing_ledger(hook, repo: Path) -> None:
    (repo / LEDGER).rmdir()

    assert "created the ledger" in _install(hook, repo)
    assert (repo / LEDGER).is_dir()


def test_a_dry_run_names_the_missing_ledger_and_creates_nothing(hook, repo: Path) -> None:
    (repo / LEDGER).rmdir()
    stream = io.StringIO()

    hook.install(repo, ledger=repo / LEDGER, dry_run=True, interpreter="RUNNER", stream=stream)

    assert "would create the ledger" in stream.getvalue()
    assert not (repo / LEDGER).exists()


_POSIX_HOOK = pytest.mark.skipif(sys.platform == "win32", reason="runs the hook with /bin/sh")


def _tool_dir(tmp_path: Path, python: str | None) -> Path:
    tools = tmp_path / "tools"
    tools.mkdir()
    git = shutil.which("git")
    assert git
    (tools / "git").symlink_to(git)
    if python is not None:
        (tools / "python3").symlink_to(python)
    return tools


def _checkout_with_a_shard(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    kit = _consumer(root)
    (root / ".gitignore").write_text(
        f"{VENDORED.as_posix()}/__pycache__/\n{LEDGER.as_posix()}/snapshot.jsonl\n",
        encoding="utf-8",
    )
    subprocess.run(
        [sys.executable, str(kit / "cli.py"), "create", str(root / LEDGER), "--prefix", "acme"],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "s"],
        check=True,
    )
    hook = _load(kit / "install_hook.py", f"kit_hook_test_{next(_loaded)}")
    _install(hook, root)
    return root


def _run_hook(root: Path, tools: Path) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": str(tools),
        "HOME": str(root),
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    return subprocess.run(
        ["/bin/sh", str(root / ".git" / "hooks" / "post-merge")],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _shards(root: Path) -> list[str]:
    return sorted(path.name for path in (root / LEDGER).glob("pending-*.jsonl"))


@_POSIX_HOOK
def test_the_hook_reports_compaction_without_folding_when_python_is_present(tmp_path: Path) -> None:
    root = _checkout_with_a_shard(tmp_path)
    assert _shards(root) == ["pending-main.jsonl"]

    done = _run_hook(root, _tool_dir(tmp_path, sys.executable))

    assert done.returncode == 0
    assert "pending shards are unchanged" in done.stderr
    assert _shards(root) == ["pending-main.jsonl"]
    log = subprocess.run(
        ["git", "-C", str(root), "log", "-1", "--format=%s"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert log.stdout.strip() == "s"


@_POSIX_HOOK
def test_the_hook_reports_compaction_without_needing_an_interpreter(tmp_path: Path) -> None:
    root = _checkout_with_a_shard(tmp_path)

    done = _run_hook(root, _tool_dir(tmp_path, None))

    assert done.returncode == 0
    assert "pending shards are unchanged" in done.stderr
    assert "compact .basicly/ledger" in done.stderr
    assert _shards(root) == ["pending-main.jsonl"]


@_POSIX_HOOK
def test_the_hook_does_not_invoke_a_failing_interpreter(tmp_path: Path) -> None:
    root = _checkout_with_a_shard(tmp_path)
    failing = tmp_path / "failing-python"
    failing.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    failing.chmod(0o755)

    done = _run_hook(root, _tool_dir(tmp_path, str(failing)))

    assert done.returncode == 0
    assert "pending shards are unchanged" in done.stderr
    assert "compact .basicly/ledger" in done.stderr
    assert _shards(root) == ["pending-main.jsonl"]


@_POSIX_HOOK
def test_the_hook_leaves_a_feature_branch_unfolded(tmp_path: Path) -> None:
    root = _checkout_with_a_shard(tmp_path)
    subprocess.run(["git", "-C", str(root), "checkout", "-q", "-b", "feature"], check=True)

    done = _run_hook(root, _tool_dir(tmp_path, sys.executable))

    assert done.returncode == 0
    assert done.stderr == ""
    assert _shards(root) == ["pending-main.jsonl"], (
        "a fold on a feature branch makes its pull request edit the shared trunk log, "
        "which the forge reports as conflicting (basicly-k1pxru4.3)"
    )


@_POSIX_HOOK
def test_the_hook_reports_on_the_branch_the_remote_names_default(tmp_path: Path) -> None:
    root = _checkout_with_a_shard(tmp_path)
    subprocess.run(["git", "-C", str(root), "checkout", "-q", "-b", "trunk"], check=True)
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    git = ["git", "-C", str(root)]
    subprocess.run([*git, "update-ref", "refs/remotes/origin/trunk", head], check=True)
    subprocess.run(
        [*git, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk"], check=True
    )

    done = _run_hook(root, _tool_dir(tmp_path, sys.executable))

    assert done.returncode == 0
    assert "pending shards are unchanged" in done.stderr
    assert _shards(root) == ["pending-main.jsonl"]


@_POSIX_HOOK
def test_a_shared_hook_stays_silent_in_a_linked_checkout_without_the_tracker(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "feature", str(root)], check=True)
    for key, value in (("user.name", "Test"), ("user.email", "test@example.invalid")):
        subprocess.run(["git", "-C", str(root), "config", key, value], check=True)
    (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    ledger = root / LEDGER
    ledger.mkdir(parents=True)
    events.append(
        ledger,
        [events.Draft("probe-linked", events.KIND_CREATED, {"title": "Pending linked record"})],
        actor="test",
        writer="feature",
    )
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "seed"], check=True)
    linked = root.parent / (root.name + ".worktrees") / "main"
    linked.parent.mkdir()
    subprocess.run(
        ["git", "-C", str(root), "worktree", "add", "-q", "-b", "main", str(linked)], check=True
    )
    hook = _module(root)
    _install(hook, root)
    assert not (linked / VENDORED / "cli.py").exists()
    before = (linked / LEDGER / "pending-feature.jsonl").read_bytes()

    done = subprocess.run(
        ["git", "hook", "run", "post-merge"],
        cwd=linked,
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
    assert (linked / LEDGER / "pending-feature.jsonl").read_bytes() == before


def _main(root: Path, *extra: str, module=None) -> str:
    module = module or _module(root)
    ledger = root / LEDGER
    if not any(ledger.iterdir()):
        ledger.rmdir()
    stream = io.StringIO()
    saved = sys.stdout
    sys.stdout = stream
    try:
        module.main(["--root", str(root), "--ledger", str(ledger), "--pin", *extra])
    finally:
        sys.stdout = saved
    return stream.getvalue()


@pytest.mark.parametrize(("folder", "prefix"), [("Shop-App_2", "shopapp2"), ("demo", "demo")])
def test_a_pinned_init_names_the_prefix_from_the_folder_and_how_to_change_it(
    tmp_path: Path, folder: str, prefix: str
) -> None:
    root = tmp_path / folder
    (root / ".git" / "hooks").mkdir(parents=True)

    said = _main(root, "--runner", "basicly-tracker")

    assert f"the id prefix is {prefix}" in said
    assert "basicly-tracker config .basicly/ledger set prefix NAME" in said
    assert "--prefix NAME" in said
    template = (root / LEDGER / "template.json").read_text(encoding="utf-8")
    assert f'"prefix": "{prefix}"' in template


def test_a_pinned_init_keeps_a_prefix_the_ledger_already_sets(tmp_path: Path) -> None:
    root = tmp_path / "demo"
    (root / ".git" / "hooks").mkdir(parents=True)
    _main(root)
    settings = _load(root / VENDORED / "settings.py", f"kit_settings_{next(_loaded)}")
    settings.write(root / LEDGER, "prefix", "kept")
    module = _load(root / VENDORED / "install_hook.py", f"kit_hook_test_{next(_loaded)}")

    said = _main(root, module=module)

    assert "the id prefix is" not in said
    assert '"prefix": "kept"' in (root / LEDGER / "template.json").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("name", "derived"),
    [
        ("My Project", "myproject"),
        ("2048-game", "game"),
        ("averyveryverylongrepositoryname", "averyveryver"),
        ("---", "work"),
        ("1234", "work"),
        ("ćevapi", "evapi"),
    ],
)
def test_a_derived_prefix_follows_the_prefix_rules_or_takes_the_named_fallback(
    name: str, derived: str
) -> None:
    settings = _load(REPO_ROOT / KIT_RELATIVE / "settings.py", f"kit_settings_{next(_loaded)}")

    assert settings.derived_prefix(name) == derived
    assert settings.ids.validate_prefix(derived) == derived
