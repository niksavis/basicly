from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from basicly import checkout, hook_python, pyright_inputs, pytest_inputs, verify


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    checkout.git(["init", "-q"], cwd=tmp_path)
    (tmp_path / "source.py").write_text("value = 1\n")
    checkout.git(["add", "source.py"], cwd=tmp_path)
    return tmp_path


def test_tracker_only_changes_preserve_the_workspace_proof(repository: Path) -> None:
    before = pytest_inputs._workspace(repository)
    ledger = repository / ".basicly/ledger"
    ledger.mkdir(parents=True)
    (ledger / "pending-main.jsonl").write_text("tracker metadata\n")
    checkout.git(["add", ".basicly/ledger"], cwd=repository)
    assert pytest_inputs._workspace(repository) == before


@pytest.mark.parametrize("name", ["source.py", "tests/test_source.py", "README.md", "uv.lock"])
def test_changed_source_tests_documentation_and_dependencies_change_proof(
    repository: Path, name: str
) -> None:
    before = pytest_inputs._workspace(repository)
    path = repository / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("changed\n")
    assert pytest_inputs._workspace(repository) != before


def test_deleted_tracked_input_refuses_reuse(repository: Path) -> None:
    (repository / "source.py").unlink()
    with pytest.raises(OSError):
        pytest_inputs._workspace(repository)


def test_ignored_build_artifacts_remain_test_inputs(repository: Path) -> None:
    (repository / ".gitignore").write_text("build/\n")
    before = pytest_inputs._workspace(repository)
    artifacts = repository / "build/lefthook"
    artifacts.mkdir(parents=True)
    (artifacts / "archive.tar.gz").write_bytes(b"changed")
    assert pytest_inputs._workspace(repository) != before


def test_protected_inventory_is_refused_before_opening(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        checkout, "git", lambda *_a, **_kw: subprocess.CompletedProcess([], 0, ".env\0", "")
    )
    with pytest.raises(pyright_inputs.UnsupportedInputsError, match="protected"):
        pytest_inputs._workspace(repository)


def test_only_the_reviewed_full_suite_command_is_eligible() -> None:
    command = (
        "pytest",
        "-q",
        "-n",
        "4",
        "--durations=25",
        "-m",
        "not live_ledger and not fresh_runtime",
    )
    assert pytest_inputs.supported_command(command, "full")
    assert not pytest_inputs.supported_command(command, "fast")
    assert not pytest_inputs.supported_command(("pytest", "-q"), "full")
    assert not pytest_inputs.supported_command((*command, "-k", "one_test"), "full")


def test_execution_and_proof_share_the_scrubbed_environment() -> None:
    environment = {"PATH": "tools", "GIT_DIR": "elsewhere", "SHLVL": "9", "CUSTOM": "kept"}
    assert pytest_inputs.execution_environment(environment) == {
        **hook_python.ENVIRONMENT,
        "PATH": "tools",
        "CUSTOM": "kept",
        "NO_COLOR": "1",
        "UV_OFFLINE": "1",
    }


def test_external_import_paths_refuse_runtime_reuse(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / "external.pth").write_text(str(tmp_path / "external") + "\n")
    with pytest.raises(pyright_inputs.UnsupportedInputsError, match="external pytest import path"):
        pytest_inputs._dependency(site, tmp_path / "workspace")


def test_changed_runtime_package_changes_the_runtime_proof(tmp_path: Path) -> None:
    package = tmp_path / "package.py"
    package.write_text("value = 1\n")
    before = pytest_inputs._dependency(tmp_path)
    package.write_text("value = 2\n")
    assert pytest_inputs._dependency(tmp_path) != before


def test_empty_launcher_runs_fresh_instead_of_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = tmp_path / "pytest"
    launcher.write_bytes(b"")
    monkeypatch.setattr(pytest_inputs.shutil, "which", lambda *_a, **_kw: str(launcher))
    inputs, reason = verify._inputs(tmp_path, "pytest")
    assert inputs == ""
    expected = (
        "empty pytest launcher" if os.name == "posix" else "unsupported pytest launcher platform"
    )
    assert expected in reason


def test_hook_dispatch_and_nested_uv_environment_do_not_change_execution_inputs() -> None:
    base = {"PATH": os.pathsep.join(["venv", "tools"]), "CUSTOM": "kept"}
    nested = {
        **base,
        "PATH": os.pathsep.join(["venv", "venv", "tools"]),
        "UV_RUN_RECURSION_DEPTH": "3",
        "BASICLY_HOOK_RESULT": "temporary",
        "BASICLY_HOOK_SPECS": "dispatcher",
        "LEFTHOOK_VERBOSE": "1",
        "PWD": "shell",
    }
    assert pytest_inputs.execution_environment(base) == pytest_inputs.execution_environment(nested)


def test_native_git_and_lefthook_context_matches_direct_execution() -> None:
    base = {"PATH": os.pathsep.join(["venv", "tools"]), "NO_COLOR": "1"}
    hook = {
        "PATH": os.pathsep.join(["venv", "git-core", "tools"]),
        "GIT_EXEC_PATH": "git-core",
        "NO_COLOR": "true",
        "BASICLY_FOLD_COMMAND": "dispatcher",
    }
    assert pytest_inputs.execution_environment(base) == pytest_inputs.execution_environment(hook)


