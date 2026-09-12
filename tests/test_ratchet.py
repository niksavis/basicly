from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS = REPO_ROOT / ".scripts"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ratchet = _load(SCRIPTS / "ratchet.py", "ratchet")
GATE_FILES = ("check_module_size.py", "check_noqa_debt.py")
GATES = tuple(_load(SCRIPTS / name, name.removesuffix(".py")) for name in GATE_FILES)

_OUTSIDE = (
    '"""A module in a root no gate scopes."""\n\nX = [\n'
    + "".join(f'    "value {index}",\n' for index in range(120))
    + "]\nY = X  # noqa: E402 - a suppression the walk has to reach\n"
)


def _scratch_repo(tmp_path: Path) -> Path:
    (tmp_path / "extra").mkdir()
    (tmp_path / "extra" / "mod.py").write_text(_OUTSIDE, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    return tmp_path


def _walked(repo: Path) -> tuple[list[str], list[str]]:
    module_size, noqa_debt = GATES
    return (
        [module.path for module in module_size.tracked_modules(repo)],
        [item.path for item in noqa_debt.tracked_suppressions(repo)],
    )


@pytest.mark.parametrize(
    "definition",
    ["class Ratchet[", "class Finding", "class RatchetError", "SCOPE_ROOTS = "],
)
def test_the_framework_is_defined_once_for_the_ratchet_gates(definition: str) -> None:

    pattern = re.compile(rf"^{re.escape(definition)}", re.MULTILINE)
    holders = [
        name
        for name in ("ratchet.py", *GATE_FILES)
        if pattern.search((SCRIPTS / name).read_text(encoding="utf-8"))
    ]

    assert holders == ["ratchet.py"]


def test_a_scope_root_added_once_is_seen_by_every_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _scratch_repo(tmp_path)

    assert _walked(repo) == ([], [])

    monkeypatch.setattr(ratchet, "SCOPE_ROOTS", (*ratchet.SCOPE_ROOTS, "extra"))

    assert _walked(repo) == (["extra/mod.py"], ["extra/mod.py"])


def test_a_tree_git_will_not_list_is_refused_rather_than_measured_as_empty(
    tmp_path: Path,
) -> None:
    with pytest.raises(ratchet.RatchetError, match="could not list tracked files"):
        list(ratchet.tracked_sources(tmp_path))


def _pyproject(repo: Path, body: str) -> Path:
    (repo / "pyproject.toml").write_text(body, encoding="utf-8")
    (repo / "basicly.d").mkdir(exist_ok=True)
    return repo


def _fragment(repo: Path, body: str) -> None:
    (repo / "basicly.d" / "basicly-one.toml").write_text(body, encoding="utf-8")


def _load_shares(repo: Path) -> Any:
    return ratchet.compose_ratchet(repo, "gate", count_key="waiver_count", entry_type=float)


def test_a_composed_share_is_rounded_to_the_grid_it_is_measured_on(tmp_path: Path) -> None:
    repo = _pyproject(
        tmp_path,
        '[tool.gate]\nwaiver_count = 0\n\n[tool.gate.frozen]\n"src/fsck.py" = 51.3\n',
    )
    _fragment(repo, '[ratchet.gate.frozen]\n"src/fsck.py" = -0.1\n')

    assert _load_shares(repo).frozen == {"src/fsck.py": 51.2}


def test_a_waiver_count_stays_whole_when_the_entries_are_fractional(tmp_path: Path) -> None:
    repo = _pyproject(tmp_path, "[tool.gate]\nwaiver_count = 1\n")
    _fragment(repo, "[ratchet.gate]\ncount_delta = 1.5\n")

    with pytest.raises(ratchet.RatchetError, match="count_delta must be an integer delta"):
        _load_shares(repo)


def test_a_whole_delta_still_moves_a_fractional_gate_s_waiver_count(tmp_path: Path) -> None:
    repo = _pyproject(tmp_path, "[tool.gate]\nwaiver_count = 1\n")
    _fragment(repo, "[ratchet.gate]\ncount_delta = 1\n")

    assert _load_shares(repo).count == 2


def test_a_missing_table_is_refused_rather_than_defaulted_to_empty(tmp_path: Path) -> None:
    repo = _pyproject(tmp_path, "[tool.other]\n")

    with pytest.raises(ratchet.RatchetError, match=r"no \[tool\.gate\]"):
        _load_shares(repo)


def test_a_count_recorded_under_another_key_is_refused(tmp_path: Path) -> None:
    repo = _pyproject(tmp_path, "[tool.gate]\nunreasoned_count = 1\n")

    with pytest.raises(ratchet.RatchetError, match="must declare waiver_count"):
        _load_shares(repo)


def test_a_counting_gate_refuses_a_fractional_entry(tmp_path: Path) -> None:
    repo = _pyproject(
        tmp_path, '[tool.gate]\nwaiver_count = 0\n\n[tool.gate.frozen]\n"a.py" = 4.5\n'
    )

    with pytest.raises(ratchet.RatchetError, match="go-live number"):
        ratchet.compose_ratchet(repo, "gate", count_key="waiver_count", entry_type=int)


def test_a_finding_prints_its_subject_then_its_remedy(capsys: pytest.CaptureFixture) -> None:
    ratchet.report("noqa-debt", [ratchet.Finding("E731", "2 up from 1", "record it")])

    assert capsys.readouterr().err == "noqa-debt: E731: 2 up from 1\nnoqa-debt:   record it\n"
