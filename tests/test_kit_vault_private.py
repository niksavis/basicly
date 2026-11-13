from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_kit_vault_shapes import Vault

KIT = Path(__file__).resolve().parents[1] / ".basicly" / "core" / "kit" / "tracker"
SPEC = importlib.util.spec_from_file_location(
    "basicly_tracker_kit_vault_private", KIT / "vault_private.py"
)
assert SPEC and SPEC.loader
private = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = private
SPEC.loader.exec_module(private)


def _clone(vault: Vault, tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(url=vault.root.as_uri(), bare=tmp_path / "cache" / "clone")


def test_private_map_outside_git_is_saved_with_owner_permissions(tmp_path: Path) -> None:
    vault = Vault(tmp_path / "vault")
    target = tmp_path / "private-map.json"
    destination = private.prepare(target, _clone(vault, tmp_path))
    private.write(destination, [{"old": "old-event", "new": "new-event", "fields": ["actor"]}])
    assert json.loads(target.read_text())["events"][0]["new"] == "new-event"
    assert str(target) not in repr(destination)
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_private_map_requires_an_ignored_untracked_path_in_another_checkout(tmp_path: Path) -> None:
    vault = Vault(tmp_path / "vault")
    other = Vault(tmp_path / "other")
    other.write(".gitignore", "private/\ntracked.json\n")
    other.write("tracked.json", "original\n")
    other.git("add", "-f", "tracked.json")
    folder = other.root / "private"
    folder.mkdir()
    destination = private.prepare(folder / "map.json", _clone(vault, tmp_path))
    private.write(destination, [])
    assert (folder / "map.json").is_file()
    for path in (other.root / "public.json", other.root / "tracked.json"):
        with pytest.raises(private.PrivateMapError, match="not ignored"):
            private.prepare(path, _clone(vault, tmp_path), overwrite=True)
    assert (other.root / "tracked.json").read_text() == "original\n"


def test_private_map_refuses_vault_checkout_and_linked_worktree_even_when_ignored(
    tmp_path: Path,
) -> None:
    vault = Vault(tmp_path / "vault")
    vault.write(".gitignore", "map.json\n")
    vault.commit("ignore fixture")
    linked = tmp_path / "linked"
    vault.git("worktree", "add", "-q", "-b", "fixture-lane", str(linked))
    for folder in (vault.root, linked, vault.root / ".git"):
        with pytest.raises(private.PrivateMapError, match="vault repository"):
            private.prepare(folder / "map.json", _clone(vault, tmp_path))
        assert not (folder / "map.json").exists()


def test_private_map_resolves_symlinks_before_repository_checks(tmp_path: Path) -> None:
    vault = Vault(tmp_path / "vault")
    other = Vault(tmp_path / "other")
    link = tmp_path / "alias"
    try:
        link.symlink_to(other.root, target_is_directory=True)
    except OSError:
        pytest.skip("this platform does not permit directory symlinks")
    with pytest.raises(private.PrivateMapError, match="not ignored"):
        private.prepare(link / "map.json", _clone(vault, tmp_path))
    link.unlink()
    link.symlink_to(vault.root, target_is_directory=True)
    with pytest.raises(private.PrivateMapError, match="vault repository"):
        private.prepare(link / "map.json", _clone(vault, tmp_path))


def test_private_map_never_overwrites_without_explicit_permission(tmp_path: Path) -> None:
    vault = Vault(tmp_path / "vault")
    clone = _clone(vault, tmp_path)
    target = tmp_path / "map.json"
    destination = private.prepare(target, clone)
    target.write_text("keep me")
    with pytest.raises(private.PrivateMapError, match="already exists"):
        private.write(destination, [])
    assert target.read_text() == "keep me"
    private.write(private.prepare(target, clone, overwrite=True), [{"new": "replacement"}])
    assert json.loads(target.read_text())["events"] == [{"new": "replacement"}]


def test_private_map_rechecks_repository_admission_before_writing(tmp_path: Path) -> None:
    vault = Vault(tmp_path / "vault")
    folder = tmp_path / "later-checkout"
    folder.mkdir()
    destination = private.prepare(folder / "map.json", _clone(vault, tmp_path))
    private.git_runner.run(["init", "-q"], folder)
    with pytest.raises(private.PrivateMapError, match="not ignored"):
        private.write(destination, [])
    assert not (folder / "map.json").exists()


def test_private_map_recognizes_an_encoded_vault_url_without_new_pathlib_methods(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(Path, "from_uri", raising=False)
    vault = Vault(tmp_path / "vault with space-é")
    vault.write(".gitignore", "map.json\n")
    vault.commit("ignored map fixture")
    assert "%20" in vault.root.as_uri()
    assert "%C3%A9" in vault.root.as_uri()
    clone = _clone(vault, tmp_path)
    with pytest.raises(private.PrivateMapError, match="vault repository"):
        private.prepare(vault.root / "map.json", clone)
    destination = private.prepare(tmp_path / "outside.json", clone)
    private.write(destination, [])
    assert json.loads((tmp_path / "outside.json").read_text())["events"] == []
