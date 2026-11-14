from __future__ import annotations

import json
import runpy
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HELPER = Path(__file__).resolve().parents[1] / ".scripts/rehearse_vault_migration.py"


def fixture(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    helper = source / ".scripts/rehearse_vault_migration.py"
    helper.parent.mkdir(parents=True)
    shutil.copyfile(HELPER, helper)
    shutil.copyfile(
        HELPER.with_name("vault_rehearsal_evidence.py"),
        helper.with_name("vault_rehearsal_evidence.py"),
    )
    cli = source / ".basicly/core/kit/tracker/cli.py"
    cli.parent.mkdir(parents=True)
    cli.write_text("", encoding="utf-8")
    output = tmp_path / "reports"
    output.mkdir()
    snapshot = output / "workspace-source"
    snapshot.mkdir()
    (snapshot / "events-0001.jsonl").write_text("control\n", encoding="utf-8")
    return helper, output


def invoke(helper: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(helper),
            "--source-repo",
            str(helper.parents[1]),
            "--out",
            str(output),
            "--part",
            "complete",
            "--workspace-record",
            "proof-ab12",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def retained_pass(helper: Path, output: Path) -> dict[str, object]:
    code = (
        "import json,runpy,sys; from pathlib import Path; "
        "sys.path.insert(0,str(Path(sys.argv[1]).parent)); "
        "module=runpy.run_path(sys.argv[1]); snapshot=Path(sys.argv[2])/'workspace-source'; "
        "print(json.dumps({'result':'pass','provenance':module['provenance'](),"
        "'source_sha256':module['evidence'].files_digest(snapshot,snapshot.rglob('*'))}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(helper), str(output)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_old_passing_reports_cannot_complete_a_rehearsal(tmp_path: Path) -> None:
    helper, output = fixture(tmp_path)
    for name in ("part1", "control", "lifecycle", "ledger-compat"):
        (output / f"{name}.json").write_text(json.dumps({"result": "pass"}))

    result = invoke(helper, output)

    assert result.returncode == 1
    assert "source changed" in result.stderr
    assert '"result": "pass"' not in result.stdout


def test_changed_kit_refuses_before_consumer_checks(tmp_path: Path) -> None:
    helper, output = fixture(tmp_path)
    report = retained_pass(helper, output)
    (output / "part1.json").write_text(json.dumps(report))
    (helper.parents[1] / ".basicly/core/kit/tracker/cli.py").write_text("changed = True\n")

    result = invoke(helper, output)

    assert result.returncode == 1
    assert "source changed" in result.stderr
    assert not (output / "lifecycle.json").exists()


def test_existing_consumer_report_cannot_supply_a_new_pass(tmp_path: Path) -> None:
    helper, output = fixture(tmp_path)
    report = retained_pass(helper, output)
    for name in ("part1", "control"):
        (output / f"{name}.json").write_text(json.dumps(report))
    stale = json.dumps({"result": "pass"})
    (output / "lifecycle.json").write_text(stale)

    result = invoke(helper, output)

    assert result.returncode == 1
    assert "consumer reports already exist" in result.stderr
    assert (output / "lifecycle.json").read_text() == stale


def test_changed_copied_ledger_refuses_before_consumer_checks(tmp_path: Path) -> None:
    helper, output = fixture(tmp_path)
    (output / "part1.json").write_text(json.dumps(retained_pass(helper, output)))
    (output / "workspace-source/events-0001.jsonl").write_text("changed\n", encoding="utf-8")

    result = invoke(helper, output)

    assert result.returncode == 1
    assert "copied ledger changed" in result.stderr
    assert not (output / "lifecycle.json").exists()


def test_source_write_during_copy_refuses_the_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(HELPER.parent))
    module = runpy.run_path(str(HELPER))
    source = tmp_path / "source"
    source.mkdir()
    original = source / "events-0001.jsonl"
    original.write_text("original\n", encoding="utf-8")
    copyfile = shutil.copyfile

    def concurrent_copy(src: Path, dst: Path) -> Path:
        copied = copyfile(src, dst)
        original.write_text("original\nconcurrent\n", encoding="utf-8")
        return copied

    monkeypatch.setattr(shutil, "copyfile", concurrent_copy)
    tracker = SimpleNamespace(
        events=SimpleNamespace(ledger_paths=lambda path: list(path.glob("events-*.jsonl")))
    )

    with pytest.raises(ValueError, match="source ledger changed during copying"):
        module["copy_ledger"](tracker, source, tmp_path / "snapshot")
