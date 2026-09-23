from __future__ import annotations

import json
import os
import subprocess  # nosec B404
import sys
import zipfile
from pathlib import Path

import pytest

PACKAGE = Path(__file__).parent.parent / "packages" / "basicly-tracker"


@pytest.fixture(scope="module")
def pyz(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("bundle")
    subprocess.run(  # nosec B603 B607
        ["uv", "build", "--wheel", str(PACKAGE), "--out-dir", str(root / "wheel")],
        check=True,
        capture_output=True,
    )
    with zipfile.ZipFile(next((root / "wheel").glob("*.whl"))) as wheel:
        wheel.extractall(root / "site")
    out = root / "tracker.pyz"
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import basicly_tracker; "
        "sys.exit(basicly_tracker.main(['bundle', '--out', sys.argv[2]]))"
    )
    subprocess.run(  # nosec B603
        [sys.executable, "-c", code, str(root / "site"), str(out)], check=True, capture_output=True
    )
    return out


def _run(pyz: Path, cwd: Path, cache: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "XDG_CACHE_HOME": str(cache), "LOCALAPPDATA": str(cache)}
    return subprocess.run(  # nosec B603
        [sys.executable, str(pyz), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_archive_installs_and_runs_the_tracker(pyz: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)  # nosec B603 B607
    cache = tmp_path / "cache"

    installed = _run(pyz, repo, cache, "init")
    assert installed.returncode == 0, installed.stderr
    assert "merge=union" in (repo / ".gitattributes").read_text(encoding="utf-8")

    ledger = str(Path(".basicly") / "ledger")
    created = _run(pyz, repo, cache, "create", ledger, "--prefix", "acme", "--title", "a")
    assert created.returncode == 0, created.stderr
    record = json.loads(created.stdout)["record"]

    ready = _run(pyz, repo, cache, "ready", ledger)
    assert [row["record"] for row in json.loads(ready.stdout)["records"]] == [record]


def test_the_archive_unpacks_once_per_content(pyz: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"

    for _ in range(2):
        assert _run(pyz, tmp_path, cache, "--help").returncode == 0

    assert len(list((cache / "basicly-kits").iterdir())) == 1


def test_bundle_refuses_the_source_tree(tmp_path: Path) -> None:
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import basicly_tracker; "
        "sys.exit(basicly_tracker.main(['bundle', '--out', sys.argv[2]]))"
    )
    done = subprocess.run(  # nosec B603
        [sys.executable, "-c", code, str(PACKAGE), str(tmp_path / "x.pyz")],
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode != 0
    assert "bundle runs from the built package" in done.stderr
    assert not (tmp_path / "x.pyz").exists()
