from __future__ import annotations

import ctypes
import importlib.util
import json
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
@pytest.mark.parametrize("scenario", ["fresh", "hook", "existing"])
def test_fetch_creates_private_state_before_writing_its_clone(
    tmp_path: Path, scenario: str
) -> None:
    report = fetch_private_state(tmp_path, scenario=scenario)
    assert report["fetch_succeeded"]
    assert report["only_current_user"]
    assert report["owner_current_user"]


@pytest.mark.skipif(os.name != "nt", reason="This consumer checks native Windows access lists.")
@pytest.mark.parametrize("scenario", ["explicit-child", "broad-root"])
def test_unsafe_existing_state_refuses_without_rewriting_it(tmp_path: Path, scenario: str) -> None:
    report = fetch_private_state(tmp_path, scenario=scenario)
    assert report["unsafe_state_refused"]
    assert report["original_bytes_and_acls_unchanged"]


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
def test_a_hardlink_refuses_without_changing_the_external_file(tmp_path: Path) -> None:
    permissions = load("windows_permissions")
    state = tmp_path / "state"
    permissions.private(state)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"Keep the external file.\n")
    os.link(outside, state / "linked.txt")
    before = access(outside)
    with pytest.raises(OSError, match="hardlinked"):
        permissions.private(state)
    assert access(outside) == before
    assert outside.read_bytes() == b"Keep the external file.\n"


@pytest.mark.skipif(os.name != "nt", reason="This consumer checks native Windows ownership.")
def test_another_owner_refuses_before_its_access_list_changes(tmp_path: Path) -> None:
    permissions = load("windows_permissions")
    state = tmp_path / "state"
    permissions.private(state)
    changed = json.loads(
        powershell(
            "$ErrorActionPreference = 'Stop'; "
            "$path = $env:BASICLY_ACL_PROBE_PATH; "
            "$acl = Get-Acl -LiteralPath $path; "
            "try { "
            "$acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')); "
            "Set-Acl -LiteralPath $path -AclObject $acl; "
            "@{supported=$true} | ConvertTo-Json -Compress "
            "} catch { @{supported=$false} | ConvertTo-Json -Compress }",
            state,
        )
    )
    if not changed["supported"]:
        pytest.skip("The current token cannot assign a distinct existing owner.")
    before = access(state)
    assert not before["owner_current_user"]
    with pytest.raises(OSError, match="owned by another principal"):
        permissions.private(state)
    assert access(state) == before


@pytest.mark.skipif(os.name != "nt", reason="This consumer checks native Windows owner readback.")
def test_a_foreign_owner_readback_refuses_before_protecting_the_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    permissions = load("windows_permissions")
    parent = tmp_path / "parent"
    permissions.private(parent)
    state = parent / "state"
    state.mkdir()
    before = access(state)
    assert not before["protected"]
    kernel, security = permissions._api()
    convert = security.ConvertStringSidToSidW
    convert.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)]
    convert.restype = ctypes.c_int
    foreign = ctypes.c_void_p()
    assert convert("S-1-1-0", ctypes.byref(foreign))
    observed: list[bool] = []

    class ForeignOwner:
        def __getattr__(self, name: str) -> Any:
            if name == "GetSecurityInfo":
                return self.get_security_info
            if name == "SetSecurityInfo":
                return self.set_security_info
            return getattr(security, name)

        def get_security_info(self, *args: Any) -> int:
            result = int(security.GetSecurityInfo(*args))
            if not result:
                ctypes.cast(args[3], ctypes.POINTER(ctypes.c_void_p))[0] = foreign.value
                observed.append(True)
            return result

        def set_security_info(self, *_args: Any) -> int:
            pytest.fail("The directory permissions changed before its owner was accepted.")

    monkeypatch.setattr(permissions, "_api", lambda: (kernel, ForeignOwner()))
    try:
        with pytest.raises(OSError, match="owned by another principal"):
            permissions.private(state)
    finally:
        kernel.LocalFree(foreign)
    assert observed
    assert access(state) == before


@pytest.mark.skipif(os.name != "nt", reason="This consumer checks native Windows inheritance.")
def test_an_owned_private_directory_gets_protection_without_losing_data(tmp_path: Path) -> None:
    permissions = load("windows_permissions")
    parent = tmp_path / "parent"
    permissions.private(parent)
    state = parent / "state"
    state.mkdir()
    (state / "retained.txt").write_bytes(b"Keep the inherited private file.\n")
    before = access(state)
    assert before["only_current_user"] and not before["protected"]
    permissions.private(state)
    after = access(state)
    assert after["only_current_user"] and after["protected"]
    assert (state / "retained.txt").read_bytes() == b"Keep the inherited private file.\n"


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
