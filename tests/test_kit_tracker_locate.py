from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

KIT_DIR = Path(__file__).parent.parent / ".basicly" / "core" / "kit" / "tracker"
SHAPED = [
    "--description",
    "When I commit work, I want the tracker to know who holds it, so I can trust it.",
    "--acceptance",
    "- [ ] it knows",
    "--requirements",
    "- none",
]


def _module():
    spec = importlib.util.spec_from_file_location(
        "basicly_tracker_kit_locate", KIT_DIR / "locate.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


locate = _module()


PYZ = ".basicly/tracker.pyz"
FOLDER = ".basicly/kit/tracker/cli.py"
PLACES = (PYZ, FOLDER)


def _never(_name: str) -> None:
    return None


def _touch(root: Path, relative: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def test_the_first_place_that_holds_a_file_wins(tmp_path: Path) -> None:
    for relative in PLACES:
        _touch(tmp_path, relative)

    assert locate.locate(tmp_path, PLACES, _never) == locate.Tracker(PYZ, "")


def test_a_later_place_wins_when_the_first_is_empty(tmp_path: Path) -> None:
    _touch(tmp_path, FOLDER)

    found = locate.locate(tmp_path, PLACES, lambda _name: "/opt/bin/basicly-tracker")

    assert found == locate.Tracker(FOLDER, "")
    assert found.typed() == f"python3 {FOLDER}"


def test_the_user_install_is_the_last_place(tmp_path: Path) -> None:
    found = locate.locate(tmp_path, PLACES, lambda _name: "/opt/bin/basicly-tracker")

    assert found == locate.Tracker("", "/opt/bin/basicly-tracker")
    assert found.typed() == "basicly-tracker"


def test_nothing_found_is_none_and_the_message_names_the_pinned_install(tmp_path: Path) -> None:
    ledger = tmp_path / "data" / "ledger"
    ledger.mkdir(parents=True)
    (ledger / ".kit-version").write_text("0.18.20\n", encoding="utf-8")

    assert locate.locate(tmp_path, PLACES, _never) is None
    message = locate.missing(tmp_path, "data/ledger", PLACES)
    assert "basicly@v0.18.20#subdirectory=packages/basicly-tracker" in message
    assert "ledger at data/ledger" in message and FOLDER in message


def test_the_package_orders_the_sandbox_file_before_the_vendored_folder() -> None:
    spec = importlib.util.spec_from_file_location(
        "basicly_tracker_under_test",
        Path(__file__).parent.parent
        / "packages"
        / "basicly-tracker"
        / "basicly_tracker"
        / "__init__.py",
    )
    assert spec is not None and spec.loader is not None
    package = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(package)

    assert package.PLACES == PLACES
    assert package.MANAGED == (".basicly/kit/", PYZ, ".basicly/board.pyz")


def _git(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603 B607
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False, env=env
    )


def _install(root: Path) -> None:
    hook = root / ".basicly/kit/tracker/install_hook.py"
    done = subprocess.run(  # nosec B603
        [sys.executable, str(hook), "--root", str(root), "--pin"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr


def _bare_path(tmp_path: Path) -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for tool in ("git", "python3", "cat", "sh", "mktemp", "mkdir", "find"):
        found = shutil.which(tool)
        assert found is not None
        link = bin_dir / tool
        if not link.exists():
            link.symlink_to(found)
    return {**os.environ, "PATH": str(bin_dir)}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "consumer"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Dev A")
    _git(root, "config", "user.email", "dev-a@example.invalid")
    ignored = shutil.ignore_patterns("__pycache__")
    shutil.copytree(KIT_DIR, root / ".basicly" / "kit" / "tracker", ignore=ignored)
    (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    _install(root)
    done = subprocess.run(  # nosec B603
        [
            sys.executable,
            ".basicly/kit/tracker/cli.py",
            "create",
            ".basicly/ledger",
            *("--prefix", "acme", "--title", "x", *SHAPED),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    (root / "record.txt").write_text(json.loads(done.stdout)["record"], encoding="utf-8")
    return root


@pytest.mark.skipif(sys.platform == "win32", reason="the hook fixture links POSIX tools")
def test_a_ledger_with_no_tracker_refuses_the_commit_with_the_install_command(
    repo: Path, tmp_path: Path
) -> None:
    shutil.rmtree(repo / ".basicly" / "kit")
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")

    refused = _git(repo, "commit", "-q", "-m", "feat: add value", env=_bare_path(tmp_path))

    assert refused.returncode != 0
    assert "no tracker was found" in refused.stderr
    assert "uv tool install --force git+https://github.com/niksavis/basicly@v" in refused.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="the hook fixture links POSIX tools")
def test_the_user_install_on_path_runs_the_claim_check(repo: Path, tmp_path: Path) -> None:
    kit = tmp_path / "user-kit"
    shutil.move(str(repo / ".basicly" / "kit" / "tracker"), str(kit))
    env = _bare_path(tmp_path)
    command = Path(env["PATH"]) / "basicly-tracker"
    command.write_text(f'#!/bin/sh\nexec python3 "{kit / "cli.py"}" "$@"\n', encoding="utf-8")
    command.chmod(command.stat().st_mode | stat.S_IXUSR)
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    record = (repo / "record.txt").read_text(encoding="utf-8")

    refused = _git(repo, "commit", "-q", "-m", f"feat: add value {record}", env=env)

    assert refused.returncode != 0
    assert f"basicly-tracker claim .basicly/ledger {record}" in refused.stderr


def test_typed_in_names_the_repository_path_or_the_installed_command(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    inside = repo / ".basicly" / "kit" / "tracker" / "cli.py"
    inside.parent.mkdir(parents=True)
    inside.write_text("", encoding="utf-8")
    (repo / ".git").mkdir()
    sub = repo / "src" / "app"
    sub.mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()

    assert locate.typed_in(inside, repo) == "python3 .basicly/kit/tracker/cli.py"
    typed = locate.typed_in(inside, sub)
    assert typed == "python3 ../../.basicly/kit/tracker/cli.py"
    assert (sub / typed.split(" ")[1]).resolve() == inside.resolve()
    assert locate.typed_in(inside, tmp_path / "elsewhere") == "basicly-tracker"


@pytest.mark.parametrize(
    ("place", "runner"),
    [
        ("repo/.basicly/kit/tracker", "python3 .basicly/kit/tracker/cli.py"),
        ("tools/lib/site-packages/basicly_tracker/kit", "basicly-tracker"),
    ],
)
def test_a_refusal_remedy_never_names_a_path_outside_the_repository(
    tmp_path: Path, place: str, runner: str
) -> None:
    kit = tmp_path / place
    shutil.copytree(KIT_DIR, kit, ignore=shutil.ignore_patterns("__pycache__"))
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)

    def run(*argv: str) -> dict:
        done = subprocess.run(  # nosec B603 — the copied kit under test
            [sys.executable, str(kit / "cli.py"), *argv],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.stdout, done.stderr
        return json.loads(done.stdout)

    created = run(
        "create",
        ".basicly/ledger",
        "--prefix",
        "acme",
        "--title",
        "A bug",
        "--field",
        "issue_type=bug",
        "--description",
        "## Context\nIt breaks.",
        "--acceptance",
        "- it works",
    )
    assert "record" in created, created
    record = created["record"]

    refused = run("close", ".basicly/ledger", record, "--reason", "done")["refused"]

    assert f"next: {runner} review .basicly/ledger {record}" in refused
    assert str(tmp_path) not in refused


def test_no_tracker_output_names_an_absolute_path_from_a_repository_subfolder(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    kit = repo / ".basicly" / "kit" / "tracker"
    shutil.copytree(KIT_DIR, kit, ignore=shutil.ignore_patterns("__pycache__"))
    clean = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=clean)  # nosec B603 B607
    sub = repo / "src" / "app"
    sub.mkdir(parents=True)
    ledger = "../../.basicly/ledger"

    def run(*argv: str) -> str:
        done = subprocess.run(  # nosec B603 — the copied kit under test
            [sys.executable, str(kit / "cli.py"), *argv],
            cwd=sub,
            capture_output=True,
            text=True,
            check=False,
        )
        return done.stdout

    outputs = [run("create", ledger, "--title", "No prefix yet")]
    created = run(
        "create",
        ledger,
        "--prefix",
        "acme",
        "--title",
        "A bug",
        "--field",
        "issue_type=bug",
        "--description",
        "## Context\nIt breaks.",
        "--acceptance",
        "- it works",
    )
    outputs.append(created)
    assert "record" in json.loads(created), outputs
    record = json.loads(created)["record"]
    outputs.extend(
        run(*argv)
        for argv in (
            ("show", ledger, record),
            ("close", ledger, record, "--reason", "done"),
            ("claim", ledger, record, "--to", "a-holder"),
            ("claim", ledger, "acme-none", "--to", "a-holder"),
            ("update", ledger, record, "--status", "in_progress"),
            ("ready", ledger),
            ("blocked", ledger),
            ("stats", ledger),
            ("refine", ledger),
            ("dor", ledger, record),
            ("fsck", ledger),
            ("list", ledger),
        )
    )

    assert all(outputs), outputs
    leaked = [one for one in outputs if str(tmp_path) in one or str(repo.resolve()) in one]
    assert leaked == []
    assert any("python3 ../../.basicly/kit/tracker/cli.py" in one for one in outputs)
