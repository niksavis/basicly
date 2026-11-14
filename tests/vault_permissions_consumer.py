from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / ".basicly/core/kit/tracker"
QUERY = """
$ErrorActionPreference = 'Stop'
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$path = $env:BASICLY_ACL_PROBE_PATH
$root = Get-Item -LiteralPath $path
$items = @($root) + @(Get-ChildItem -LiteralPath $path -Force -Recurse)
$bad = @($items | Where-Object {
    $acl = Get-Acl -LiteralPath $_.FullName
    $rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
    $other = @($rules | Where-Object { $_.IdentityReference.Value -ne $sid })
    $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    $rules.Count -ne 1 -or $other.Count -ne 0 -or $owner -ne $sid
})
$rootAcl = Get-Acl -LiteralPath $path
$hash = [Security.Cryptography.SHA256]::Create()
$digest = $hash.ComputeHash($rootAcl.GetSecurityDescriptorBinaryForm())
@{
    only_current_user = $bad.Count -eq 0
    examined = $items.Count
    protected = $rootAcl.AreAccessRulesProtected
    acl_digest = [Convert]::ToBase64String($digest)
    owner_current_user = $rootAcl.GetOwner([Security.Principal.SecurityIdentifier]).Value -eq $sid
} | ConvertTo-Json -Compress
"""
PERMISSIVE = """
$ErrorActionPreference = 'Stop'
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$acl = New-Object Security.AccessControl.DirectorySecurity
$acl.SetOwner($sid)
$acl.SetAccessRuleProtection($true, $false)
$inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
$propagate = [Security.AccessControl.PropagationFlags]::None
$allow = [Security.AccessControl.AccessControlType]::Allow
$own = [Security.AccessControl.FileSystemAccessRule]::new(
    $sid, 'FullControl', $inherit, $propagate, $allow)
$everyone = New-Object Security.Principal.SecurityIdentifier('S-1-1-0')
$read = [Security.AccessControl.FileSystemAccessRule]::new(
    $everyone, 'ReadAndExecute', $inherit, $propagate, $allow)
$acl.AddAccessRule($own)
$acl.AddAccessRule($read)
Set-Acl -LiteralPath $env:BASICLY_ACL_PROBE_PATH -AclObject $acl
"""
PERMISSIVE_FILE = """
$ErrorActionPreference = 'Stop'
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$acl = New-Object Security.AccessControl.FileSecurity
$acl.SetOwner($sid)
$acl.SetAccessRuleProtection($true, $false)
$allow = [Security.AccessControl.AccessControlType]::Allow
$own = [Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', $allow)
$everyone = New-Object Security.Principal.SecurityIdentifier('S-1-1-0')
$read = [Security.AccessControl.FileSystemAccessRule]::new($everyone, 'Read', $allow)
$acl.AddAccessRule($own)
$acl.AddAccessRule($read)
Set-Acl -LiteralPath $env:BASICLY_ACL_PROBE_PATH -AclObject $acl
"""


def powershell(script: str, path: Path) -> str:
    executable = shutil.which("powershell") or shutil.which("pwsh")
    if executable is None:
        raise ValueError("The Windows ACL consumer needs PowerShell.")
    done = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, "BASICLY_ACL_PROBE_PATH": str(path)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    if done.returncode:
        raise ValueError("The independent Windows ACL query failed.")
    return done.stdout


def access(path: Path) -> dict:
    return json.loads(powershell(QUERY, path))


def _snapshot(state: Path) -> dict[Path, str]:
    return {
        path.relative_to(state): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in state.rglob("*")
        if path.is_file() and not any(part.startswith(".env") for part in path.parts)
    }


def _refuse_unchanged(
    run: Callable[..., subprocess.CompletedProcess[str]],
    fetch: list[str],
    state: Path,
    original: Path,
) -> dict:
    before_acl, before_file = access(state), access(original)
    before_bytes = _snapshot(state)
    refused = json.loads(run(fetch, expected=1).stdout)
    assert "BASICLY_TRACKER_STATE" in refused["refused"], refused
    assert "preserve" in refused["refused"], refused
    assert access(state) == before_acl
    assert access(original) == before_file
    assert _snapshot(state) == before_bytes
    return {"unsafe_state_refused": True, "original_bytes_and_acls_unchanged": True}


