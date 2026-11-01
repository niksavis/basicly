from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from types import SimpleNamespace

import pytest

from basicly import pyright_inputs


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    tmp_path = tmp_path / "project"
    tmp_path.mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pyright]\nvenvPath="."\nvenv=".venv"\nextraPaths=["src", "."]\n',
        encoding="utf-8",
    )
    (tmp_path / ".venv/lib/python3.14/site-packages").mkdir(parents=True)
    (tmp_path / ".venv/pyvenv.cfg").write_text("version_info = 3.14.8\n", encoding="utf-8")
    (tmp_path / "entry.py").write_text("import optional\n", encoding="utf-8")
    monkeypatch.setattr(pyright_inputs, "_runtime", lambda _root: {"runtime": "fixed"})
    return tmp_path


@pytest.mark.parametrize(
    "operation", ["add", "delete", "rename", "content", "config", "stub", "search"]
)
def test_inventory_and_content_changes_invalidate(project: Path, operation: str) -> None:
    before = pyright_inputs.fingerprint(project)
    source = project / "entry.py"
    site = project / ".venv/lib/python3.14/site-packages"
    if operation == "add":
        (project / "optional.py").write_text("answer = 'wrong type'\n", encoding="utf-8")
    elif operation == "delete":
        source.unlink()
    elif operation == "rename":
        source.rename(project / "renamed.py")
    elif operation == "content":
        source.write_text("import different\n", encoding="utf-8")
    elif operation == "config":
        config = project / "pyproject.toml"
        config.write_text(
            config.read_text(encoding="utf-8") + 'pythonVersion="3.13"\n', encoding="utf-8"
        )
    elif operation == "stub":
        (site / "typing.pyi").write_text("value: str\n", encoding="utf-8")
    else:
        (site / "local.pth").write_text(str(project) + "\n", encoding="utf-8")

    assert pyright_inputs.fingerprint(project) != before


def test_unrelated_output_does_not_invalidate(project: Path) -> None:
    before = pyright_inputs.fingerprint(project)
    output = project / ".basicly/usage"
    output.mkdir(parents=True)
    (output / "verify-run.json").write_text('{"passed": true}', encoding="utf-8")
    assert pyright_inputs.fingerprint(project) == before


@pytest.mark.parametrize("directory", ["node_modules", "__pycache__"])
def test_explicit_python_imports_inside_discovery_exclusions_invalidate(
    project: Path, directory: str
) -> None:
    module = project / directory / "optional.py"
    module.parent.mkdir()
    module.write_text("answer: int = 1\n", encoding="utf-8")
    before = pyright_inputs.fingerprint(project)
    module.write_text("answer: str = 'changed'\n", encoding="utf-8")
    assert pyright_inputs.fingerprint(project) != before


def test_equivalent_sibling_analysis_environments_match(project: Path, tmp_path: Path) -> None:
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    for name in ("pyproject.toml", "entry.py"):
        shutil.copyfile(project / name, sibling / name)
    site = ".venv/lib/python3.14/site-packages"
    (sibling / site).mkdir(parents=True)
    shutil.copyfile(project / ".venv/pyvenv.cfg", sibling / ".venv/pyvenv.cfg")
    (project / site / "local.pth").write_text(str(project) + "\n", encoding="utf-8")
    (sibling / site / "local.pth").write_text(str(sibling) + "\n", encoding="utf-8")
    assert pyright_inputs.fingerprint(project) == pyright_inputs.fingerprint(sibling)


@pytest.mark.parametrize(
    "path",
    [
        PurePosixPath("../outside"),
        PurePosixPath("/outside"),
        PureWindowsPath("C:/outside"),
        PureWindowsPath("//server/share/stubs"),
        PureWindowsPath("/rooted"),
        PurePosixPath(".env/private"),
        PurePosixPath("node_modules/stubs"),
    ],
)
def test_external_or_protected_search_paths_refuse_reuse(project: Path, path: PurePath) -> None:
    config = project / "pyproject.toml"
    config.write_text(
        '[tool.pyright]\nvenvPath="."\nvenv=".venv"\nextraPaths=[' + json.dumps(str(path)) + "]\n",
        encoding="utf-8",
    )
    with pytest.raises(pyright_inputs.UnsupportedInputsError):
        pyright_inputs.fingerprint(project)


def test_external_pth_refuses_reuse(project: Path) -> None:
    pth = project / ".venv/lib/python3.14/site-packages/external.pth"
    pth.write_text(str(project.parent / "external") + "\n", encoding="utf-8")
    with pytest.raises(pyright_inputs.UnsupportedInputsError, match="external analysis"):
        pyright_inputs.fingerprint(project)


