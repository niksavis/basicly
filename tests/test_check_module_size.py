from __future__ import annotations

import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

from basicly.read_cost import SCOPE_FILE_READ_CAP

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".scripts" / "check_module_size.py"
CAP = SCOPE_FILE_READ_CAP

WAIVER_MARKER = "module-size-waiver"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(SCRIPT, "check_module_size")


def _waiver(subject: str, reason: str) -> object:
    return gate.Waiver(subject=subject, kind=gate.COHESION, retires=None, reason=reason)


def _module(path: str, tokens: int, waiver: str | None = None) -> object:
    return gate.Module(
        path=path, tokens=tokens, waiver=None if waiver is None else _waiver(path, waiver)
    )


def _ratchet(frozen: dict[str, int] | None = None, waivers: int = 0) -> object:
    return gate.Ratchet(frozen=frozen or {}, count=waivers)


def test_a_module_over_the_cap_fails_naming_the_file_its_tokens_and_the_cap() -> None:
    findings = gate.collect([_module("src/basicly/new.py", CAP + 1)], _ratchet())

    assert len(findings) == 1
    assert findings[0].subject == "src/basicly/new.py"
    assert str(CAP + 1) in findings[0].detail
    assert str(CAP) in findings[0].detail


def test_a_module_at_the_cap_is_admitted() -> None:
    assert gate.collect([_module("src/basicly/new.py", CAP)], _ratchet()) == []


def test_the_cap_is_never_respelled_in_the_gate() -> None:
    assert str(CAP) not in SCRIPT.read_text(encoding="utf-8")


def test_a_frozen_module_that_grew_fails_naming_both_counts() -> None:
    frozen = {"src/basicly/cli.py": 53_095}
    findings = gate.collect([_module("src/basicly/cli.py", 53_096)], _ratchet(frozen))

    assert len(findings) == 1
    assert "53096" in findings[0].detail
    assert "53095" in findings[0].detail


def test_an_added_import_line_does_not_count_against_a_frozen_module() -> None:

    before = "import json\n\n\nVALUE = 1\n"
    after = "import json\nfrom pathlib import Path\n\n\nVALUE = 1\n"

    assert gate.module_tokens(after) == gate.module_tokens(before)


def test_a_parenthesised_import_is_excluded_across_its_continuation_lines() -> None:
    one_line = "from . import a\n\n\nVALUE = 1\n"
    wrapped = "from . import (\n    a,\n    b,\n    c,\n)\n\n\nVALUE = 1\n"

    assert gate.module_tokens(wrapped) == gate.module_tokens(one_line)


def test_a_function_level_import_is_still_counted() -> None:
    without = "def f():\n    return 1\n"
    deferred = "def f():\n    from . import supervise\n\n    return 1\n"

    assert gate.module_tokens(deferred) > gate.module_tokens(without)


def test_a_module_that_grew_by_code_still_fails_after_the_import_exclusion() -> None:

    lean = "import json\n\n\nVALUE = 1\n"
    grown = "import json\nimport sys\n\n\nVALUE = 1\nOTHER = 2\n"

    assert gate.module_tokens(grown) > gate.module_tokens(lean)


def test_a_module_under_twice_the_cap_is_told_to_reach_the_cap() -> None:
    frozen = {"src/basicly/verify.py": 5_436}
    findings = gate.collect([_module("src/basicly/verify.py", 5_556)], _ratchet(frozen))

    assert "under 4000 tokens" in findings[0].remedy
    assert "one extraction" in findings[0].remedy


def test_a_module_far_over_the_cap_is_only_told_not_to_grow() -> None:

    frozen = {"src/basicly/cli.py": 54_336}
    findings = gate.collect([_module("src/basicly/cli.py", 54_362)], _ratchet(frozen))

    assert "back under 54336" in findings[0].remedy
    assert "decomposition track of its own" in findings[0].remedy
    assert "one extraction" not in findings[0].remedy


def test_a_frozen_module_that_shrank_is_admitted_without_editing_the_record() -> None:
    frozen = {"src/basicly/cli.py": 53_095}

    assert gate.collect([_module("src/basicly/cli.py", 40_000)], _ratchet(frozen)) == []