def _hook_clone(
    run: Callable[..., subprocess.CompletedProcess[str]],
    cli: list[str],
    bare: Path,
    state: Path,
    message: Path,
) -> Path:
    message.write_text("feat: missing record identity\n", encoding="utf-8")
    checked = json.loads(
        run(
            [*cli, "commit-msg", str(message), "--vault", str(bare), "--state", str(state)],
            expected=1,
        ).stdout
    )
    assert checked["reached"] and "no record id" in checked["refused"], checked
    clones = list(state.glob("vaults/*/clone"))
    assert len(clones) == 1
    return clones[0]


def fetch_private_state(root: Path, *, scenario: str = "fresh") -> dict:
    if scenario not in {"fresh", "hook", "existing", "explicit-child", "broad-root"}:
        raise ValueError("Unknown Windows state consumer scenario.")
    env = os.environ.copy()
    for name in ("CI", "BR_AGENT_NAME", "AI_AGENT", "CLAUDECODE"):
        env.pop(name, None)
    env.update(
        BASICLY_TRACKER_EPHEMERAL="1",
        BASICLY_TRACKER_SESSION="acl-consumer",
        BASICLY_HOLDER="acl-consumer",
        HOME=str(root / "home"),
        USERPROFILE=str(root / "home"),
    )

    def run(argv: list[str], *, expected: int = 0) -> subprocess.CompletedProcess[str]:
        done = subprocess.run(
            argv, cwd=root, env=env, capture_output=True, text=True, check=False, timeout=60
        )
        if done.returncode != expected:
            raise ValueError(f"The vault consumer command refused: {done.stdout} {done.stderr}")
        return done

    run(["git", "init", "-q", "-b", "main"])
    bare = root / "vault.git"
    run(["git", "init", "--bare", "-q", "-b", "main", str(bare)])
    cli = [sys.executable, "-I", "-S", str(KIT / "cli.py")]
    run([*cli, "vault-init", "--vault", str(bare), "--prefix", "demo", "--yes"])
    parent = root / "permissive"
    parent.mkdir()
    powershell(PERMISSIVE, parent)
    before = access(parent)
    assert before["examined"] == 1 and not before["only_current_user"], before
    state = parent / "state"
    original = state / "retained.txt"
    fetch = [*cli, "fetch", "--vault", str(bare), "--state", str(state)]
    if scenario in {"existing", "explicit-child", "broad-root"}:
        if scenario == "broad-root":
            state.mkdir()
        else:
            run(fetch)
        original.write_bytes(b"Existing private history.\n")
        if scenario == "explicit-child":
            powershell(PERMISSIVE_FILE, original)
            assert not access(original)["only_current_user"]
    if scenario in {"explicit-child", "broad-root"}:
        return _refuse_unchanged(run, fetch, state, original)
    if scenario == "hook":
        clone = _hook_clone(run, cli, bare, state, root / "COMMIT_EDITMSG")
    else:
        fetched = json.loads(
            run([*cli, "fetch", "--vault", str(bare), "--state", str(state)]).stdout
        )
        assert fetched["fetched"] and fetched["head"], fetched
        clone = Path(fetched["clone"])
    assert (clone / "HEAD").is_file()
    genesis = run(["git", "--git-dir", str(clone), "show", "refs/remotes/origin/main:vault.toml"])
    assert 'kind = "vault"' in genesis.stdout
    private_file = clone.parent / "private.txt"
    private_file.write_text("Private tracker state.\n", encoding="utf-8")
    observed = access(state)
    assert observed["examined"] > 10, observed
    assert observed["protected"], observed
    assert observed["only_current_user"], observed
    assert private_file.read_text(encoding="utf-8") == "Private tracker state.\n"
    if scenario == "existing":
        assert original.read_bytes() == b"Existing private history.\n"
    return {
        **observed,
        "fetch_succeeded": True,
        "permissive_parent_control": True,
        "hook_refused_missing_id": scenario == "hook",
    }
