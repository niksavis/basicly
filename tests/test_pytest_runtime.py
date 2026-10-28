from __future__ import annotations

import json
import os
import subprocess
import venv
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from basicly import checkout, pytest_inputs, verify
from basicly.config import VerifyCheck

COMMAND = (
    "pytest",
    "-q",
    "-n",
    "{workers}",
    "--durations=25",
    "-m",
    "not live_ledger and not fresh_runtime",
)


def _checkout(root: Path, value: str) -> tuple[Path, Path]:
    root.mkdir()
    checkout.git(["init", "-q"], cwd=root)
    (root / ".gitignore").write_text(".venv/\n")
    source = root / "src"
    source.mkdir()
    (source / "checkout_value.py").write_text(f"VALUE = {value!r}\n")
    checkout.git(["add", "."], cwd=root)
    environment = root / ".venv"
    venv.EnvBuilder(with_pip=False).create(environment)
    interpreter = environment / "bin/python"
    site = Path(
        subprocess.check_output(
            [str(interpreter), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            text=True,
        ).strip()
    )
    (site / "checkout.pth").write_text(str(source) + "\n")
    package = site / "pytest"
    package.mkdir()
    (package / "__init__.py").write_text("")
    launcher = environment / "bin/pytest"
    launcher.write_text(
        f"#!{interpreter}\n"
        "import json, os, subprocess, sys\n"
        "import checkout_value\n"
        "source = 'import checkout_value; print(checkout_value.VALUE)'\n"
        "child = subprocess.check_output([sys.executable, '-c', source], text=True).strip()\n"
        "path_child = subprocess.check_output(['python', '-c', source], text=True).strip()\n"
        "print(json.dumps(dict(value=checkout_value.VALUE, child=child, path_child=path_child, "
        "python=sys.executable, virtual_env=os.environ.get('VIRTUAL_ENV'))))\n"
    )
    launcher.chmod(0o755)
    return interpreter, site


@pytest.fixture
def runtime_checkouts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    if os.name != "posix":
        pytest.skip("the reviewed shebang runtime requires POSIX")
    base, lane = tmp_path / "base", tmp_path / "lane"
    base_python, _ = _checkout(base, "base")
    lane_python, site = _checkout(lane, "lane")
    monkeypatch.setenv("PATH", str(base_python.parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("VIRTUAL_ENV", str(base / ".venv"))
    for name in ("PYTHONPATH", "PYTHONHOME"):
        monkeypatch.delenv(name, raising=False)
    original_dependency = pytest_inputs._dependency

    def dependency(root: Path, workspace: Path | None = None) -> str:
        if not root.is_relative_to(tmp_path):
            return "unchanged-shared-standard-library"
        return original_dependency(root, workspace)

    monkeypatch.setattr(pytest_inputs, "_dependency", dependency)
    return lane, lane_python, site


def test_reviewed_pytest_uses_lane_runtime_for_tests_and_both_child_launch_forms(
    runtime_checkouts: tuple[Path, Path, Path],
) -> None:
    lane, interpreter, _ = runtime_checkouts
    check = VerifyCheck("tests", COMMAND, frozenset({"full"}), reuse="pytest")
    result = verify.run_check(check, lane, "full", capture=True)
    assert result.status == "pass", result.output
    assert json.loads(result.output) == {
        "value": "lane",
        "child": "lane",
        "path_child": "lane",
        "python": str(interpreter),
        "virtual_env": str(lane / ".venv"),
    }


def test_fingerprint_uses_the_selected_lane_runtime(
    runtime_checkouts: tuple[Path, Path, Path],
) -> None:
    lane, _, _ = runtime_checkouts
    proof, reason = verify._inputs(lane, "pytest")
    assert proof, reason


def test_selected_runtime_package_changes_invalidate_the_proof(
    runtime_checkouts: tuple[Path, Path, Path],
) -> None:
    lane, _, site = runtime_checkouts
    before = pytest_inputs.fingerprint(lane)
    (site / "pytest/__init__.py").write_text("VALUE = 'changed'\n")
    assert pytest_inputs.fingerprint(lane) != before


def test_missing_lane_pytest_refuses_to_run_the_base_launcher(
    runtime_checkouts: tuple[Path, Path, Path],
) -> None:
    lane, interpreter, _ = runtime_checkouts
    (interpreter.parent / "pytest").unlink()
    check = VerifyCheck("tests", COMMAND, frozenset({"full"}), reuse="pytest")
    result = verify.run_check(check, lane, "full", capture=True)
    assert result.status == "fail"
    assert result.returncode == 127
    assert not verify._inputs(lane, "pytest")[0]


def test_unsupported_pytest_command_retains_ambient_runtime_selection(
    runtime_checkouts: tuple[Path, Path, Path],
) -> None:
    lane, _, _ = runtime_checkouts
    check = VerifyCheck("tests", ("pytest", "-q"), frozenset({"full"}), reuse="pytest")
    result = verify.run_check(check, lane, "full", capture=True)
    assert result.status == "pass"
    assert json.loads(result.output)["value"] == "base"


def test_external_editable_import_still_refuses_a_lane_receipt(
    runtime_checkouts: tuple[Path, Path, Path],
) -> None:
    lane, _, site = runtime_checkouts
    (site / "external.pth").write_text(str(lane.parent / "base/src") + "\n")
    proof, reason = verify._inputs(lane, "pytest")
    assert not proof
    assert "external pytest import path" in reason


@pytest.mark.parametrize("name", ["PYTHONPATH", "PYTHONHOME"])
def test_unreviewed_import_overrides_do_not_receive_a_trusted_proof(
    runtime_checkouts: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    lane, _, _ = runtime_checkouts
    monkeypatch.setenv(name, str(lane.parent / "base"))
    proof, reason = verify._inputs(lane, "pytest")
    assert not proof
    assert reason


@pytest.mark.parametrize(
    "root, expected",
    [
        (PurePosixPath("/repo"), PurePosixPath("/repo/.venv/bin/pytest")),
        (PureWindowsPath("C:/repo"), PureWindowsPath("C:/repo/.venv/Scripts/pytest.exe")),
    ],
)
def test_project_launchers_follow_the_checkout_platform(
    root: PurePosixPath | PureWindowsPath, expected: PurePosixPath | PureWindowsPath
) -> None:
    assert pytest_inputs._project_launcher(root) == expected


@pytest.mark.parametrize(
    "change, reason",
    [
        ("shape", "invalid pytest interpreter metadata"),
        ("relative", "invalid pytest interpreter metadata"),
        ("identity", "pytest interpreter identity changed"),
        ("import", "unsupported pytest import location"),
    ],
)
def test_untrusted_runtime_metadata_cannot_issue_a_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    change: str,
    reason: str,
) -> None:
    interpreter = tmp_path / "python"
    payload = {
        "purelib": str(tmp_path / "site"),
        "stdlib": str(tmp_path / "stdlib"),
        "executable": str(interpreter),
        "pytest": str(tmp_path / "site/pytest/__init__.py"),
    }
    if change == "shape":
        payload.clear()
    elif change == "relative":
        payload["purelib"] = "relative"
    elif change == "identity":
        payload["executable"] = str(tmp_path / "other")
    else:
        payload["pytest"] = str(tmp_path / "external/pytest/__init__.py")
    monkeypatch.setattr(
        pytest_inputs.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, json.dumps(payload), ""),
    )
    with pytest.raises(verify.pyright_inputs.UnsupportedInputsError, match=reason):
        pytest_inputs._runtime_paths(interpreter, tmp_path, dict(os.environ))
