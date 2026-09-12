from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


def _load_hook():
    script_path = (
        Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks" / "catalog-lint.py"
    )
    spec = importlib.util.spec_from_file_location("catalog_lint_hook", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prefers_basicly_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_hook()
    monkeypatch.setattr(
        module.shutil, "which", lambda name: "/usr/bin/basicly" if name == "basicly" else None
    )
    assert module._cli_command() == ["/usr/bin/basicly", "catalog", "lint"]


def test_falls_back_to_importable_module(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_hook()
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)
    assert module._cli_command() == [sys.executable, "-m", "basicly.cli", "catalog", "lint"]


def test_falls_back_to_uvx(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_hook()
    monkeypatch.setattr(
        module.shutil, "which", lambda name: "/usr/bin/uvx" if name == "uvx" else None
    )
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda _name: None)
    assert module._cli_command() == [
        "/usr/bin/uvx",
        "--from",
        module.dist_source(),
        "basicly",
        "catalog",
        "lint",
    ]


def test_dist_source_pins_the_installed_version(tmp_path: Path) -> None:
    module = _load_hook()
    state = tmp_path / module.INSTALL_STATE
    state.parent.mkdir(parents=True)
    state.write_text('{"basicly_version": "0.5.1"}', encoding="utf-8")

    assert module.dist_source(tmp_path) == "git+https://github.com/niksavis/basicly@v0.5.1"


@pytest.mark.parametrize(
    "payload",
    ["", "{}", "not json", '{"basicly_version": ""}', '{"basicly_version": 7}'],
    ids=["empty", "no-version", "malformed", "blank-version", "non-string"],
)
def test_dist_source_falls_back_to_the_branch(tmp_path: Path, payload: str) -> None:
    module = _load_hook()
    state = tmp_path / module.INSTALL_STATE
    state.parent.mkdir(parents=True)
    state.write_text(payload, encoding="utf-8")

    assert module.dist_source(tmp_path) == module.DIST_FALLBACK


def test_dist_source_falls_back_without_a_state_file(tmp_path: Path) -> None:
    module = _load_hook()

    assert module.dist_source(tmp_path) == module.DIST_FALLBACK


def test_advisory_skip_when_no_channel(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    module = _load_hook()
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda _name: None)

    assert module.main() == 0
    err = capsys.readouterr().err
    assert "catalog-lint skipped" in err
    assert "basicly-gates.yml" in err


def _stub_run(monkeypatch: pytest.MonkeyPatch, module, returncode: int, stderr: str) -> None:

    result = subprocess.CompletedProcess(["uvx"], returncode, stdout="", stderr=stderr)
    monkeypatch.setattr(module.subprocess, "run", lambda *_a, **_kw: result)


def test_a_pin_whose_tag_was_never_pushed_is_advisory(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    module = _load_hook()
    monkeypatch.setattr(
        module.shutil, "which", lambda name: "/usr/bin/uvx" if name == "uvx" else None
    )
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda _name: None)
    _stub_run(monkeypatch, module, 1, "fatal: couldn't find remote ref refs/tags/v9.9.9\n")

    assert module.main() == 0
    assert "does not resolve" in capsys.readouterr().err


def test_a_real_catalog_violation_still_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_hook()
    monkeypatch.setattr(
        module.shutil, "which", lambda name: "/usr/bin/uvx" if name == "uvx" else None
    )
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda _name: None)
    _stub_run(monkeypatch, module, 1, "catalog lint: FAILED\n  no 'invocation' declared\n")

    assert module.main() == 1
    assert "no 'invocation' declared" in capsys.readouterr().err
