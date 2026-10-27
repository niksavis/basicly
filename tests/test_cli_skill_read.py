from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import cli, skill_reference


def test_skill_read_delivers_exact_procedure_and_refuses_stale_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / ".agents/skills/example/SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("# Required procedure\nPreserve [brackets].\n", encoding="utf-8")
    manifest, _ = skill_reference.skill_brief(tmp_path, ["example"], family="codex")
    argv = json.loads(manifest)["skills"][0]["retrieve"][1:]
    monkeypatch.chdir(tmp_path)
    assert cli.main(argv) == 0
    assert capsys.readouterr().out == path.read_text(encoding="utf-8")
    path.write_text("changed requirement", encoding="utf-8")
    assert cli.main(argv) == 1
    result = capsys.readouterr()
    assert result.out == ""
    assert "changed" in result.err


def test_skill_read_requires_a_digest_and_refuses_unrelated_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "private.txt").write_text("do not disclose", encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        cli.main(["skill-read", "private.txt"])
    assert error.value.code == 2
    capsys.readouterr()
    assert cli.main(["skill-read", "private.txt", "--sha256", "0" * 64]) == 1
    output = capsys.readouterr()
    assert not output.out
    assert "projected skill path" in output.err
    assert "do not disclose" not in output.err
