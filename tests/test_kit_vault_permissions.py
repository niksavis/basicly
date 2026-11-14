from __future__ import annotations

import importlib.util
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.vault_permissions_consumer import KIT, access, fetch_private_state, powershell


def load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location("permissions_test_" + name, KIT / (name + ".py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(os.name != "nt", reason="This consumer checks native Windows access lists.")
@pytest.mark.parametrize("hook,existing", [(False, False), (True, False), (False, True)])
def test_fetch_creates_private_state_before_writing_its_clone(
    tmp_path: Path, hook: bool, existing: bool
) -> None:
    report = fetch_private_state(tmp_path, hook=hook, existing=existing)
    assert report["fetch_succeeded"]
    assert report["only_current_user"]


@pytest.mark.skipif(os.name != "nt", reason="This consumer checks native Windows reparse points.")
def test_a_junction_refuses_without_changing_its_target(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "retained.txt").write_bytes(b"Keep the target.\n")
    powershell(
        "$ErrorActionPreference = 'Stop'; "
        "$root = $env:BASICLY_ACL_PROBE_PATH; "
        "New-Item -ItemType Junction -Path (Join-Path $root 'link') "
        "-Value (Join-Path $root 'target') | Out-Null",
        tmp_path,
    )
    before = access(target)
    with pytest.raises(OSError, match="reparse point"):
        load("windows_permissions").private(tmp_path / "link")
    assert access(target) == before
    assert (target / "retained.txt").read_bytes() == b"Keep the target.\n"


@pytest.mark.skipif(os.name != "nt", reason="This consumer checks native Windows access lists.")
def test_a_file_refuses_without_changing_its_permissions_or_bytes(tmp_path: Path) -> None:
    path = tmp_path / "file"
    path.write_bytes(b"Keep this file.\n")
    before = access(path)
    with pytest.raises(OSError, match="must be a directory"):
        load("windows_permissions").private(path)
    assert access(path) == before
    assert path.read_bytes() == b"Keep this file.\n"


@pytest.mark.skipif(os.name != "nt", reason="This consumer checks native Windows access lists.")
def test_concurrent_state_hardening_keeps_the_current_users_access(tmp_path: Path) -> None:
    permissions = load("windows_permissions")
    state = tmp_path / "concurrent"
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: permissions.private(state), range(8)))
    (state / "private.txt").write_bytes(b"The current user can still write.\n")
    assert access(state)["only_current_user"]


def test_permission_failure_refuses_fetch_before_git_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone_module = load("vault_clone")

    def refuse(_path: Path) -> None:
        raise OSError("The security API refused.")

    def unexpected_git(*_args: Any, **_kwargs: Any) -> str:
        pytest.fail("Fetch started Git before securing the state directory.")

    monkeypatch.setattr(clone_module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(clone_module, "_load", lambda *_args: SimpleNamespace(private=refuse))
    clone = clone_module.Clone(
        "https://example.invalid/vault.git", tmp_path / "state/vaults/one", False
    )
    with pytest.raises(clone_module.CloneError, match="BASICLY_TRACKER_STATE"):
        clone_module.fetch(clone, runner=unexpected_git, clock=time.time)
    assert not (tmp_path / "state").exists()
