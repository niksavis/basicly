from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".scripts" / "check_noqa_debt.py"
RATCHET = REPO_ROOT / ".scripts" / "ratchet.py"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(SCRIPT, "check_noqa_debt")


def _found(code: str, *, reason: str | None = "a reason", line: int = 1) -> object:
    return gate.Suppression(path="src/basicly/mod.py", line=line, code=code, reason=reason)


def _ratchet(frozen: dict[str, int] | None = None, unreasoned: int = 0) -> object:
    return gate.Ratchet(frozen=frozen or {}, count=unreasoned)


def _codes(source: str) -> list[str]:
    return [item.code for item in gate.suppressions("m.py", source)]


@pytest.mark.parametrize(
    ("source", "codes"),
    [
        ("x = 1  # noqa: F841\n", ["F841"]),
        ("x = 1  #noqa:F841\n", ["F841"]),
        ("x = 1  # NOQA: F841\n", ["F841"]),
        ("x = 1  # noqa : F841\n", ["F841"]),
        ("x = 1  # noqa: F841,F842\n", ["F841", "F842"]),
        ("x = 1  # noqa: S603 S607\n", ["S603", "S607"]),
        ("x = 1  # type: ignore  # noqa: F841\n", ["F841"]),
        ("x = 1  # nosec B603  # noqa: S603 - literal argv\n", ["S603"]),
        ("# noqa: F841\nx = 1\n", ["F841"]),
        ("x = 1  # noqa\n", [gate.BLANKET]),
        ("x = 1  # noqa/nosec pair: prose about both\n", []),
        ("x = 1  # noqadoc: not a directive\n", []),
        ("x = 1  # nothing to see here\n", []),
    ],
)
def test_a_directive_is_read_the_way_ruff_reads_it(source: str, codes: list[str]) -> None:
    assert _codes(source) == codes


def test_a_marker_inside_a_string_is_a_mention_not_a_suppression() -> None:
    assert _codes('MARKER = "# noqa: F841"\n') == []
    assert _codes('"""Docs naming # noqa: F841 in prose."""\n') == []


def test_a_directive_carries_the_line_it_sits_on() -> None:
    found = gate.suppressions("src/basicly/mod.py", "x = 1\ny = 2  # noqa: F841\n")

    assert [item.site for item in found] == ["src/basicly/mod.py:2"]


def test_a_module_that_does_not_tokenize_fails_rather_than_being_skipped() -> None:
    with pytest.raises(gate.RatchetError, match="could not tokenize"):
        gate.suppressions("broken.py", "def f(\n")


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        ("x = 1  # noqa: E731 - a sort key, not a function\n", "a sort key, not a function"),
        ("x = 1  # noqa: E402 — path set above\n", "path set above"),
        ("x = 1  # noqa: E402  (path set above)\n", "(path set above)"),
        ("x = 1  # noqa: PLR0913\n", None),
        ("x = 1  # noqa: PLR0913   \n", None),
        ("x = 1  # noqa: PLR0913 -\n", None),
    ],
)
def test_the_reason_is_whatever_follows_the_code(source: str, reason: str | None) -> None:
    assert gate.suppressions("m.py", source)[0].reason == reason


def test_one_reason_covers_every_code_in_its_directive() -> None:
    found = gate.suppressions("m.py", "x = 1  # noqa: S603,S607 - argv list, no shell\n")

    assert [item.reason for item in found] == ["argv list, no shell"] * 2


def test_a_tree_that_matches_its_recorded_counts_is_admitted() -> None:
    found = [_found("PLR0913"), _found("PLR0913"), _found("S603")]

    assert gate.collect(found, _ratchet({"PLR0913": 2, "S603": 1})) == []


def test_an_added_suppression_fails_naming_the_code_and_both_counts() -> None:
    found = [_found("PLR0913"), _found("PLR0913"), _found("PLR0913")]
    findings = gate.collect(found, _ratchet({"PLR0913": 2}))

    assert len(findings) == 1
    assert findings[0].subject == "PLR0913"
    assert "3 suppressions of PLR0913" in findings[0].detail
    assert "frozen 2" in findings[0].detail
    assert "`PLR0913 = +1`" in findings[0].remedy


def test_a_code_the_table_never_recorded_is_refused() -> None:
    findings = gate.collect([_found("N806")], _ratchet())

    assert [finding.subject for finding in findings] == ["N806"]
    assert "does not record" in findings[0].detail
    assert "`N806 = +1`" in findings[0].remedy


def test_a_blanket_suppression_is_refused_and_cannot_be_recorded_away() -> None:
    findings = gate.collect([_found(gate.BLANKET, reason=None)], _ratchet())

    assert [finding.subject for finding in findings] == [gate.BLANKET]
    assert "every rule on its line" in findings[0].detail
    assert gate.FROZEN_FRAGMENT not in findings[0].remedy


