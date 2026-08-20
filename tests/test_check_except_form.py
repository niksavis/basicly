"""Tests for the `except`-clause form gate (PEP 758 house form).

Every case here is a *source string*, never a real `except` clause in this file's own code:
`ruff format` rewrites a paren-wrapped tuple in real source, so an inline fixture would be
silently converted into the form it is meant to fail on. Inside a string literal the
formatter leaves it alone.

The first five tests are the two-directional positive control. `test_ast_cannot_separate_the
_two_forms` is why the instrument is `tokenize`: an AST probe reported 54 offenders on this
tree and every one was conforming, so a control that only proved the reporting direction
would have passed for that probe too.
"""

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
    """Load a standalone script by path, the way `uv run python` does."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(SCRIPT, "check_except_form")


TEMPLATE = "try:\n    pass\n{}\n    pass\n"


def _offenders(clause: str) -> list[Any]:
    """The findings for a module whose only handler is *clause*.

    Typed `Any` rather than `object` because the gate is loaded by path, so its `Finding` is
    not a name a checker can resolve — the same reason `test_check_comment_density` does it.
    """
    source = f"try:\n    pass\n{clause}\n    pass\n"
    findings, _ = gate.collect([("probe.py", source)])
    return findings


PAREN_TUPLE = "except (ValueError, OSError):"
HOUSE_TUPLE = "except ValueError, OSError:"
BINDING = "except (ValueError, OSError) as err:"
SINGLE = "except ValueError:"
REDUNDANT_PARENS = "except (ValueError):"


def test_a_paren_wrapped_tuple_binding_nothing_is_reported() -> None:
    """The one direction that compiles, and the shape that reached `0a0d669e`."""
    (finding,) = _offenders(PAREN_TUPLE)
    assert finding.subject == "probe.py:3"
    assert PAREN_TUPLE in finding.detail


def test_the_paren_free_house_form_is_not_reported() -> None:
    """PEP 758's form is the house form; reporting it would invert the rule."""
    assert _offenders(HOUSE_TUPLE) == []


def test_parentheses_are_allowed_where_the_clause_binds() -> None:
    """`as` makes them mandatory, so the same parens are conforming here."""
    assert _offenders(BINDING) == []


def test_a_single_exception_type_is_not_reported() -> None:
    """No tuple, no choice of form."""
    assert _offenders(SINGLE) == []


def test_redundant_parentheses_around_one_name_are_not_reported() -> None:
    """Stated decision: no comma, so not a tuple, so outside the rule.

    `ruff format` strips them anyway — measured 2026-08-20, it rewrites this to
    `except ValueError:` — so nothing is left uncovered by declining it here.
    """
    assert _offenders(REDUNDANT_PARENS) == []


def test_ast_cannot_separate_the_two_forms() -> None:
    """Why the instrument is `tokenize`, pinned so a future rewrite cannot regress to `ast`."""
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
    """A remedy naming only what is wrong leaves the reader to invent the fix."""
    (finding,) = _offenders(PAREN_TUPLE)
    assert f"write `{HOUSE_TUPLE}`" in finding.remedy


def test_the_remedy_keeps_the_indentation_of_a_nested_clause() -> None:
    """The rewrite is offered as a clause, so a nested handler's leading run is dropped."""
    source = "def f():\n    try:\n        pass\n    except (ValueError, OSError):\n        pass\n"
    findings, _ = gate.collect([("probe.py", source)])
    assert f"write `{HOUSE_TUPLE}`" in findings[0].remedy


def test_a_multi_line_clause_is_exempt() -> None:
    """Parentheses are the only continuation `ruff format` accepts (see the gate docstring)."""
    source = "try:\n    pass\nexcept (\n    ValueError,\n    OSError,\n):\n    pass\n"
    findings, _ = gate.collect([("probe.py", source)])
    assert findings == []


def test_an_except_star_group_is_read() -> None:
    """`except*` takes the same rule, and the `*` sits between `except` and the clause."""
    assert len(_offenders("except* (ValueError, OSError):")) == 1


def test_a_handler_after_a_binding_one_is_still_read() -> None:
    """The scan must not stop at the `as` that ends an earlier clause."""
    source = (
        "try:\n    pass\n"
        "except (ValueError, OSError) as err:\n    pass\n"
        "except (KeyError, TypeError):\n    pass\n"
    )
    findings, _ = gate.collect([("probe.py", source)])
    assert [finding.subject for finding in findings] == ["probe.py:5"]


def test_parens_around_a_subexpression_do_not_wrap_the_clause() -> None:
    """`open_col` means the whole clause, so a wrapped operand is not a wrapped clause."""
    (clause,) = gate.clauses("try:\n    pass\nexcept (ValueError, OSError)[0]:\n    pass\n")
    assert clause.open_col == -1
    assert not clause.offends


def test_a_source_that_cannot_be_tokenized_is_a_finding_not_a_skip() -> None:
    """A file the gate could not read reports no clauses, which reads as conforming."""
    findings, seen = gate.collect([("broken.py", "def f(:\n")])
    assert seen == 0
    assert findings[0].subject == "broken.py"
    assert "cannot tokenize" in findings[0].detail


def test_clauses_refuses_source_it_cannot_tokenize() -> None:
    """The refusal is the module's contract, not only `collect`'s handling of it."""
    with pytest.raises(gate.RatchetError):
        gate.clauses("def f(:\n")


# --- through `main`, against a real git repo -------------------------------------


def _repo_with(tmp_path: Path, source: str) -> Path:
    """A git repo whose only tracked module is *source*, staged so `git ls-files` sees it."""
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
    """End to end: scope, finding, remedy and exit code, which `collect` alone cannot show."""
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
    """A gate whose scope silently emptied is indistinguishable from a conforming tree."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)

    assert gate.main() == 1
    assert "no tracked Python modules found" in capsys.readouterr().err


# --- the real tree, and the wiring ----------------------------------------------


def test_the_tracked_tree_uses_the_house_form() -> None:
    """The measured baseline: zero offenders, and a summary that names what was read."""
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
    """An instrument built and never connected is this repo's named defect class."""
    config = tomllib.loads((REPO_ROOT / "basicly.toml").read_text(encoding="utf-8"))
    checks = config.get("verify", {}).get("checks", [])
    (entry,) = [check for check in checks if check["name"] == "except-form"]
    assert entry["command"][-1].endswith("check_except_form.py")
