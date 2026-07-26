"""Tests for the single br adapter seam (src/basicly/br.py)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from basicly import br


def test_run_br_raises_when_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The hard entry point raises with one canonical absence message."""
    monkeypatch.setattr(br, "which", lambda: None)
    with pytest.raises(RuntimeError, match="br is not on PATH"):
        br.run_br(tmp_path, ["ready"])


def test_try_run_br_returns_none_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The soft entry point degrades to None for optional tracker features."""
    monkeypatch.setattr(br, "which", lambda: None)
    assert br.try_run_br(tmp_path, ["sync", "--merge"]) is None


def test_version_probe_warns_below_the_floor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An outdated br gets one warning per process, never a failure."""
    monkeypatch.setattr(br, "which", lambda: "/usr/bin/br")
    monkeypatch.setattr(br, "_probed_paths", set())

    def fake_run(cmd, **_kw):
        out = "br 0.0.1" if "--version" in cmd else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(br.subprocess, "run", fake_run)
    br.run_br(tmp_path, ["ready"])
    br.run_br(tmp_path, ["ready"])
    err = capsys.readouterr().err
    assert err.count("older than the harness floor") == 1


# --- Reading the committed export (basicly-kjc5.50) --------------------------


def _write_export(beads: Path, *lines: str) -> None:
    beads.mkdir(parents=True, exist_ok=True)
    (beads / "issues.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_export_records_skips_junk_and_keeps_file_order(tmp_path: Path) -> None:
    """An unparsable or id-less line is skipped, never fatal: every consumer is evidence."""
    _write_export(
        tmp_path / ".beads",
        json.dumps({"id": "b-1"}),
        "{not json",
        json.dumps({"title": "no id"}),
        "",
        json.dumps({"id": "b-2"}),
    )
    assert [record["id"] for record in br.export_records(tmp_path)] == ["b-1", "b-2"]


def test_export_records_is_empty_without_an_export(tmp_path: Path) -> None:
    """No workspace, no records — the callers all degrade rather than fail."""
    assert br.export_records(tmp_path) == []


def test_export_records_follows_the_beads_redirect(tmp_path: Path) -> None:
    """A harness worktree shares the base tracker, so the redirect target is authoritative."""
    base = tmp_path / "base" / ".beads"
    _write_export(base, json.dumps({"id": "base-1"}))
    worktree = tmp_path / "wt"
    _write_export(worktree / ".beads", json.dumps({"id": "stale-1"}))
    (worktree / ".beads" / "redirect").write_text(str(base), encoding="utf-8")

    assert br.beads_dir(worktree) == base
    assert [record["id"] for record in br.export_records(worktree)] == ["base-1"]


def test_export_comment_texts_reads_only_well_formed_comments() -> None:
    """Comments are the shared ledger's carrier; a malformed row is ignored."""
    record = {
        "id": "b-1",
        "comments": [{"text": "first"}, {"author": "niksa"}, "not a row", {"text": 7}],
    }
    assert br.export_comment_texts(record) == ["first"]
    assert br.export_comment_texts({"id": "b-2"}) == []
