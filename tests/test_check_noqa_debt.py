"""Tests for the `# noqa` suppression-debt ratchet (basicly-u2hl.12).

The gate's whole value is that it fails, so most of these assert a *failure* and name the
numbers it has to report. Three things can each make it fail open, and each is pinned here:

* **It counts what ruff obeys, not what grep finds.** The spellings in
  ``test_a_directive_is_read_the_way_ruff_reads_it`` were run through ruff 0.14 itself on
  2026-08-08; every case that says "suppressed" was observed to suppress and every case that
  says "not a directive" was observed to warn. A substring count would inflate the debt with
  comments that silence nothing, and `src/basicly/br.py:70` is one of those today.
* **A marker in a string is a mention.** Comments come from :mod:`tokenize`, which is what
  lets this file and the gate spell the marker throughout without counting themselves —
  ``test_neither_the_gate_nor_this_test_declares_a_suppression`` is what proves it.
* **The count falls as well as rises.** A debt that fell and was not banked licenses regrowth
  back to the old number for free, which is the shape `check_module_size.py` was built to
  refuse and the reason its waiver count is checked in both directions.

The logic tests drive :func:`collect` with synthetic suppressions rather than building trees.
Two end-to-end runs cover the acceptance criterion in both directions: the real repository
must exit zero, and a scratch repository carrying one unannounced suppression must not.
"""

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


def _load(path: Path, name: str) -> ModuleType:
    """Load a standalone script by path, the way `uv run python` does."""
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
    return gate.Ratchet(frozen=frozen or {}, unreasoned_count=unreasoned)


def _codes(source: str) -> list[str]:
    return [item.code for item in gate.suppressions("m.py", source)]


# --- reading a directive --------------------------------------------------------------


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
        # Observed 2026-08-08: ruff warns "Invalid `# noqa` directive" and suppresses
        # nothing. Counting it would charge the tree for a comment that does nothing.
        ("x = 1  # noqa/nosec pair: prose about both\n", []),
        ("x = 1  # noqadoc: not a directive\n", []),
        ("x = 1  # nothing to see here\n", []),
    ],
)
def test_a_directive_is_read_the_way_ruff_reads_it(source: str, codes: list[str]) -> None:
    """Each spelling was run through ruff itself before being written down here."""
    assert _codes(source) == codes


def test_a_marker_inside_a_string_is_a_mention_not_a_suppression() -> None:
    """The discriminator a regex over raw text cannot draw, and this file depends on it."""
    assert _codes('MARKER = "# noqa: F841"\n') == []
    assert _codes('"""Docs naming # noqa: F841 in prose."""\n') == []


def test_a_directive_carries_the_line_it_sits_on() -> None:
    """A count nobody can act on is a count nobody acts on."""
    found = gate.suppressions("src/basicly/mod.py", "x = 1\ny = 2  # noqa: F841\n")

    assert [item.site for item in found] == ["src/basicly/mod.py:2"]


def test_a_module_that_does_not_tokenize_fails_rather_than_being_skipped() -> None:
    """Skipping it would exempt the file, which is the fail-open shape this gate refuses."""
    with pytest.raises(gate.RatchetError, match="could not tokenize"):
        gate.suppressions("broken.py", "def f(\n")


# --- the reason -----------------------------------------------------------------------


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
    """A dash on its own is not an argument; the house form is `# noqa: CODE - reason`."""
    assert gate.suppressions("m.py", source)[0].reason == reason


def test_one_reason_covers_every_code_in_its_directive() -> None:
    """`# noqa: S603, S607 - argv list, no shell` argues for both, as this tree writes it."""
    found = gate.suppressions("m.py", "x = 1  # noqa: S603,S607 - argv list, no shell\n")

    assert [item.reason for item in found] == ["argv list, no shell"] * 2


# --- the ratchet ----------------------------------------------------------------------


def test_a_tree_that_matches_its_recorded_counts_is_admitted() -> None:
    """The baseline is the whole debt, so agreeing with it exactly is the passing state."""
    found = [_found("PLR0913"), _found("PLR0913"), _found("S603")]

    assert gate.collect(found, _ratchet({"PLR0913": 2, "S603": 1})) == []


def test_an_added_suppression_fails_naming_the_code_and_both_counts() -> None:
    """The acceptance criterion: a count that rose has to be legible without a diff."""
    found = [_found("PLR0913"), _found("PLR0913"), _found("PLR0913")]
    findings = gate.collect(found, _ratchet({"PLR0913": 2}))

    assert len(findings) == 1
    assert findings[0].subject == "PLR0913"
    assert "3 suppressions of PLR0913" in findings[0].detail
    assert "frozen 2" in findings[0].detail
    assert "PLR0913 to 3" in findings[0].remedy


def test_a_code_the_table_never_recorded_is_refused() -> None:
    """Refused by default: "we already suppress that one" is what has to be written down."""
    findings = gate.collect([_found("N806")], _ratchet())

    assert [finding.subject for finding in findings] == ["N806"]
    assert "does not record" in findings[0].detail
    assert "`N806 = 1`" in findings[0].remedy


def test_a_blanket_suppression_is_refused_and_cannot_be_recorded_away() -> None:
    """Ruff passes a used blanket directive; RUF100 only catches one silencing nothing."""
    findings = gate.collect([_found(gate.BLANKET, reason=None)], _ratchet())

    assert [finding.subject for finding in findings] == [gate.BLANKET]
    assert "every rule on its line" in findings[0].detail
    assert gate.FROZEN_TABLE not in findings[0].remedy