def test_a_frozen_module_that_reached_the_cap_must_leave_the_frozen_list() -> None:
    frozen = {"src/basicly/cli.py": 53_095}
    findings = gate.collect([_module("src/basicly/cli.py", CAP)], _ratchet(frozen))

    assert len(findings) == 1
    assert "delete" in findings[0].remedy
    assert gate.FROZEN_TABLE in findings[0].remedy


def test_a_frozen_entry_naming_no_module_fails() -> None:
    findings = gate.collect([], _ratchet({"src/basicly/gone.py": 9_000}))

    assert [f.subject for f in findings] == ["src/basicly/gone.py"]
    assert "no readable tracked module" in findings[0].detail


def test_a_waived_module_may_not_also_stay_frozen() -> None:
    module = _module("src/basicly/cli.py", 53_095, waiver="one cohesive dispatch table")
    findings = gate.collect([module], _ratchet({"src/basicly/cli.py": 53_095}, waivers=1))

    assert len(findings) == 1
    assert "waiver" in findings[0].detail
    assert gate.FROZEN_TABLE in findings[0].remedy


def test_a_waiver_admits_a_module_over_the_cap() -> None:
    module = _module("src/basicly/big.py", CAP * 3, waiver="one generated table")

    assert gate.collect([module], _ratchet(waivers=1)) == []


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("# module-size-waiver: one cohesive state machine", "one cohesive state machine"),
        ("#module-size-waiver: no space needed", "no space needed"),
        ("# module-size-waiver:", None),
        ("# module-size-waiver:    ", None),
        ("    # module-size-waiver: indented, so not a module-level statement", None),
        ('MARKER = "# module-size-waiver: named, not claimed"', None),
    ],
)
def test_a_waiver_is_a_column_zero_comment_carrying_a_reason(line: str, reason: str | None) -> None:

    waiver = gate.read_waiver("m.py", f"x = 1\n{line}\ny = 2\n", WAIVER_MARKER)

    assert (waiver.reason if waiver is not None else None) == reason


def test_the_waiver_count_ratchet_fails_when_a_waiver_appears_unannounced() -> None:
    module = _module("src/basicly/big.py", CAP * 3, waiver="one generated table")
    findings = gate.collect([module], _ratchet(waivers=0))

    assert len(findings) == 1
    assert "src/basicly/big.py" in findings[0].detail
    assert "`count_delta = +1`" in findings[0].remedy


def test_the_waiver_count_ratchet_fails_when_the_last_waiver_disappears() -> None:
    findings = gate.collect([_module("src/basicly/small.py", 10)], _ratchet(waivers=1))

    assert len(findings) == 1
    assert "`count_delta = -1`" in findings[0].remedy


def test_neither_the_gate_nor_this_test_carries_a_waiver() -> None:
    for path in (SCRIPT, Path(__file__)):
        body = path.read_text(encoding="utf-8")
        assert gate.read_waiver(str(path), body, WAIVER_MARKER) is None, path


def test_the_gate_passes_on_this_repository() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )

    assert completed.returncode == 0, completed.stderr
    assert "frozen" in completed.stdout


def test_every_frozen_entry_names_a_tracked_module_that_is_over_the_cap() -> None:
    ratchet = gate.load_ratchet(REPO_ROOT)
    sizes = {module.path: module.tokens for module in gate.tracked_modules(REPO_ROOT)}

    assert ratchet.frozen, "an empty frozen list would pass everything"
    for path, baseline in ratchet.frozen.items():
        assert baseline > CAP, path
        assert sizes.get(path, 0) <= baseline, path


def test_the_gate_is_declared_as_a_verify_check() -> None:
    config = tomllib.loads((REPO_ROOT / "basicly.toml").read_text(encoding="utf-8"))
    checks = {check["name"]: check for check in config["verify"]["checks"]}

    assert "module-size" in checks
    entry = checks["module-size"]
    assert SCRIPT.relative_to(REPO_ROOT).as_posix() in entry["command"]
    assert entry["command"][:3] == ["uv", "run", "python"]
    assert set(entry["modes"]) == {"fast", "full"}


def test_ruff_gates_cyclomatic_complexity_at_fifteen() -> None:

    config = tomllib.loads((REPO_ROOT / ".ruff.toml").read_text(encoding="utf-8"))

    assert "C90" in config["lint"]["select"]
    assert config["lint"]["mccabe"]["max-complexity"] == 15
