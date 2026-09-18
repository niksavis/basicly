from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import cli, state

if TYPE_CHECKING:
    import pytest


def _catalog(root: Path) -> Path:
    src = root / "bundled"
    (src / "fragments").mkdir(parents=True)
    (src / "fragments" / "a.fragment.yaml").write_text("id: a\n", encoding="utf-8")
    (src / "fragments" / "b.fragment.yaml").write_text("id: b\n", encoding="utf-8")
    return src


def _paths(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


def test_a_dry_sync_reports_what_it_would_copy_and_copies_nothing(tmp_path: Path) -> None:
    src = _catalog(tmp_path)
    dst = tmp_path / "core"
    before = _paths(tmp_path)

    report = cli._sync_catalog(src, dst, None, force=False, dry_run=True)

    assert sorted(report.new) == ["fragments/a.fragment.yaml", "fragments/b.fragment.yaml"]
    assert _paths(tmp_path) == before


def test_a_dry_sync_names_the_same_files_the_real_one_writes(tmp_path: Path) -> None:
    src = _catalog(tmp_path)
    dst = tmp_path / "core"

    dry = cli._sync_catalog(src, dst, None, force=False, dry_run=True)
    real = cli._sync_catalog(src, dst, None, force=False)

    assert sorted(dry.new) == sorted(real.new)
    assert (dst / "fragments" / "a.fragment.yaml").is_file()


def test_a_dry_sync_keeps_a_file_it_would_delete(tmp_path: Path) -> None:
    src = _catalog(tmp_path)
    dst = tmp_path / "core"
    cli._sync_catalog(src, dst, None, force=False)
    stale = dst / "fragments" / "gone.fragment.yaml"
    stale.write_text("id: gone\n", encoding="utf-8")
    previous = state.InstallState(
        basicly_version="0",
        installed_at="",
        core_hashes={"fragments/gone.fragment.yaml": state.sha256_of_file(stale)},
    )

    report = cli._sync_catalog(src, dst, previous, force=False, dry_run=True)

    assert report.deleted == ["fragments/gone.fragment.yaml"]
    assert stale.is_file()


def test_a_dry_scaffold_writes_nothing_and_says_it_would(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "nested" / "tasks.json"

    cli._write_scaffold(target, "{}\n", "tasks.json", force=False, dry_run=True)

    assert not target.exists()
    assert "Would write tasks.json" in capsys.readouterr().out


def test_a_dry_ignore_scaffold_leaves_the_file_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ignore = tmp_path / ".gitignore"
    ignore.write_text("node_modules/\n", encoding="utf-8")

    cli._scaffold_generated_ignores(tmp_path, dry_run=True)

    assert ignore.read_text(encoding="utf-8") == "node_modules/\n"
    assert "Would add" in capsys.readouterr().out


def _steps(names: list[str]) -> list[tuple[str, object, argparse.Namespace]]:
    return [(name, None, argparse.Namespace()) for name in names]


def test_a_fresh_repo_names_every_step_instead_of_checking_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli._report_install_steps(_steps(["build", "skills-build"]), upgrade=False) == 0

    out = capsys.readouterr().out
    assert "no core catalog yet" in out
    assert "build, skills-build" in out


def test_an_upgrade_names_only_the_steps_whose_check_failed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli,
        "_dry_run_counterparts",
        lambda: {
            "build": ("check", lambda _n: 1, argparse.Namespace()),
            "skills-build": ("skills-check", lambda _n: 0, argparse.Namespace()),
        },
    )

    assert cli._report_install_steps(_steps(["build", "skills-build"]), upgrade=True) == 0

    out = capsys.readouterr().out
    assert "would rewrite a projected file: build." in out
    assert "skills-build" not in out.rsplit("complete:", 1)[1]
