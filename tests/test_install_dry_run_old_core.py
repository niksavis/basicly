from __future__ import annotations

import subprocess
from pathlib import Path

from tests.markdownlint_runtime_fixture import provision_markdownlint_double
from tests.test_install_baseline import _install, _old_core


def _old_consumer(tmp_path: Path) -> Path:
    consumer = tmp_path / "consumer"
    _old_core(consumer)
    provision_markdownlint_double(consumer)
    subprocess.run(["git", "init", "-q", str(consumer)], check=True)  # nosec B603 B607
    old_cli = consumer / ".basicly/core/kit/tracker/cli.py"
    subprocess.run(  # nosec B603 B607
        ["python3", str(old_cli), "create", ".basicly/ledger", "--prefix", "old", "--title", "x"],
        cwd=consumer,
        capture_output=True,
        check=True,
    )
    return consumer


def test_a_dry_run_over_an_old_core_reports_the_ledger_rules_it_would_write(
    tmp_path: Path,
) -> None:
    consumer = _old_consumer(tmp_path)

    result = _install(consumer, "--dry-run")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "pending-*.jsonl -text merge=union" in result.stdout


def test_a_real_install_over_an_old_core_writes_the_ledger_rules(tmp_path: Path) -> None:
    consumer = _old_consumer(tmp_path)

    result = _install(consumer)

    assert result.returncode == 0, result.stdout + result.stderr
    rules = (consumer / ".gitattributes").read_text(encoding="utf-8")
    assert "pending-*.jsonl -text merge=union" in rules
