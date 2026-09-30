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
    for tool in ("git", "python3", "cat", "sh"):
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
