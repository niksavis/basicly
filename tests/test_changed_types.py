from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parent.parent / ".scripts" / "check_changed_types.py"
BROKEN = 'def f() -> int:\n    return "x"\n'
FINE = "def f() -> int:\n    return 1\n"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_changed_types", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_changed_types"] = module
    spec.loader.exec_module(module)
    return module


checker = _load()


def _project(tmp_path: Path, files: dict[str, str]) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pyright]\ninclude = ["src", ".hidden"]\n', encoding="utf-8"
    )
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def test_a_type_error_in_a_changed_file_in_a_hidden_folder_fails(tmp_path: Path) -> None:
    root = _project(tmp_path, {".hidden/bad.py": BROKEN, "src/good.py": FINE})

    assert checker.main(["--root", str(root), ".hidden/bad.py", "src/good.py"]) == 1


def test_a_type_error_in_a_changed_visible_file_fails(tmp_path: Path) -> None:
    root = _project(tmp_path, {"src/bad.py": BROKEN})

    assert checker.main(["--root", str(root), "src/bad.py"]) == 1


def test_only_the_changed_files_are_checked(tmp_path: Path) -> None:
    root = _project(tmp_path, {"src/bad.py": BROKEN, "src/good.py": FINE, ".hidden/ok.py": FINE})

    assert checker.main(["--root", str(root), "src/good.py", ".hidden/ok.py"]) == 0


def test_a_changed_file_outside_the_included_folders_is_not_checked(tmp_path: Path) -> None:
    root = _project(tmp_path, {"other/bad.py": BROKEN})

    assert checker.main(["--root", str(root), "other/bad.py"]) == 0
