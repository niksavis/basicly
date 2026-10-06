from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_default_install_ignores_the_persistent_lock_and_preserves_existing_rules(
    tmp_path: Path,
) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    ignored = consumer / ".gitignore"
    ignored.write_text("mine/\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(consumer)], check=True, capture_output=True)
    environment = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    for _ in range(2):
        installed = subprocess.run(
            [sys.executable, "-m", "basicly.cli", "install"],
            cwd=consumer,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert installed.returncode == 0, installed.stdout + installed.stderr
    lines = ignored.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "mine/"
    assert lines.count(".basicly/ledger/.events.lock") == 1
    found = subprocess.run(
        ["git", "check-ignore", ".basicly/ledger/.events.lock"],
        cwd=consumer,
        capture_output=True,
        text=True,
        check=False,
    )
    assert found.returncode == 0
    assert found.stdout.strip() == ".basicly/ledger/.events.lock"
