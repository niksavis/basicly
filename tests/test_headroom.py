"""Tests for the two-ratchet headroom read (basicly-co64).

The read exists because measuring one ratchet feels like having measured, so the tests that
matter are the ones where the two answers disagree: a module with 3,700 tokens of room and
none on prose has to be named, or this is the same one-sided measurement in one command.

Logic tests drive :func:`is_tight` and :func:`render` with synthetic rooms, as the sibling
gate tests drive ``collect``. The composition of the two gates is what cannot be faked, so
that is exercised against a real git tree — and the same tree is the control: a module with
room on both bounds must *not* be named, or "nothing is close" is unfalsifiable.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".scripts" / "headroom.py"


def _load(path: Path, name: str) -> ModuleType:
    """Load a standalone script by path, the way `uv run python` does."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


read = _load(SCRIPT, "headroom")

# Both caps, taken from the gates that own them rather than respelled here.
CAP = read.size.SCOPE_FILE_READ_CAP
SHARE_CAP = read.prose.CAP


def _room(
    tokens: int = 100,
    token_limit: int | None = CAP,
    share: float | None = 10.0,
    share_limit: float | None = SHARE_CAP,
) -> object:
    return read.Headroom(
        path="src/basicly/thing.py",
        tokens=tokens,
        token_limit=token_limit,
        share=share,
        share_limit=share_limit,
    )


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A git tree holding one module of each shape the two bounds can disagree about."""
    repo = tmp_path / "tree"
    (repo / "src").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        "[tool.module_size]\nwaiver_count = 0\n[tool.module_size.frozen]\n"
        '"src/frozen.py" = 9000\n\n'
        "[tool.comment_density]\nwaiver_count = 0\n[tool.comment_density.frozen]\n",
        encoding="utf-8",
    )
    code = "\n".join(f"VALUE_{i} = {i} + {i} * 3 - 1" for i in range(60))
    (repo / "src" / "roomy.py").write_text(f'"""One line."""\n\n{code}\n', encoding="utf-8")
    (repo / "src" / "wordy.py").write_text(
        f'"""{"Prose that says nothing the code does not. " * 45}"""\n\n{code}\n',
        encoding="utf-8",
    )
    (repo / "src" / "frozen.py").write_text(f'"""One line."""\n\n{code}\n', encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)  # nosec B603 B607
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)  # nosec B603 B607
    return repo


# --- one command, both bounds -------------------------------------------------------


def test_one_line_carries_both_bounds_and_what_is_left_under_each() -> None:
    """The whole requirement: one answer, so measuring one and not the other is not a step."""
    line = read.render(_room(tokens=3400, share=45.0))

    assert "3400/4000 tokens (600 left)" in line
    assert "45.0/50.0% prose (5.0 left)" in line


def test_a_frozen_module_is_measured_against_its_baseline_not_the_cap(tree: Path) -> None:
    """A module over the cap is bounded by its recorded go-live number, which is its room."""
    rooms = {room.path: room for room in read.measure(tree)}

    assert rooms["src/frozen.py"].token_limit == 9000
    assert rooms["src/roomy.py"].token_limit == CAP


def test_a_module_under_the_prose_floor_reports_no_share_rather_than_zero(tree: Path) -> None:
    """`comment-density` does not measure a stub, so neither does this — 0.0% would be a lie."""
    (tree / "src" / "stub.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tree), "add", "-A"], check=True)  # nosec B603 B607

    rooms = {room.path: room for room in read.measure(tree)}

    assert rooms["src/stub.py"].share is None
    assert "prose floor" in read.render(rooms["src/stub.py"])


def test_a_waived_module_reports_no_bound_on_the_gate_that_waived_it() -> None:
    """A waiver removes the bound; printing one would invent a constraint the gate has not."""
    line = read.render(_room(token_limit=None, share=59.1, share_limit=None))

    assert "(waived)" in line
    assert "59.1% prose (waived)" in line


# --- the two bounds disagreeing, which is the reason this exists ---------------------


def test_a_module_with_token_room_and_no_prose_room_is_named() -> None:
    """The trap, as a test: three lanes measured tokens, passed, and paid on prose."""
    assert read.is_tight(_room(tokens=100, share=49.5))


def test_a_module_with_prose_room_and_no_token_room_is_named() -> None:
    """The mirror, so the predicate cannot be one-sided in the other direction."""
    assert read.is_tight(_room(tokens=CAP - 1, share=10.0))


def test_a_module_with_room_on_both_bounds_is_not_named() -> None:
    """The control: without it, "within N of a bound" could be satisfied by naming everything."""
    assert not read.is_tight(_room(tokens=100, share=10.0))


def test_the_control_tree_names_the_wordy_module_and_not_the_roomy_one(tree: Path) -> None:
    """End to end over a real tree: both modules hold the same code, one holds prose too."""
    rooms = {room.path: room for room in read.measure(tree)}

    # The fixture's premise, asserted rather than assumed: `wordy.py` is the disagreement
    # case — thousands of tokens of room, and none on prose. Measured 54.7% at 45 repeats.
    assert rooms["src/wordy.py"].token_limit == CAP
    assert rooms["src/wordy.py"].tokens < CAP - read.TIGHT_TOKENS
    assert rooms["src/wordy.py"].share is not None
    assert rooms["src/wordy.py"].share > SHARE_CAP - read.TIGHT_POINTS
    assert [path for path, room in rooms.items() if read.is_tight(room)] == ["src/wordy.py"]


# --- a read, not a gate -------------------------------------------------------------


def test_a_path_naming_no_tracked_module_fails_instead_of_reporting_a_full_cap(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A typo's fail-open answer is "plenty of room", which is the answer the caller wants."""
    assert read.main(["--repo", str(tree), "src/typo.py"]) == 1
    assert "no tracked module in scope" in capsys.readouterr().err


def test_the_read_exits_zero_on_this_repository_and_counts_what_it_measured() -> None:
    """Run as a consumer runs it: it reports, it never refuses."""
    completed = subprocess.run(  # nosec B603
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )

    assert completed.returncode == 0, completed.stderr
    assert "tracked modules are within" in completed.stdout


def test_the_read_is_not_wired_as_a_verify_check() -> None:
    """Not a new gate: both ratchets already bind, and a second refusal would be noise."""
    config = tomllib.loads((REPO_ROOT / "basicly.toml").read_text(encoding="utf-8"))
    commands = [" ".join(check["command"]) for check in config["verify"]["checks"]]

    assert not [command for command in commands if "headroom.py" in command]
