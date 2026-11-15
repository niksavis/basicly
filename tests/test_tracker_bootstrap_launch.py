from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from basicly import tracker_agent


def _launcher(root: Path) -> ModuleType:
    path = tracker_agent.write_launch(root, version="0.45.0")
    spec = importlib.util.spec_from_file_location("machine_bootstrap_probe", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("provider", ["uv", "uvx"])
def test_missing_user_tool_is_provisioned_before_memory_is_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    provider: str,
) -> None:
    module = _launcher(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(module.shutil, "which", lambda name: name if name == provider else None)

    def run(argv: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if "install" in argv:
            return subprocess.CompletedProcess(argv, 0)
        assert "bootstrap" in argv
        assert options["cwd"] == tmp_path
        return subprocess.CompletedProcess(argv, 0, json.dumps({"ready": True, "records": 1}), "")

    monkeypatch.setattr(module.subprocess, "run", run)

    assert module.main() == 0
    assert json.loads(capsys.readouterr().out)["ready"] is True
    if provider == "uv":
        assert calls[0] == [
            "uv",
            "tool",
            "install",
            "--upgrade",
            "--python",
            ">=3.14",
            module.SOURCE,
        ]
        assert calls[1] == [
            "uv",
            "tool",
            "run",
            "--python",
            ">=3.14",
            "--from",
            module.SOURCE,
            "basicly-tracker",
            "bootstrap",
            "--repository",
            str(tmp_path),
        ]
    else:
        assert calls == [
            [
                "uvx",
                "--python",
                ">=3.14",
                "--from",
                module.SOURCE,
                "basicly-tracker",
                "bootstrap",
                "--repository",
                str(tmp_path),
            ]
        ]


def test_missing_runtime_prerequisites_refuse_without_running_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _launcher(tmp_path)
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)

    assert module.main() == 2
    assert "Install uv" in capsys.readouterr().err


def test_usable_install_with_missing_credentials_is_not_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _launcher(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        module.shutil, "which", lambda name: name if name == "basicly-tracker" else None
    )

    def run(argv: list[str], **_options: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return (
            subprocess.CompletedProcess(argv, 0)
            if "--help" in argv
            else subprocess.CompletedProcess(
                argv, 2, "", "Restore vault credentials before work.\n"
            )
        )

    monkeypatch.setattr(module.subprocess, "run", run)

    assert module.main() == 2
    assert "credentials" in capsys.readouterr().err
    assert len(calls) == 2
    assert all(argv[0] == "basicly-tracker" for argv in calls)


def test_onboarding_preserves_unmanaged_bootstrap_file(tmp_path: Path) -> None:
    path = tmp_path / ".basicly/tracker-bootstrap.py"
    path.parent.mkdir()
    path.write_text("a person's existing bootstrap\n")

    with pytest.raises(ValueError, match="unmanaged"):
        tracker_agent.write_launch(tmp_path, version="0.45.0")

    assert path.read_text() == "a person's existing bootstrap\n"
