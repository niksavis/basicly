from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from basicly import cli, retention

BASELINE = """\
## Core Rules

- Minimal diffs; an unrelated refactor hides which change failed.
- Deterministic tests; a bug fix ships a regression test.
- Parameterize shell commands and queries; concatenation reads input as syntax.
- Keep defaults portable; a committed hostname breaks the next clone.
"""


def _repo(tmp_path: Path) -> Path:
    target = tmp_path / ".claude/CLAUDE.md"
    target.parent.mkdir(parents=True)
    target.write_text(BASELINE, encoding="utf-8")
    return tmp_path


def _run(repo: Path, monkeypatch: pytest.MonkeyPatch, response: str, **flags: object) -> int:
    monkeypatch.setattr(cli, "_repo_root", lambda: repo)
    path = repo / "response.txt"
    path.write_text(response, encoding="utf-8")
    args = argparse.Namespace(response=str(path), baseline=None, verbose=False, strict=False)
    for key, value in flags.items():
        setattr(args, key, value)
    return cli.cmd_retention(args)


def _full(text: str) -> str:
    return text + "\n- filler line one\n- filler line two\n- filler line three\n"


def test_a_full_recall_reports_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    rules = retention.derive_rules(BASELINE)

    _run(repo, monkeypatch, _full("\n".join(f"- {r.text}" for r in rules)))

    assert "LOADED" in capsys.readouterr().out


def test_an_unrelated_answer_reports_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)

    _run(repo, monkeypatch, _full("\n".join("- The quick brown fox jumps." for _ in range(4))))

    assert "ABSENT" in capsys.readouterr().out


def test_a_short_answer_reports_undersampled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)

    _run(repo, monkeypatch, "- Minimal diffs.\n")

    assert "UNDERSAMPLED" in capsys.readouterr().out


def test_the_scope_warning_is_always_printed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    rules = retention.derive_rules(BASELINE)

    _run(repo, monkeypatch, _full("\n".join(f"- {r.text}" for r in rules)))

    out = capsys.readouterr().out
    assert "does not measure adherence" in out
    assert "cannot measure gradual forgetting" in out


def test_strict_exits_non_zero_only_on_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    rules = retention.derive_rules(BASELINE)
    absent = _full("\n".join("- The quick brown fox jumps." for _ in range(4)))
    loaded = _full("\n".join(f"- {r.text}" for r in rules))

    assert _run(repo, monkeypatch, absent, strict=True) == 1
    assert _run(repo, monkeypatch, loaded, strict=True) == 0
    assert _run(repo, monkeypatch, absent, strict=False) == 0


def test_verbose_names_the_rules_that_did_not_come_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)

    _run(repo, monkeypatch, _full("\n".join("- unrelated" for _ in range(4))), verbose=True)

    assert "core-rules.1" in capsys.readouterr().out


def test_every_run_appends_one_trend_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    rules = retention.derive_rules(BASELINE)
    response = _full("\n".join(f"- {r.text}" for r in rules))

    _run(repo, monkeypatch, response)
    _run(repo, monkeypatch, response)
    capsys.readouterr()

    lines = (repo / cli.RETENTION_TREND).read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    entry = json.loads(lines[0])
    assert entry["verdict"] == retention.LOADED
    assert entry["baseline"] == ".claude/CLAUDE.md"
    assert 0.0 <= entry["rate"] <= 1.0


def test_a_repo_with_no_instruction_file_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)

    with pytest.raises(SystemExit, match="no always-on instruction file"):
        cli._retention_baseline(tmp_path, None)


def test_an_instruction_file_with_no_rules_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".claude/CLAUDE.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Title\n\nprose only, no bullets\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    (tmp_path / "r.txt").write_text("anything\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="declares no rules"):
        cli.cmd_retention(
            argparse.Namespace(
                response=str(tmp_path / "r.txt"), baseline=None, verbose=False, strict=False
            )
        )
