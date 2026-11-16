from __future__ import annotations

import importlib.util
import json
import os
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import Any

import pytest

HOOKS = Path(__file__).resolve().parents[1] / ".basicly" / "core" / "hooks"
PINNED = "9.8.7"
OLDER = "9.0.0"


def _tool(folder: Path, name: str, version: str, log: Path) -> None:
    body = (
        "import json, sys\n"
        f"log = {str(log)!r}\n"
        "if sys.argv[1:] == ['--version']:\n"
        f"    print('basicly {version}')\n"
        "    raise SystemExit(0)\n"
        "with open(log, 'a', encoding='utf-8') as stream:\n"
        f"    stream.write(json.dumps([{name!r}, *sys.argv[1:]]) + '\\n')\n"
        "print('ledger: stub report')\n"
    )
    script = folder / f"{name}_stub.py"
    script.write_text(body, encoding="utf-8")
    if os.name == "nt":
        (folder / f"{name}.cmd").write_text(
            f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8"
        )
    else:
        launcher = folder / name
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        launcher.chmod(0o755)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    state = root / ".basicly/state/install.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"basicly_version": PINNED}), encoding="utf-8")
    return root


def _path(tmp_path: Path, *, basicly: str | None, uvx: bool) -> tuple[str, Path]:
    folder = tmp_path / "bin"
    folder.mkdir()
    log = tmp_path / "calls.jsonl"
    if basicly:
        _tool(folder, "basicly", basicly, log)
    if uvx:
        _tool(folder, "uvx", "0.0.0", log)
    return str(folder), log


def _calls(log: Path) -> list[list[str]]:
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def _run(hook: str, repo: Path, path: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PATH": path}
    return subprocess.run(  # nosec B603
        [sys.executable, str(HOOKS / hook)],
        cwd=repo,
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def test_catalog_lint_runs_the_pinned_version_when_an_older_basicly_is_on_path(
    tmp_path: Path, repo: Path
) -> None:
    path, log = _path(tmp_path, basicly=OLDER, uvx=True)

    shown = _run("catalog-lint.py", repo, path)

    assert shown.returncode == 0, shown.stderr
    assert _calls(log) == [
        [
            "uvx",
            "--from",
            f"https://github.com/niksavis/basicly/releases/download/v{PINNED}/"
            f"basicly-{PINNED}-py3-none-any.whl",
            "basicly",
            "catalog",
            "lint",
        ]
    ]
    assert OLDER in shown.stderr and PINNED in shown.stderr


def test_catalog_lint_refuses_by_name_when_only_an_older_basicly_is_reachable(
    tmp_path: Path, repo: Path
) -> None:
    path, log = _path(tmp_path, basicly=OLDER, uvx=False)

    shown = _run("catalog-lint.py", repo, path)

    assert shown.returncode != 0
    assert _calls(log) == []
    assert OLDER in shown.stderr and PINNED in shown.stderr
    assert "uv tool install --force" in shown.stderr


def test_catalog_lint_runs_basicly_on_path_when_its_version_matches(
    tmp_path: Path, repo: Path
) -> None:
    path, log = _path(tmp_path, basicly=PINNED, uvx=True)

    shown = _run("catalog-lint.py", repo, path)

    assert shown.returncode == 0, shown.stderr
    assert _calls(log) == [["basicly", "catalog", "lint"]]


def test_session_start_refuses_by_name_when_an_older_basicly_is_on_path(
    tmp_path: Path, repo: Path
) -> None:
    path, log = _path(tmp_path, basicly=OLDER, uvx=True)

    shown = _run("session-start.py", repo, path, "{}")

    assert shown.returncode == 2
    assert _calls(log) == []
    assert OLDER in shown.stderr and PINNED in shown.stderr


def test_session_start_runs_basicly_on_path_when_its_version_matches(
    tmp_path: Path, repo: Path
) -> None:
    path, log = _path(tmp_path, basicly=PINNED, uvx=False)

    shown = _run("session-start.py", repo, path, "{}")

    assert shown.returncode == 0, shown.stderr
    assert _calls(log) == [["basicly", "session", "start"]]


def _fold_hook() -> Any:
    spec = importlib.util.spec_from_file_location(
        "engine_pin_tracker_fold", HOOKS / "tracker-fold.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("version", "advice"),
    [(OLDER, ["uvx", "--from", "pinned", "basicly", "tracker", "fold"]), (PINNED, None)],
    ids=["older-advises-pinned", "matching-advises-path"],
)
def test_tracker_fold_advises_basicly_on_path_only_when_its_version_matches(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch, version: str, advice: Any
) -> None:
    path, log = _path(tmp_path, basicly=version, uvx=True)
    pinned = ["uvx", "--from", "pinned", "basicly", "tracker", "fold"]
    monkeypatch.setenv("PATH", path)
    monkeypatch.setenv("BASICLY_FOLD_COMMAND", json.dumps(pinned))

    shown = _fold_hook()._fold_command(repo)

    assert shown == (advice or ["basicly", "tracker", "fold"])
    assert _calls(log) == []
