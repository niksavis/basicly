from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404
import sys
import threading
from pathlib import Path
from urllib.request import urlopen

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def package(tmp_path: Path) -> Path:
    target = tmp_path / "basicly_tracker"
    target.mkdir()
    shutil.copyfile(
        ROOT / "packages/basicly-tracker/basicly_tracker/__init__.py", target / "__init__.py"
    )
    for name in ("installer", "modes", "bundle", "plugin"):
        shutil.copyfile(ROOT / "packages" / f"kit_{name}.py", target / f"{name}.py")
    for name, source in (("kit", "tracker"), ("board", "board")):
        shutil.copytree(
            ROOT / ".basicly/core/kit" / source,
            target / name,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    return target


def run_package(package: Path, *args: str) -> subprocess.CompletedProcess[str]:
    code = (
        "import importlib.util,sys; "
        "s=importlib.util.spec_from_file_location('basicly_tracker',sys.argv[1]); "
        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
        "raise SystemExit(m.main(sys.argv[2:]))"
    )
    return subprocess.run(
        [sys.executable, "-I", "-S", "-c", code, str(package / "__init__.py"), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_plugin_contains_portable_identity_shared_guidance_and_standalone_cli(
    package: Path, tmp_path: Path
) -> None:
    out = tmp_path / "plugin"
    result = run_package(package, "plugin", "--out", str(out))
    assert result.returncode == 0, result.stderr
    manifest = json.loads((out / "plugin.json").read_text())
    assert manifest["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert manifest["name"] == "basicly-tracker"
    assert set(manifest) == {"$schema", "name", "version", "description", "author"}
    assert manifest["author"] == {"name": "basicly"}
    compatibility = json.loads((out / ".claude-plugin/plugin.json").read_text())
    assert compatibility == {key: value for key, value in manifest.items() if key != "$schema"}
    skill = out / "skills/basicly-tracker"
    guidance = (skill / "SKILL.md").read_text()
    assert "name: basicly-tracker" in guidance
    assert "scripts/tracker.pyz" in guidance
    assert "basicly tracker write --" in guidance
    assert "serve .basicly/ledger" in guidance
    assert "python3 .basicly/kit/tracker/cli.py" not in guidance
    environment = os.environ.copy()
    environment["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    environment["LOCALAPPDATA"] = str(tmp_path / "cache")
    bundle = skill / "scripts/tracker.pyz"
    completed = subprocess.run(
        [sys.executable, "-I", "-S", str(bundle), "--help"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert "ready" in completed.stdout
    served = subprocess.run(
        [sys.executable, "-I", "-S", str(bundle), "serve", "--help"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert served.returncode == 0, served.stderr
    assert "--port" in served.stdout


def test_plugin_refuses_existing_destination_without_overwriting(
    package: Path, tmp_path: Path
) -> None:
    out = tmp_path / "existing"
    out.mkdir()
    sentinel = out / "keep.txt"
    sentinel.write_text("a person's work")
    result = run_package(package, "plugin", "--out", str(out))
    assert result.returncode == 1
    assert "already exists" in result.stderr
    assert sentinel.read_text() == "a person's work"
    assert list(out.iterdir()) == [sentinel]


def test_plugin_refuses_missing_built_ui_before_creating_destination(
    package: Path, tmp_path: Path
) -> None:
    (package / "board/server.py").unlink()
    out = tmp_path / "plugin"
    result = run_package(package, "plugin", "--out", str(out))
    assert result.returncode == 1
    assert "built tracker package" in result.stderr
    assert not out.exists()


def test_tracker_package_declares_the_shared_ui_and_plugin_exporter() -> None:
    manifest = (ROOT / "packages/basicly-tracker/pyproject.toml").read_text()
    assert '"../../.basicly/core/kit/board" = "basicly_tracker/board"' in manifest
    assert '"../kit_plugin.py" = "basicly_tracker/plugin.py"' in manifest
    assert "dependencies = []" in manifest


def test_exported_plugin_serves_the_same_ledger_without_engine(
    package: Path, tmp_path: Path
) -> None:
    out = tmp_path / "plugin"
    exported = run_package(package, "plugin", "--out", str(out))
    assert exported.returncode == 0, exported.stderr
    bundle = out / "skills/basicly-tracker/scripts/tracker.pyz"
    ledger = tmp_path / "ledger"
    environment = os.environ.copy()
    environment["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    environment["LOCALAPPDATA"] = str(tmp_path / "cache")
    created = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(bundle),
            "create",
            str(ledger),
            "--prefix",
            "demo",
            "--title",
            "One shared ledger",
            "--description",
            "When I plan work, I want to record it, so I can share its progress.",
            "--acceptance",
            "When work is recorded, the tracker shall display it.",
            "--requirements",
            "The ledger shall be shared by CLI and UI.",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert created.returncode == 0, created.stderr
    process = subprocess.Popen(
        [sys.executable, "-I", "-S", str(bundle), "serve", str(ledger), "--port", "0"],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    stderr = process.stderr
    assert stderr is not None
    lines: list[str] = []
    reader = threading.Thread(target=lambda: lines.append(stderr.readline()), daemon=True)
    reader.start()
    try:
        reader.join(timeout=30)
        assert not reader.is_alive(), "the bundled server did not announce readiness"
        assert lines and "http://" in lines[0], lines
        url = lines[0].split("http://", 1)[1].split(" ", 1)[0]
        with urlopen("http://" + url, timeout=10) as response:
            assert response.status == 200
            assert b"<!" in response.read()
        with urlopen("http://" + url + "api/v1/records", timeout=10) as response:
            report = json.loads(response.read())
        assert "One shared ledger" in json.dumps(report)
    finally:
        process.terminate()
        process.wait(timeout=30)
        stderr.close()
        reader.join(timeout=30)