def test_a_count_that_fell_must_be_banked_in_the_same_diff() -> None:
    """A record left at the old number licenses regrowth back to it for free."""
    findings = gate.collect([_found("PLR0913")], _ratchet({"PLR0913": 4}))

    assert len(findings) == 1
    assert "down from the frozen 4" in findings[0].detail
    assert "`PLR0913 = 1`" in findings[0].remedy


def test_the_last_suppression_of_a_code_deletes_its_entry() -> None:
    """Zeroing it would keep a licence for a rule this tree no longer suppresses at all."""
    findings = gate.collect([], _ratchet({"E731": 1}))

    assert len(findings) == 1
    assert "delete" in findings[0].remedy
    assert gate.FROZEN_TABLE in findings[0].remedy


def test_a_blanket_suppression_is_not_counted_against_the_reason_ratchet() -> None:
    """It is already refused outright; charging it twice would misname the repair."""
    findings = gate.collect([_found(gate.BLANKET, reason=None)], _ratchet())

    assert [finding.subject for finding in findings] == [gate.BLANKET]


# --- the reason ratchet ---------------------------------------------------------------


def test_a_reasonless_suppression_fails_naming_where_it_is() -> None:
    """A bare marker is a suppression with no argument; the remedy names writing one."""
    found = [_found("PLR0913", reason=None, line=42)]
    findings = gate.collect(found, _ratchet({"PLR0913": 1}))

    assert len(findings) == 1
    assert "src/basicly/mod.py:42" in findings[0].detail
    assert "unreasoned_count = 1" in findings[0].remedy
    assert "reason" in findings[0].remedy


def test_the_reason_ratchet_fails_when_the_last_unargued_one_is_justified() -> None:
    """It fails in both directions, or the count decays into a blanket exemption."""
    findings = gate.collect([_found("E731")], _ratchet({"E731": 1}, unreasoned=1))

    assert len(findings) == 1
    assert "unreasoned_count = 0" in findings[0].remedy


def test_an_unargued_suppression_cannot_be_swapped_for_another_one() -> None:
    """The count is what stops a justified one being spent on a new bare marker."""
    found = [_found("E731", reason=None), _found("PLR0913", reason="mirrors the CLI surface")]

    assert gate.collect(found, _ratchet({"E731": 1, "PLR0913": 1}, unreasoned=1)) == []


# --- the recorded state, and the wiring -----------------------------------------------


def test_the_ratchet_cannot_be_read_as_empty(tmp_path: Path) -> None:
    """An absent table has to fail, not default to a baseline of nothing.

    Defaulting would report the whole existing debt as new, which is loud — but a table that
    parsed to `{}` for any other reason would pass every code silently.
    """
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")

    with pytest.raises(gate.RatchetError, match=re.escape(f"no {gate.RATCHET_TABLE}")):
        gate.load_ratchet(tmp_path)


def test_no_frozen_entry_sits_at_zero() -> None:
    """The table is a measurement, not an allowance: an emptied code leaves the list."""
    ratchet = gate.load_ratchet(REPO_ROOT)

    assert ratchet.frozen, "an empty table would refuse every code at once"
    for code, count in ratchet.frozen.items():
        assert count > 0, code


def test_the_gate_passes_on_this_repository() -> None:
    """The recorded ratchet describes this tree — run as a consumer runs it."""
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
    """The other half of the acceptance criterion, run the way a commit would run it.

    A scratch repository rather than a mutation of this one: the gate resolves its root from
    its own location, so copying it into a tmp tree exercises `git ls-files`, the tokenizer
    and the TOML read together without putting a deliberate defect in the working tree.
    """
    scripts = tmp_path / ".scripts"
    scripts.mkdir()
    copied = shutil.copy(SCRIPT, scripts / SCRIPT.name)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.noqa_debt]\nunreasoned_count = 0\n\n[tool.noqa_debt.frozen]\nE731 = 1\n",
        encoding="utf-8",
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text(
        "f = lambda x: x  # noqa: E731 - a sort key\n"
        "g = lambda y: y  # noqa: E731 - one more than the record allows\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)

    completed = subprocess.run(
        [sys.executable, copied], capture_output=True, text=True, check=False, cwd=tmp_path
    )

    assert completed.returncode == 1
    assert "2 suppressions of E731, up from the frozen 1" in completed.stderr


def test_neither_the_gate_nor_this_test_declares_a_suppression() -> None:
    """Both spell the marker throughout; neither may thereby inflate the debt it measures."""
    for path in (SCRIPT, Path(__file__)):
        assert gate.suppressions(path.name, path.read_text(encoding="utf-8")) == [], path


def test_the_gate_is_declared_as_a_verify_check() -> None:
    """Wired to the fast set, so it runs at commit time and not only on request."""
    config = tomllib.loads((REPO_ROOT / "basicly.toml").read_text(encoding="utf-8"))
    checks = {check["name"]: check for check in config["verify"]["checks"]}

    assert "noqa-debt" in checks
    entry = checks["noqa-debt"]
    assert SCRIPT.relative_to(REPO_ROOT).as_posix() in entry["command"]
    # A bare `python` on windows-latest is a system interpreter, not the project's.
    assert entry["command"][:3] == ["uv", "run", "python"]
    assert set(entry["modes"]) == {"fast", "full"}
