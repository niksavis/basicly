"""Tests for the prose-density ratchet's ``basicly.d`` fragment delta route (basicly-e7rtjn).

The boundary is the fragment route against the measurement and ratchet decisions its
sibling :mod:`test_check_comment_density` holds. A fragment only means anything once
`git ls-files`, the TOML read and the composer run together, so every case here builds a
scratch repository and runs the gate as a subprocess — which is also why they are the
expensive half, and why the split that put them here relieved a module that had reached
the size cap.

The loader below is spelled out rather than imported from the sibling: 28 test modules in
this tree each carry their own copy, because a test module importing another depends on a
`sys.path` pytest is free to change.

Every waiver marker below is indented inside a string literal, so this file does not waive
itself — ``test_this_half_carries_no_waiver_either`` proves it.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from basicly import dropin

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".scripts" / "check_comment_density.py"
RATCHET = REPO_ROOT / ".scripts" / "ratchet.py"
WAIVERS = REPO_ROOT / ".scripts" / "waivers.py"


def _load(path: Path, name: str) -> ModuleType:
    """Load a standalone script by path, the way `uv run python` does."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(SCRIPT, "check_comment_density")

# The marker the gate reads, without its colon; `waivers.read_waiver` takes it as data.
WAIVER_MARKER = "comment-density-waiver"


def _prose_module(marker: str = "") -> str:
    """A module over the cap and over the token floor, whose payload is narration.

    Written rather than borrowed from the tree so the share it measures is a property of
    this test, not of whatever the module it borrowed from looks like this week.
    """
    functions = "\n\n".join(
        f'def f{index}() -> int:\n    """Say nothing the signature does not, at some'
        f' length, for the {word} time."""\n    # And restate the return below.\n'
        f"    return {index}\n"
        for index, word in enumerate(("first", "second", "third", "fourth", "fifth"))
    )
    return f'"""A module whose whole payload is narration."""\n{marker}\n\n{functions}'


def _scratch_repo(tmp_path: Path, module: str, *, frozen: str = "", waiver_count: int = 0) -> Path:
    """A tiny git repo holding the gate, the modules it reads through, and *module*.

    The gate resolves its root from its own location, so a copy of it in a tmp tree
    exercises `git ls-files`, the tokenizer, the TOML read and the fragments together
    without putting a deliberate defect in this working tree.

    Only the measured modules are added to the index. `src` and `.scripts` are both in the
    gate's scope roots, so tracking the modules the copy has to import would measure them
    too — `read_cost.py` is 82% prose against a record that freezes nothing, and every case
    here would report it. Importing does not need git; measuring does.
    """
    scripts = tmp_path / ".scripts"
    scripts.mkdir()
    for script in (SCRIPT, RATCHET, WAIVERS):
        shutil.copy(script, scripts / script.name)
    package = tmp_path / "src" / "basicly"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    for name in ("dropin.py", "read_cost.py"):
        shutil.copy(REPO_ROOT / "src" / "basicly" / name, package / name)
    (tmp_path / "pyproject.toml").write_text(
        f"[tool.comment_density]\nwaiver_count = {waiver_count}\n\n"
        f"[tool.comment_density.frozen]\n{frozen}",
        encoding="utf-8",
    )
    (tmp_path / "src" / "mod.py").write_text(module, encoding="utf-8")
    (tmp_path / dropin.FRAGMENT_DIR).mkdir()
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-f", "src/mod.py"], check=True)
    return tmp_path


def _fragment(repo: Path, name: str, body: str) -> None:
    """Drop one lane's fragment into the scratch repo's ``basicly.d``."""
    (repo / dropin.FRAGMENT_DIR / f"{name}.toml").write_text(body, encoding="utf-8")


def _second_waived_module(repo: Path) -> None:
    """A second module carrying a waiver, tracked, so two waivers are outstanding."""
    (repo / "src" / "other.py").write_text(
        _prose_module("# comment-density-waiver: cohesion: also provenance"), encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(repo), "add", "-f", "src/other.py"], check=True)


