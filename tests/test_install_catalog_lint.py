from __future__ import annotations

from typing import TYPE_CHECKING

from tests.test_cli import run_basicly_consumer

if TYPE_CHECKING:
    from pathlib import Path


def test_a_fresh_install_passes_its_own_catalog_lint(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()

    installed = run_basicly_consumer(consumer, "install")
    assert installed.returncode == 0, installed.stderr

    linted = run_basicly_consumer(consumer, "catalog", "lint")

    assert linted.returncode == 0, linted.stdout + linted.stderr
    assert "no floor declared" in linted.stdout + linted.stderr
