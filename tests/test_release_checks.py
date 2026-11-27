from __future__ import annotations

from pathlib import Path

import pytest

from basicly import release_checks
from tests.release_repo_fixture import _git, seeded


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return seeded(tmp_path)


@pytest.mark.parametrize(
    ("changed", "said"),
    [
        (".basicly/ledger/events.jsonl", "live-ledger tests: run, 1 path(s) of its inputs changed"),
        ("docs/other.md", "live-ledger tests: skipped, no path of its inputs changed since"),
    ],
)
def test_release_checks_run_the_record_checks_and_the_live_ledger_tests_only_on_changed_inputs(
    repo: Path, changed: str, said: str
) -> None:
    config = repo / "basicly.toml"
    before = config.read_text(encoding="utf-8") if config.exists() else ""
    config.write_text(
        before + '\n[[verify.checks]]\nname = "ledger-fsck"\ncommand = ["true"]\n'
        'modes = ["fast", "full"]\nlocal_only = true\n'
        '\n[[verify.checks]]\nname = "ruff"\ncommand = ["true"]\nmodes = ["full"]\n'
        f'\n[[verify.checks]]\nname = "{release_checks.LIVE_LEDGER}"\n'
        'command = ["pytest", "-n", "{workers}", "-m", "live_ledger"]\n'
        'modes = ["fast", "full"]\nlocal_only = true\ninputs = [".basicly/ledger/**"]\n',
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: declare the record checks")
    _git(repo, "tag", "-a", "v0.5.2", "-m", "v0.5.2")
    (repo / changed).parent.mkdir(parents=True, exist_ok=True)
    (repo / changed).write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "fix: a later change")
    ran: list[list[str]] = []

    outcome = release_checks.run_checks(repo, lambda argv, _cwd: ran.append(list(argv)) or 0)

    assert outcome.passed
    assert outcome.lines[0].startswith("record check ledger-fsck: pass in ")
    assert len(outcome.lines) == 2 and outcome.lines[1].startswith(said)
    assert ran[0] == ["true"]
    live = [argv for argv in ran if "live_ledger" in argv]
    assert live == (
        [["uv", "run", "pytest", "-n", "4", "-m", "live_ledger"]] if "run" in said else []
    )
