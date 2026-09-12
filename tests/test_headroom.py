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
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


read = _load(SCRIPT, "headroom")

CAP = read.size.SCOPE_FILE_READ_CAP


def _room(tokens: int = 100, token_limit: int | None = CAP) -> object:
    return read.Headroom(path="src/basicly/thing.py", tokens=tokens, token_limit=token_limit)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    repo = tmp_path / "tree"
    (repo / "src").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        '[tool.module_size]\nwaiver_count = 0\n[tool.module_size.frozen]\n"src/frozen.py" = 9000\n',
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


def test_one_line_carries_the_bound_and_what_is_left_under_it() -> None:
    line = read.render(_room(tokens=3400))

    assert "3400/4000 tokens (600 left)" in line


def test_a_frozen_module_is_measured_against_its_baseline_not_the_cap(tree: Path) -> None:
    rooms = {room.path: room for room in read.measure(tree)}

    assert rooms["src/frozen.py"].token_limit == 9000
    assert rooms["src/roomy.py"].token_limit == CAP


def test_a_waived_module_reports_no_bound() -> None:
    line = read.render(_room(token_limit=None))

    assert "(waived)" in line


def test_a_module_close_to_its_token_bound_is_named() -> None:
    assert read.is_tight(_room(tokens=CAP - 1))


def test_a_module_with_room_is_not_named() -> None:
    assert not read.is_tight(_room(tokens=100))


def test_a_path_naming_no_tracked_module_fails_instead_of_reporting_a_full_cap(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert read.main(["--repo", str(tree), "src/typo.py"]) == 1
    assert "no tracked module in scope" in capsys.readouterr().err


def test_the_read_exits_zero_on_this_repository_and_counts_what_it_measured() -> None:
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
    config = tomllib.loads((REPO_ROOT / "basicly.toml").read_text(encoding="utf-8"))
    commands = [" ".join(check["command"]) for check in config["verify"]["checks"]]

    assert not [command for command in commands if "headroom.py" in command]