def test_unknown_configuration_refuses_reuse(project: Path) -> None:
    (project / "pyrightconfig.json").write_text("{}", encoding="utf-8")
    with pytest.raises(pyright_inputs.UnsupportedInputsError, match="reviewed input recipe"):
        pyright_inputs.fingerprint(project)


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    binary = tmp_path / "bin"
    binary.mkdir()
    interpreter = binary / "python"
    interpreter.write_bytes(b"python-runtime")
    body = (
        "# -*- coding: utf-8 -*-\nimport sys\nfrom pyright.cli import entrypoint\n"
        'if __name__ == "__main__":\n'
        '    if sys.argv[0].endswith("-script.pyw"):\n'
        "        sys.argv[0] = sys.argv[0][:-11]\n"
        '    elif sys.argv[0].endswith(".exe"):\n'
        "        sys.argv[0] = sys.argv[0][:-4]\n"
        "    sys.exit(entrypoint())\n"
    )
    (binary / "pyright").write_text(f"#!{interpreter}\n{body}", encoding="utf-8")
    (binary / "node").write_bytes(b"node-runtime")
    site = tmp_path / "site"
    (site / "pyright/dist").mkdir(parents=True)
    (site / "pyright/__init__.py").write_text("", encoding="utf-8")
    (site / "pyright/dist/package.json").write_text('{"version":"1.1.411"}', encoding="utf-8")
    stdlib = tmp_path / "stdlib"
    stdlib.mkdir()
    (stdlib / "typing.py").write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setattr(
        pyright_inputs,
        "os",
        SimpleNamespace(
            name="posix", environ={"PATH": str(binary)}, pathsep=os.pathsep, defpath=os.defpath
        ),
    )
    monkeypatch.setattr(
        pyright_inputs,
        "_interpreter_paths",
        lambda *_args: {
            "purelib": str(site),
            "stdlib": str(stdlib),
            "pyright": str(site / "pyright/__init__.py"),
            "nodejs_wheel": "",
        },
    )
    monkeypatch.setattr(pyright_inputs.shutil, "which", lambda name, **_kw: str(binary / name))
    return tmp_path


@pytest.mark.parametrize("changed", ["node", "python", "stdlib", "tool", "environment"])
def test_runtime_changes_change_identity(runtime: Path, changed: str) -> None:
    before = pyright_inputs._runtime(runtime)
    if changed == "node":
        (runtime / "bin/node").write_bytes(b"new-node")
    elif changed == "python":
        (runtime / "bin/python").write_bytes(b"new-python")
    elif changed == "stdlib":
        (runtime / "stdlib/typing.py").write_text("value = 2\n", encoding="utf-8")
    elif changed == "tool":
        (runtime / "site/pyright/new.py").write_text("value = 3\n", encoding="utf-8")
    else:
        pyright_inputs.os.environ["PYTHONWARNINGS"] = "changed"
    assert pyright_inputs._runtime(runtime) != before


def test_shared_compiler_runtime_matches_across_virtual_environment_boundaries(
    runtime: Path,
) -> None:
    sibling = runtime.parent / "sibling"
    sibling.mkdir()
    for root in (runtime, sibling):
        (root / "pyproject.toml").write_text(
            '[tool.pyright]\nvenvPath="."\nvenv=".venv"\n', encoding="utf-8"
        )
    pyright_inputs.os.environ["VIRTUAL_ENV"] = str(runtime / ".venv")
    origin = pyright_inputs._runtime(runtime)

    assert pyright_inputs._runtime(sibling) == origin


@pytest.mark.parametrize("override", ["PYTHONPATH", "NODE_OPTIONS", "PYRIGHT_PYTHON_FORCE_VERSION"])
def test_runtime_overrides_refuse_reuse(runtime: Path, override: str) -> None:
    pyright_inputs.os.environ[override] = "unsupported"
    with pytest.raises(pyright_inputs.UnsupportedInputsError, match="runtime override"):
        pyright_inputs._runtime(runtime)


def test_new_compiler_version_executes_fresh_until_recipe_is_reviewed(runtime: Path) -> None:
    (runtime / "site/pyright/dist/package.json").write_text('{"version":"1.2.0"}', encoding="utf-8")
    with pytest.raises(pyright_inputs.UnsupportedInputsError, match="unsupported Pyright version"):
        pyright_inputs._runtime(runtime)


def test_custom_launcher_cannot_claim_the_reviewed_compiler_recipe(runtime: Path) -> None:
    launcher = runtime / "bin/pyright"
    launcher.write_text(
        launcher.read_text(encoding="utf-8") + 'print("custom behavior")\n', encoding="utf-8"
    )
    with pytest.raises(pyright_inputs.UnsupportedInputsError, match="unreviewed Pyright launcher"):
        pyright_inputs._runtime(runtime)


def test_metadata_comes_from_the_tools_actual_interpreter(tmp_path: Path) -> None:
    interpreter = Path(sys.executable)
    data = pyright_inputs._interpreter_paths(interpreter, tmp_path, dict(pyright_inputs.os.environ))
    assert Path(data["purelib"]).is_dir()
    assert Path(data["stdlib"]).is_dir()
    assert Path(data["pyright"]).name == "__init__.py"


def test_unavailable_tools_interpreter_refuses_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_args, **_kw):
        raise subprocess.TimeoutExpired("python", 10)

    monkeypatch.setattr(pyright_inputs.subprocess, "run", fail)
    with pytest.raises(pyright_inputs.UnsupportedInputsError, match="metadata unavailable"):
        pyright_inputs._interpreter_paths(tmp_path / "unknown-python", tmp_path, {})


def test_controlled_checks_preserve_the_prepared_environment(tmp_path: Path) -> None:
    source = {**os.environ, "UV_NO_SYNC": "1", "PYTHONPYCACHEPREFIX": "positive-control"}
    selected = pyright_inputs.execution_environment(tmp_path, source)
    assert selected["PYTHONPYCACHEPREFIX"] == "positive-control"
    assert selected.get("UV_NO_SYNC") == "1"
