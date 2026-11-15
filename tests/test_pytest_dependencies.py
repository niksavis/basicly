from __future__ import annotations

import os
from pathlib import Path

import pytest

from basicly import pyright_inputs, pytest_dependencies


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    parent = tmp_path / "library"
    child = parent / "site-packages"
    child.mkdir(parents=True)
    (parent / "top.py").write_bytes(b"base=1\n")
    (child / "pkg.py").write_bytes(b"alpha=1\n")
    return parent, child


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("relative", [False, True])
def test_overlapping_roots_preserve_existing_fingerprint_format(
    roots: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reverse: bool,
    relative: bool,
) -> None:
    parent, child = roots
    if relative:
        monkeypatch.chdir(tmp_path)
        parent, child = parent.relative_to(tmp_path), child.relative_to(tmp_path)
    order = (child, parent) if reverse else (parent, child)
    assert pytest_dependencies.fingerprints(order) == {
        parent: "fd269b5e0f3df2aa2684c41933df248e631d1ad2c2c82ae9ae67d16204f67a91",
        child: "ef498360a0ea5bcba3c496347f9a4ff07f77043b445c7b3573cf1b3793eea8b4",
    }


def test_same_size_edit_with_restored_mtime_changes_every_covered_root(
    roots: tuple[Path, Path],
) -> None:
    parent, child = roots
    file = child / "pkg.py"
    state = file.stat()
    before = pytest_dependencies.fingerprints(roots)
    file.write_bytes(b"alpha=2\n")
    os.utime(file, ns=(state.st_atime_ns, state.st_mtime_ns))
    after = pytest_dependencies.fingerprints(roots)
    assert all(after[root] != before[root] for root in (parent, child))


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("relative", [False, True])
def test_a_parent_traversal_keeps_the_outside_root_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reverse: bool, relative: bool
) -> None:
    parent = tmp_path / "parent"
    outside = tmp_path / "outside"
    parent.mkdir()
    outside.mkdir()
    file = outside / "changing.py"
    file.write_bytes(b"before")
    if relative:
        monkeypatch.chdir(tmp_path)
        parent = parent.relative_to(tmp_path)
    alias = parent / ".." / "outside"
    order = (alias, parent) if reverse else (parent, alias)
    before = pytest_dependencies.fingerprints(order)
    assert before[alias] == pytest_dependencies.fingerprints((alias,))[alias]
    file.write_bytes(b"after!")
    after = pytest_dependencies.fingerprints(order)
    assert after[parent] == before[parent]
    assert after[alias] != before[alias]


@pytest.mark.parametrize("name", ["__pycache__", ".cache"])
def test_an_explicit_cache_root_is_fingerprinted_without_entering_the_parent_proof(
    roots: tuple[Path, Path], name: str
) -> None:
    parent, _ = roots
    cache = parent / name
    cache.mkdir()
    file = cache / "entry.py"
    file.write_bytes(b"first")
    before = pytest_dependencies.fingerprints((parent, cache))
    file.write_bytes(b"later")
    after = pytest_dependencies.fingerprints((parent, cache))
    assert after[parent] == before[parent]
    assert after[cache] != before[cache]


@pytest.mark.skipif(os.name != "nt", reason="Windows paths have case-insensitive aliases")
@pytest.mark.parametrize("reverse", [False, True])
def test_a_case_alias_of_an_explicit_cache_root_stays_visible(
    roots: tuple[Path, Path], reverse: bool
) -> None:
    parent, _ = roots
    cache = parent / ".cache"
    cache.mkdir()
    file = cache / "entry.py"
    file.write_bytes(b"before")
    alias = parent / ".CACHE"
    assert alias.is_dir()
    order = (alias, parent) if reverse else (parent, alias)
    before = pytest_dependencies.fingerprints(order)
    assert before[alias] == pytest_dependencies.fingerprints((alias,))[alias]
    file.write_bytes(b"after!")
    after = pytest_dependencies.fingerprints(order)
    assert after[parent] == before[parent]
    assert after[alias] != before[alias]


def test_an_import_path_must_pass_every_covered_root_boundary(
    roots: tuple[Path, Path],
) -> None:
    parent, child = roots
    (child / "outside.pth").write_text(str(parent), encoding="utf-8")
    with pytest.raises(pyright_inputs.UnsupportedInputsError, match="external pytest import path"):
        pytest_dependencies.fingerprints(roots)


def test_the_workspace_import_exception_applies_to_each_covered_root(
    roots: tuple[Path, Path], tmp_path: Path
) -> None:
    _, child = roots
    workspace = tmp_path / "workspace"
    (workspace / "src").mkdir(parents=True)
    entry = child / "source.pth"
    entry.write_text(str(workspace / "src"), encoding="utf-8")
    assert len(pytest_dependencies.fingerprints(roots, workspace)) == len(roots)
    entry.write_text(str(workspace / "other"), encoding="utf-8")
    with pytest.raises(pyright_inputs.UnsupportedInputsError, match="external pytest import path"):
        pytest_dependencies.fingerprints(roots, workspace)


@pytest.mark.skipif(os.name == "nt", reason="Windows symlinks require a separate privilege")
def test_external_file_changes_remain_visible_through_each_overlapping_root(
    roots: tuple[Path, Path], tmp_path: Path
) -> None:
    _, child = roots
    target = tmp_path / "external.py"
    target.write_bytes(b"before")
    (child / "external.py").symlink_to(target)
    before = pytest_dependencies.fingerprints(roots)
    target.write_bytes(b"after!")
    after = pytest_dependencies.fingerprints(roots)
    assert all(after[root] != before[root] for root in roots)


@pytest.mark.skipif(os.name == "nt", reason="Windows symlinks require a separate privilege")
def test_retargeting_a_shared_root_during_the_read_refuses_the_proof(
    roots: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent, _ = roots
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(parent, target_is_directory=True)
    original = Path.read_bytes
    changed = False

    def read(path: Path) -> bytes:
        nonlocal changed
        data = original(path)
        if path == alias / "site-packages/pkg.py":
            alias.unlink()
            alias.symlink_to(replacement, target_is_directory=True)
            changed = True
        return data

    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(pyright_inputs.UnsupportedInputsError, match="dependency root changed"):
        pytest_dependencies.fingerprints((alias, alias / "site-packages"))
    assert changed
