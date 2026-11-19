from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

SCRIPT = Path(__file__).resolve().parent.parent / ".scripts" / "shard_weights.py"

CASES = (
    ("tests.test_a", "test_slow", "tests/test_a.py", "12.34"),
    ("tests.test_a.TestGroup", "test_case[x-1]", "tests/test_a.py", "2.0"),
    ("tests.sub.test_b", "test_fast", "tests/sub/test_b.py", "0.01"),
)
REPORT = (
    '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">'
    + "".join(
        f'<testcase classname="{cls}" name="{name}" file="{path}" line="1" time="{time}"/>'
        for cls, name, path, time in CASES
    )
    + "</testsuite></testsuites>"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("shard_weights", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["shard_weights"] = module
    spec.loader.exec_module(module)
    return module


weights = _load()


def test_shard_reports_become_node_id_weights_and_keep_the_check_costs(tmp_path: Path) -> None:
    reports = tmp_path / "reports" / "shard-junit-1"
    reports.mkdir(parents=True)
    (reports / "1-of-12.xml").write_text(REPORT, encoding="utf-8")
    out = tmp_path / "shard_weights.json"
    out.write_text(json.dumps({"check:pyright-linux": 32.1, "tests/test_old.py::test_gone": 9.0}))

    assert weights.main([str(tmp_path / "reports"), "--out", str(out)]) == 0

    assert json.loads(out.read_text(encoding="utf-8")) == {
        "check:pyright-linux": 32.1,
        "tests/test_a.py::TestGroup::test_case[x-1]": 2.0,
        "tests/test_a.py::test_slow": 12.3,
    }


def test_an_empty_report_folder_is_refused_with_the_download_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "shard_weights.json"

    assert weights.main([str(tmp_path), "--out", str(out)]) == 2

    assert "gh run download" in capsys.readouterr().err
    assert not out.exists()