def _run_gate(repo: Path) -> subprocess.CompletedProcess[str]:
    """Run the copied gate the way a commit runs it."""
    return subprocess.run(  # nosec B603
        [sys.executable, str(repo / ".scripts" / SCRIPT.name)],
        capture_output=True,
        text=True,
        check=False,
        cwd=repo,
    )


def test_a_fragment_delta_reaches_the_gate_end_to_end(tmp_path: Path) -> None:
    """The delta route the other two ratchets already have (basicly-05g0).

    The module is recorded at exactly the share it measures, so it passes; the fragment
    then lowers that record by 1.4 and the same unchanged module is over its baseline. A
    gate reading `pyproject.toml` raw reports nothing at all here.
    """
    module = _prose_module()
    share, _ = gate.measure(module)
    repo = _scratch_repo(tmp_path, module, frozen=f'"src/mod.py" = {share}\n')
    assert _run_gate(repo).returncode == 0, "the recorded share must pass before the fragment"

    _fragment(repo, "basicly-one", '[ratchet.comment_density.frozen]\n"src/mod.py" = -1.4\n')
    completed = _run_gate(repo)

    assert completed.returncode == 1
    assert f"{share}% prose, up from the frozen {round(share - 1.4, 1)}%" in completed.stderr


def test_two_fragments_each_carrying_a_waiver_compose_to_two_new_waivers(tmp_path: Path) -> None:
    """The shape `basicly-kr7t` could not record: a waiver taken without editing the anchor."""
    repo = _scratch_repo(tmp_path, _prose_module("# comment-density-waiver: cohesion: provenance"))
    _second_waived_module(repo)
    for name in ("basicly-one", "basicly-two"):
        _fragment(repo, name, "[ratchet.comment_density]\ncount_delta = 1\n")

    completed = _run_gate(repo)

    assert completed.returncode == 0, completed.stderr
    assert "2 waived" in completed.stdout


def test_the_waiver_ratchet_still_binds_when_a_fragment_is_missing(tmp_path: Path) -> None:
    """The mutation: one fragment for two waivers is still a waiver nothing declared."""
    repo = _scratch_repo(tmp_path, _prose_module("# comment-density-waiver: cohesion: provenance"))
    _second_waived_module(repo)
    _fragment(repo, "basicly-one", "[ratchet.comment_density]\ncount_delta = 1\n")

    completed = _run_gate(repo)

    assert completed.returncode == 1
    assert "waiver was added without saying so" in completed.stderr
    assert "under [ratchet.comment_density] in basicly.d/<bead-id>.toml" in completed.stderr


def test_a_fragment_that_is_not_a_delta_stops_the_gate(tmp_path: Path) -> None:
    """Fails closed: a fragment it cannot read must not compose to the recorded baseline."""
    repo = _scratch_repo(tmp_path, _prose_module())
    _fragment(repo, "basicly-one", '[ratchet.comment_density.frozen]\n"src/mod.py" = "58.6"\n')

    completed = _run_gate(repo)

    assert completed.returncode == 1
    assert "must be a numeric delta" in completed.stderr


def test_the_gate_composes_the_fragments_the_other_ratchets_do(tmp_path: Path) -> None:
    """One composer for three gates, so a fragment means the same thing at each of them."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.comment_density]\nwaiver_count = 1\n\n"
        '[tool.comment_density.frozen]\n"a.py" = 60.0\n',
        encoding="utf-8",
    )
    (tmp_path / dropin.FRAGMENT_DIR).mkdir()
    (tmp_path / dropin.FRAGMENT_DIR / "basicly-one.toml").write_text(
        '[ratchet.comment_density]\ncount_delta = 1\nfrozen = {"a.py" = -1.4}\n', encoding="utf-8"
    )

    ratchet = gate.load_ratchet(tmp_path)

    assert ratchet.frozen == {"a.py": 58.6}
    assert ratchet.count == 2


def test_this_half_carries_no_waiver_either() -> None:
    """The sibling asserts it for itself and the gate; a split must not drop the claim."""
    body = Path(__file__).read_text(encoding="utf-8")

    assert gate.read_waiver(__file__, body, WAIVER_MARKER) is None
