from __future__ import annotations

from pathlib import Path

from tests.test_kit_tracker_claims import SHAPED, _git, _use_gate, repo
from tests.tracker_claims_fixture import _kit

__all__ = ["repo"]


def _first_record(repo: Path) -> str:
    _use_gate(repo, "engine")
    return _kit(repo, "create", ".basicly/ledger", "--prefix", "acme", "--title", "x", *SHAPED)[
        "record"
    ]


def test_the_commit_that_installs_the_tracker_needs_no_claim(repo: Path) -> None:
    record = _first_record(repo)
    (repo / "README.md").write_text("# Scratch repo\n", encoding="utf-8")
    _git(repo, "add", "-A")

    installed = _git(repo, "commit", "-q", "-m", f"chore: install basicly ({record})")

    assert installed.returncode == 0, installed.stderr


def test_after_the_install_commit_code_still_needs_a_claim(repo: Path) -> None:
    record = _first_record(repo)
    _git(repo, "add", "-A")
    assert _git(repo, "commit", "-q", "-m", f"chore: install basicly ({record})").returncode == 0
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "app.py")

    refused = _git(repo, "commit", "-q", "-m", f"feat: value ({record})")

    assert refused.returncode != 0
    assert "Claim the record first" in refused.stderr
