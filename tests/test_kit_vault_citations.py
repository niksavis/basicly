from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_kit_vault_canonical import RECORD, events
from tests.test_kit_vault_migrate import vault_migrate
from tests.test_kit_vault_shapes import Vault

citations = vault_migrate.vault_citations


def test_citation_counts_find_known_matches_without_printing_old_ids(tmp_path: Path) -> None:
    repository = Vault(tmp_path / "code")
    old = events.event_id_for(RECORD, "note", {"text": "A synthetic private value."})
    other = events.event_id_for(RECORD, "note", {"text": "A different value."})
    repository.write("README.md", f"Read {old} and {old}.\n")
    repository.write("changelog.md", f"See {old}.\n")
    repository.write(".basicly/ledger/events-0001.jsonl", old)
    repository.commit(f"a historical reference {old}")
    before = repository.git("rev-parse", "HEAD")
    report = citations.census(repository.root, {old})
    assert report["available"] is True
    assert (report["files_examined"], report["files_with_citations"], report["file_citations"]) == (
        2,
        2,
        3,
    )
    assert report["commit_citations"] == 1
    assert old not in json.dumps(report)
    control = citations.census(repository.root, {other})
    assert control["files_examined"] == report["files_examined"]
    assert (control["file_citations"], control["commit_citations"]) == (0, 0)
    assert repository.git("rev-parse", "HEAD") == before
    assert repository.git("status", "--porcelain") == ""


def test_a_nonrepository_reports_unavailable_instead_of_zero(tmp_path: Path) -> None:
    assert citations.census(tmp_path, {"unavailable"})["available"] is False


def test_citation_scan_refuses_to_read_dotenv_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Vault(tmp_path / "code")
    repository.write("README.md", "A known tracked control.\n")
    repository.commit("tracked control")
    run = citations.git_runner.run

    def listing(args: list[str], directory: Path, **options: object) -> str:
        value = run(args, directory, **options)
        return value + ".env.private\0" if args[0] == "ls-files" else value

    read_bytes = Path.read_bytes

    def read(path: Path) -> bytes:
        if path.name.startswith(".env"):
            pytest.fail("citation scan read a dotenv path")
        return read_bytes(path)

    monkeypatch.setattr(citations.git_runner, "run", listing)
    monkeypatch.setattr(Path, "read_bytes", read)
    report = citations.census(repository.root, set())
    assert (report["files_examined"], report["files_unreadable"]) == (1, 0)