def test_a_count_that_fell_must_be_banked_in_the_same_diff() -> None:
    findings = gate.collect([_found("PLR0913")], _ratchet({"PLR0913": 4}))

    assert len(findings) == 1
    assert "down from the frozen 4" in findings[0].detail
    assert "`PLR0913 = -3`" in findings[0].remedy


def test_the_last_suppression_of_a_code_deletes_its_entry() -> None:

    findings = gate.collect([], _ratchet({"E731": 1}))

    assert len(findings) == 1
    assert "`E731 = -1`" in findings[0].remedy
    assert gate.FROZEN_FRAGMENT in findings[0].remedy


def test_a_blanket_suppression_is_not_counted_against_the_reason_ratchet() -> None:
    findings = gate.collect([_found(gate.BLANKET, reason=None)], _ratchet())

    assert [finding.subject for finding in findings] == [gate.BLANKET]


def test_a_reasonless_suppression_fails_naming_where_it_is() -> None:
    found = [_found("PLR0913", reason=None, line=42)]
    findings = gate.collect(found, _ratchet({"PLR0913": 1}))

    assert len(findings) == 1
    assert "src/basicly/mod.py:42" in findings[0].detail
    assert "`count_delta = +1`" in findings[0].remedy
    assert "reason" in findings[0].remedy


def test_the_reason_ratchet_fails_when_the_last_unargued_one_is_justified() -> None:
    findings = gate.collect([_found("E731")], _ratchet({"E731": 1}, unreasoned=1))

    assert len(findings) == 1
    assert "`count_delta = -1`" in findings[0].remedy


def test_an_unargued_suppression_cannot_be_swapped_for_another_one() -> None:
    found = [_found("E731", reason=None), _found("PLR0913", reason="mirrors the CLI surface")]

    assert gate.collect(found, _ratchet({"E731": 1, "PLR0913": 1}, unreasoned=1)) == []


def test_the_ratchet_cannot_be_read_as_empty(tmp_path: Path) -> None:

    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")

    with pytest.raises(gate.RatchetError, match=re.escape(f"no {gate.RATCHET_TABLE}")):
        gate.load_ratchet(tmp_path)


def test_no_frozen_entry_sits_at_zero() -> None:
    ratchet = gate.load_ratchet(REPO_ROOT)

    assert ratchet.frozen, "an empty table would refuse every code at once"
    for code, count in ratchet.frozen.items():
        assert count > 0, code


def test_the_gate_passes_on_this_repository() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )

    assert completed.returncode == 0, completed.stderr
    assert "each at its frozen count" in completed.stdout


def test_the_gate_fails_end_to_end_on_an_unannounced_suppression(tmp_path: Path) -> None:

    scripts = tmp_path / ".scripts"
    scripts.mkdir()
    copied = shutil.copy(SCRIPT, scripts / SCRIPT.name)
    shutil.copy(RATCHET, scripts / RATCHET.name)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.noqa_debt]\nunreasoned_count = 0\n\n[tool.noqa_debt.frozen]\nE731 = 1\nE402 = 1\n",
        encoding="utf-8",
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text(
        "f = lambda x: x  # noqa: E731 - a sort key\n"
        "g = lambda y: y  # noqa: E731 - one more than the record allows\n",
        encoding="utf-8",
    )
    package = src / "basicly"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy(REPO_ROOT / "src" / "basicly" / "dropin.py", package / "dropin.py")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)

    completed = subprocess.run(
        [sys.executable, copied], capture_output=True, text=True, check=False, cwd=tmp_path
    )

    assert completed.returncode == 1
    assert "2 suppressions of E731, up from the frozen 1" in completed.stderr


def test_only_the_suppression_the_gate_really_declares_is_counted() -> None:

    declared = gate.suppressions(SCRIPT.name, SCRIPT.read_text(encoding="utf-8"))

    assert [(item.code, item.reason) for item in declared] == [
        ("E402", "the path above comes first")
    ]
    assert gate.suppressions("test_check_noqa_debt.py", Path(__file__).read_text("utf-8")) == []


def test_the_gate_is_declared_as_a_verify_check() -> None:
    config = tomllib.loads((REPO_ROOT / "basicly.toml").read_text(encoding="utf-8"))
    checks = {check["name"]: check for check in config["verify"]["checks"]}

    assert "noqa-debt" in checks
    entry = checks["noqa-debt"]
    assert SCRIPT.relative_to(REPO_ROOT).as_posix() in entry["command"]
    assert entry["command"][:3] == ["uv", "run", "python"]
    assert set(entry["modes"]) == {"fast", "full"}
