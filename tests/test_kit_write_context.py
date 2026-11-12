from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.test_kit_vault_write import (
    KIT,
    _environment,
    _ledger_bytes,
    _queued,
    _review,
    _seeded,
    bare,
    commands,
    vault_write,
)

__all__ = ["_environment", "bare"]


def test_a_vault_write_without_the_write_context_is_refused_before_anything_is_written(
    tmp_path: Path, bare: Path
) -> None:
    repo = _seeded(tmp_path, bare)
    before = _ledger_bytes(repo.ledger)

    with pytest.raises(vault_write.NoClockError, match="names a vault"):
        commands.update(repo.ledger, repo.record, fields={"priority": 4})
    with pytest.raises(vault_write.NoClockError, match=r"run the write through cli\.py"):
        commands.claim(repo.ledger, repo.record, "someone")

    assert _ledger_bytes(repo.ledger) == before
    assert _queued(repo) == []


def test_the_write_context_and_a_bare_redactor_both_give_their_parts() -> None:
    def hide(text: str) -> str:
        return text.replace("x", "-")

    def clock() -> float:
        return 1.0

    assert vault_write.parts(vault_write.WriteContext(hide, clock)) == (hide, clock)
    assert vault_write.parts(hide) == (hide, None)
    assert vault_write.parts(None) == (None, None)


def test_a_bare_redactor_given_to_record_process_still_redacts_the_ledger_write(
    tmp_path: Path, bare: Path
) -> None:
    repo = _seeded(tmp_path, bare, configured=False)
    payload = _review(repo)

    commands.record_process(
        repo.ledger, repo.record, payload, redact=lambda text: text.replace("fixture", "<hidden>")
    )

    written = "".join(path.read_text(encoding="utf-8") for path in repo.ledger.glob("*.jsonl"))
    assert "<hidden>-check" in written


def _constructors(folder: Path) -> list[str]:
    found = []
    for path in sorted(folder.glob("*.py")):
        if path.name == "cli.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            called = node.func
            name = called.attr if isinstance(called, ast.Attribute) else getattr(called, "id", "")
            if name == "WriteContext":
                found.append(f"{path.name}:{node.lineno}")
    return found


def test_only_the_cli_adapter_builds_the_write_context(tmp_path: Path) -> None:
    planted = tmp_path / "planted"
    planted.mkdir()
    (planted / "cli.py").write_text("vault_write.WriteContext(None, clock)\n", encoding="utf-8")
    (planted / "rogue.py").write_text("context = WriteContext(None, clock)\n", encoding="utf-8")

    assert _constructors(planted) == ["rogue.py:1"]
    assert _constructors(KIT) == []
