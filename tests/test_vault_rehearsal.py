from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

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


def source_provenance(helper: Path) -> dict[str, str]:
    code = (
        "import json,runpy,sys; from pathlib import Path; "
        "sys.path.insert(0,str(Path(sys.argv[1]).parent)); "
        "print(json.dumps(runpy.run_path(sys.argv[1])['provenance']()))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(helper)], capture_output=True, text=True, check=True
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
    provenance = source_provenance(helper)
    (output / "part1.json").write_text(json.dumps({"result": "pass", "provenance": provenance}))
    (helper.parents[1] / ".basicly/core/kit/tracker/cli.py").write_text("changed = True\n")

    result = invoke(helper, output)

    assert result.returncode == 1
    assert "source changed" in result.stderr
    assert not (output / "lifecycle.json").exists()


def test_existing_consumer_report_cannot_supply_a_new_pass(tmp_path: Path) -> None:
    helper, output = fixture(tmp_path)
    provenance = source_provenance(helper)
    for name in ("part1", "control"):
        (output / f"{name}.json").write_text(
            json.dumps({"result": "pass", "provenance": provenance})
        )
    stale = json.dumps({"result": "pass"})
    (output / "lifecycle.json").write_text(stale)

    result = invoke(helper, output)

    assert result.returncode == 1
    assert "consumer reports already exist" in result.stderr
    assert (output / "lifecycle.json").read_text() == stale
