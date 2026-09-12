from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".scripts" / "check_except_form.py"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(SCRIPT, "check_except_form")


TEMPLATE = "try:\n    pass\n{}\n    pass\n"


def _offenders(clause: str) -> list[Any]:

    source = f"try:\n    pass\n{clause}\n    pass\n"
    findings, _ = gate.collect([("probe.py", source)])
    return findings


PAREN_TUPLE = "except (ValueError, OSError):"
HOUSE_TUPLE = "except ValueError, OSError:"
BINDING = "except (ValueError, OSError) as err:"
SINGLE = "except ValueError:"
REDUNDANT_PARENS = "except (ValueError):"


def test_a_paren_wrapped_tuple_binding_nothing_is_reported() -> None:
    (finding,) = _offenders(PAREN_TUPLE)
    assert finding.subject == "probe.py:3"
    assert PAREN_TUPLE in finding.detail


def test_the_paren_free_house_form_is_not_reported() -> None:
    assert _offenders(HOUSE_TUPLE) == []


def test_parentheses_are_allowed_where_the_clause_binds() -> None:
    assert _offenders(BINDING) == []


def test_a_single_exception_type_is_not_reported() -> None:
    assert _offenders(SINGLE) == []


def test_redundant_parentheses_around_one_name_are_not_reported() -> None:

    assert _offenders(REDUNDANT_PARENS) == []


def test_ast_cannot_separate_the_two_forms() -> None:
    dumps = {
        ast.dump(
            cast(
                "ast.expr",
                cast("ast.Try", ast.parse(TEMPLATE.format(clause)).body[0]).handlers[0].type,
            )
        )
        for clause in (PAREN_TUPLE, HOUSE_TUPLE)
    }
    assert len(dumps) == 1


def test_the_remedy_names_the_rewrite_rather_than_the_rule() -> None:
    (finding,) = _offenders(PAREN_TUPLE)
    assert f"write `{HOUSE_TUPLE}`" in finding.remedy


def test_the_remedy_keeps_the_indentation_of_a_nested_clause() -> None:
    source = "def f():\n    try:\n        pass\n    except (ValueError, OSError):\n        pass\n"
    findings, _ = gate.collect([("probe.py", source)])
    assert f"write `{HOUSE_TUPLE}`" in findings[0].remedy


def test_a_multi_line_clause_is_exempt() -> None:
    source = "try:\n    pass\nexcept (\n    ValueError,\n    OSError,\n):\n    pass\n"
    findings, _ = gate.collect([("probe.py", source)])
    assert findings == []


def test_an_except_star_group_is_read() -> None:
    assert len(_offenders("except* (ValueError, OSError):")) == 1


def test_a_handler_after_a_binding_one_is_still_read() -> None:
    source = (
        "try:\n    pass\n"
        "except (ValueError, OSError) as err:\n    pass\n"
        "except (KeyError, TypeError):\n    pass\n"
    )
    findings, _ = gate.collect([("probe.py", source)])
    assert [finding.subject for finding in findings] == ["probe.py:5"]


def test_parens_around_a_subexpression_do_not_wrap_the_clause() -> None:
    (clause,) = gate.clauses("try:\n    pass\nexcept (ValueError, OSError)[0]:\n    pass\n")
    assert clause.open_col == -1
    assert not clause.offends


def test_a_source_that_cannot_be_tokenized_is_a_finding_not_a_skip() -> None:
    findings, seen = gate.collect([("broken.py", "def f(:\n")])
    assert seen == 0
    assert findings[0].subject == "broken.py"
    assert "cannot tokenize" in findings[0].detail


def test_clauses_refuses_source_it_cannot_tokenize() -> None:
    with pytest.raises(gate.RatchetError):
        gate.clauses("def f(:\n")


def _repo_with(tmp_path: Path, source: str) -> Path:
    module = tmp_path / "src" / "probe.py"
    module.parent.mkdir(parents=True)
    module.write_text(source, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    return tmp_path


def test_main_reports_a_tracked_offender_and_exits_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        gate, "REPO_ROOT", _repo_with(tmp_path, f"try:\n    pass\n{PAREN_TUPLE}\n    pass\n")
    )

    assert gate.main() == 1
    err = capsys.readouterr().err
    assert "src/probe.py:3" in err
    assert f"write `{HOUSE_TUPLE}`" in err


def test_an_empty_scope_is_an_error_rather_than_a_clean_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)

    assert gate.main() == 1
    assert "no tracked Python modules found" in capsys.readouterr().err


def test_the_tracked_tree_uses_the_house_form() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "use the house form" in completed.stdout


def test_the_gate_is_wired_to_something_that_runs_it() -> None:
    config = tomllib.loads((REPO_ROOT / "basicly.toml").read_text(encoding="utf-8"))
    checks = config.get("verify", {}).get("checks", [])
    (entry,) = [check for check in checks if check["name"] == "except-form"]
    assert entry["command"][-1].endswith("check_except_form.py")