@pytest.mark.parametrize(
    "name",
    [
        "ALL_PROXY",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "FTP_PROXY",
        "WS_PROXY",
        "WSS_PROXY",
        "NPM_CONFIG_PROXY",
        "NPM_CONFIG_HTTP_PROXY",
        "NPM_CONFIG_HTTPS_PROXY",
        "PIP_PROXY",
        "BUNDLE_HTTP_PROXY",
        "BUNDLE_HTTPS_PROXY",
        "DOCKER_HTTP_PROXY",
        "DOCKER_HTTPS_PROXY",
        "YARN_HTTP_PROXY",
        "YARN_HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "npm_config_proxy",
        "CODEX_TOOL_CALL_ID",
    ],
)
def test_rotating_tool_transport_is_absent_from_the_actual_pytest_environment(name: str) -> None:
    base = {"PATH": "tools", "NO_PROXY": "localhost", "CUSTOM": "kept"}
    before = {**base, name: "transport-before"}
    after = {**base, name: "transport-after"}
    expected = {**base, **hook_python.ENVIRONMENT, "NO_COLOR": "1", "UV_OFFLINE": "1"}
    assert pytest_inputs.execution_environment(before) == expected
    assert pytest_inputs.execution_environment(after) == expected
    assert before[name] == "transport-before"


@pytest.mark.parametrize(
    "name", ["NO_PROXY", "no_proxy", "SSL_CERT_FILE", "PYTHONWARNINGS", "CUSTOM"]
)
def test_material_pytest_environment_inputs_are_preserved(name: str) -> None:
    assert pytest_inputs.execution_environment({name: "before"})[name] == "before"
    assert pytest_inputs.execution_environment({name: "after"})[name] == "after"


def test_previous_command_is_fresh_and_cached_execution_forces_uv_offline() -> None:
    previous = ("pytest", "-q", "-n", "4", "--durations=25", "-m", "not live_ledger")
    assert not pytest_inputs.supported_command(previous, "full")
    assert pytest_inputs.execution_environment({"UV_OFFLINE": "0"})["UV_OFFLINE"] == "1"


def test_absent_hook_runtime_defaults_match_the_installed_hook_environment() -> None:
    base = {"PATH": "tools", "CUSTOM": "kept"}
    hook = {
        **base,
        "UV_PYTHON": ">=3.14",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    assert pytest_inputs.execution_environment(base) == pytest_inputs.execution_environment(hook)


@pytest.mark.parametrize(
    "name, value",
    [("UV_PYTHON", "3.15"), ("PYTHONUTF8", "0"), ("PYTHONIOENCODING", "ascii")],
)
def test_explicit_runtime_overrides_remain_material_inputs(name: str, value: str) -> None:
    base = {"PATH": "tools"}
    changed = {**base, name: value}
    assert pytest_inputs.execution_environment(changed)[name] == value
    assert pytest_inputs.execution_environment(base) != pytest_inputs.execution_environment(changed)


@pytest.fixture
def fingerprint_repository(repository: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    if os.name != "posix":
        pytest.skip("the reviewed pytest launcher requires POSIX")
    runtime = repository / ".runtime"
    runtime.mkdir()
    (repository / ".gitignore").write_text(".runtime/\n")
    interpreter = runtime / "python"
    interpreter.write_bytes(b"interpreter-before")
    launcher = runtime / "pytest"
    launcher.write_text(f"#!{interpreter}\n")
    for name in ("purelib", "stdlib"):
        (runtime / name).mkdir()
        (runtime / name / "module.py").write_text("value = 1\n")
    original_which = pytest_inputs.shutil.which
    monkeypatch.setattr(
        pytest_inputs,
        "_runtime_paths",
        lambda *_args: {
            "executable": str(interpreter),
            "purelib": str(runtime / "purelib"),
            "stdlib": str(runtime / "stdlib"),
            "pytest": str(runtime / "purelib/pytest/__init__.py"),
        },
    )
    monkeypatch.setattr(
        pytest_inputs.shutil,
        "which",
        lambda name, **kwargs: (
            str(launcher) if name == "pytest" else original_which(name, **kwargs)
        ),
    )
    return repository


def test_hook_defaults_preserve_real_fingerprint(
    fingerprint_repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in hook_python.ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    before = pytest_inputs.fingerprint(fingerprint_repository)
    for name, value in hook_python.ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    assert pytest_inputs.fingerprint(fingerprint_repository) == before


@pytest.mark.parametrize(
    "name",
    [
        "source.py",
        "basicly.toml",
        ".runtime/python",
        ".runtime/purelib/module.py",
        ".runtime/stdlib/module.py",
    ],
)
def test_material_file_changes_invalidate_real_fingerprint(
    fingerprint_repository: Path, name: str
) -> None:
    before = pytest_inputs.fingerprint(fingerprint_repository)
    (fingerprint_repository / name).write_bytes(b"changed")
    assert pytest_inputs.fingerprint(fingerprint_repository) != before


@pytest.mark.parametrize("name", ["UV_PYTHON", "PYTHONUTF8", "PYTHONIOENCODING", "CUSTOM"])
def test_material_environment_changes_invalidate_real_fingerprint(
    fingerprint_repository: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    before = pytest_inputs.fingerprint(fingerprint_repository)
    monkeypatch.setenv(name, "different")
    assert pytest_inputs.fingerprint(fingerprint_repository) != before


def test_external_standard_library_changes_invalidate_landing(
    repository: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path_factory.mktemp("external-stdlib")
    module = library / "runtime.py"
    module.write_text("before")
    original = pytest_inputs.checkout.run

    def metadata(arguments, **kwargs):
        if arguments[1:3] == ["-I", "-c"]:
            return subprocess.CompletedProcess(arguments, 0, json.dumps([str(library)]), "")
        return original(arguments, **kwargs)

    monkeypatch.setattr(pytest_inputs.checkout, "run", metadata)
    before = pytest_inputs.landing_fingerprint(repository, ["python"], "a" * 40, {})
    assert before == pytest_inputs.landing_fingerprint(repository, ["python"], "a" * 40, {})
    module.write_text("after")
    after = pytest_inputs.landing_fingerprint(repository, ["python"], "a" * 40, {})
    assert after != before
