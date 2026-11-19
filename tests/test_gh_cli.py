from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from basicly import gh_cli


def test_a_missing_gh_answers_none_without_running_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gh_cli.shutil, "which", lambda _name: None)
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: pytest.fail("ran a process"))
    assert gh_cli.run(["run", "list"]) is None


@pytest.mark.parametrize(("code", "expected"), [(0, "listed\n"), (1, None)])
def test_gh_answers_its_output_only_when_it_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int, expected: str | None
) -> None:
    fake = tmp_path / "gh"
    fake.write_text(f"#!/bin/sh\necho listed\nexit {code}\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(gh_cli.shutil, "which", lambda _name: str(fake))
    assert gh_cli.run(["run", "list"]) == expected
