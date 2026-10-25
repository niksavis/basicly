from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.fresh_runtime


@pytest.mark.parametrize("dirty", [False, True])
def test_distributions_bundle_verified_assets_and_sdist_rebuilds_offline(
    work_repo: Path, tmp_path: Path, dirty: bool
) -> None:
    core = work_repo / ".basicly/core"
    resource = core / "templates/usage/schema.json"
    resource.parent.mkdir(parents=True, exist_ok=True)
    resource.write_text('{"resource": true}\n', encoding="utf-8")
    expected = {
        path.relative_to(core).as_posix(): path.read_bytes()
        for path in core.rglob("*")
        if path.is_file()
    }
    expected_source = {
        path.relative_to(work_repo).as_posix() for path in work_repo.rglob("*") if path.is_file()
    }
    manifest = json.loads((work_repo / "src/basicly/lefthook_assets/manifest.json").read_text())
    bundled = {
        f"src/basicly/lefthook_assets/{entry['bundled']}"
        for entry in manifest["assets"]
        if "bundled" in entry
    }
    expected_source -= bundled
    assert "kit/tracker/events.py" in expected
    if dirty:
        for relative in ["kit/tracker", "kit/tier", "templates"]:
            runtime = core / relative / ".basicly/usage/tool-usage.json"
            runtime.parent.mkdir(parents=True)
            runtime.write_text('{"runtime": true}\n', encoding="utf-8")
        for relative in [
            ".basicly/usage/verify-checks.json",
            ".basicly/bin/lefthook",
            ".import_linter_cache/cache.json",
        ]:
            runtime = work_repo / relative
            runtime.parent.mkdir(parents=True, exist_ok=True)
            runtime.write_text("runtime only\n", encoding="utf-8")
    output = tmp_path / "distributions"
    (work_repo / "build/lefthook/2.2.2/unrelated.bin").write_bytes(b"not a pinned artifact")
    build = subprocess.run(
        ["uv", "build", "--out-dir", str(output)],
        cwd=work_repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    wheel = next(output.glob("*.whl"))
    sdist = next(output.glob("*.tar.gz"))
    manifest = json.loads((work_repo / "src/basicly/lefthook_assets/manifest.json").read_text())
    with zipfile.ZipFile(wheel) as archive:
        assert {
            name.removeprefix("basicly/catalog/"): archive.read(name)
            for name in archive.namelist()
            if name.startswith("basicly/catalog/") and not name.endswith("/")
        } == expected
        assert not any("catalog/vendor/lefthook" in name for name in archive.namelist())
        assert not any(name.endswith("unrelated.bin") for name in archive.namelist())
        assert not any(
            name.startswith("basicly/lefthook_assets/windows/") for name in archive.namelist()
        )
        for entry in manifest["assets"]:
            data = archive.read(f"basicly/lefthook_assets/{manifest['version']}/{entry['name']}")
            assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    extracted = tmp_path / "source"
    with tarfile.open(sdist) as archive:
        assert {
            member.name.partition("/")[2] for member in archive.getmembers() if member.isfile()
        } == expected_source | {"PKG-INFO"}
        assert not any(name.endswith("unrelated.bin") for name in archive.getnames())
        assert not any("/.basicly/usage/" in name for name in archive.getnames())
        archive.extractall(extracted, filter="data")
    source = next(extracted.iterdir())
    rebuild = subprocess.run(
        ["uv", "build", "--wheel", "--offline", "--out-dir", str(tmp_path / "rebuilt")],
        cwd=source,
        env={**os.environ, "UV_OFFLINE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert rebuild.returncode == 0, rebuild.stdout + rebuild.stderr
    with zipfile.ZipFile(next((tmp_path / "rebuilt").glob("*.whl"))) as archive:
        assert {
            name.removeprefix("basicly/catalog/"): archive.read(name)
            for name in archive.namelist()
            if name.startswith("basicly/catalog/") and not name.endswith("/")
        } == expected
        for entry in manifest["assets"]:
            data = archive.read(f"basicly/lefthook_assets/{manifest['version']}/{entry['name']}")
            assert hashlib.sha256(data).hexdigest() == entry["sha256"]


def test_unprepared_source_build_refuses_without_implicit_download(
    work_repo: Path, tmp_path: Path
) -> None:
    prepared = work_repo / "build/lefthook"
    prepared.rename(work_repo / "build/previously-prepared")
    result = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path / "dist")],
        cwd=work_repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "Missing Lefthook artifact" in result.stderr
    assert "--from-dir" in result.stderr


@pytest.mark.parametrize("empty", [False, True])
def test_missing_catalog_refuses_instead_of_building_an_unusable_wheel(
    work_repo: Path, tmp_path: Path, empty: bool
) -> None:
    core = work_repo / ".basicly/core"
    core.rename(work_repo / ".basicly/previous-core")
    if empty:
        core.mkdir()
    result = subprocess.run(
        ["uv", "build", "--wheel", "--offline", "--out-dir", str(tmp_path / "dist")],
        cwd=work_repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "Missing harness catalog resources" in result.stderr
    assert "complete source distribution" in result.stderr
