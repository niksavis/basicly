from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import TYPE_CHECKING

from .projection import atomic_write_text

if TYPE_CHECKING:
    from .lefthook_config import HookSpec

CONFIG = Path(".codex/hooks.json")
EVENTS = {"pretooluse": "PreToolUse", "posttooluse": "PostToolUse", "sessionstart": "SessionStart"}


def windows_command(runtime: str, stage: str) -> str:
    relative = runtime.replace("'", "''")
    script = (
        "$ErrorActionPreference = 'Stop'; try { "
        "$root = (git rev-parse --show-toplevel); "
        "if ($LASTEXITCODE -ne 0) { throw 'Git root unavailable' }; "
        "$start = New-Object System.Diagnostics.ProcessStartInfo; "
        "$start.FileName = 'uv'; "
        "$start.UseShellExecute = $false; "
        "$start.Arguments = 'run --no-project --no-python-downloads "
        "--python 3.14 python \"' + "
        f"$root + '/{relative}\" codex {stage}'; "
        "$process = [System.Diagnostics.Process]::Start($start); "
        "$process.WaitForExit(); exit $process.ExitCode "
        "} catch { [Console]::Error.WriteLine('basicly hook launcher failed'); exit 2 }"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return f"powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand {encoded}"


def merge_hooks(settings: dict, specs: list[HookSpec], hooks_relpath: str) -> dict:
    output = dict(settings)
    section = dict(output.get("hooks") or {})
    runtime = f"{hooks_relpath}/lefthook-runtime.py"
    for event in EVENTS.values():
        kept = []
        for group in section.get(event, []):
            entries = [
                hook for hook in group.get("hooks", []) if runtime not in hook.get("command", "")
            ]
            if entries:
                kept.append({**group, "hooks": entries})
        if kept:
            section[event] = kept
        else:
            section.pop(event, None)
    for stage in dict.fromkeys(spec.stage for spec in specs):
        matchers = [spec.matcher for spec in specs if spec.stage == stage]
        matcher = "*" if any(value in {"", "*"} for value in matchers) else "|".join(matchers)
        script = f'"$(git rev-parse --show-toplevel)/{runtime}"'
        command = (
            f"uv run --no-project --no-python-downloads --python 3.14 python {script} codex {stage}"
        )
        section.setdefault(EVENTS[stage], []).append({
            "matcher": matcher,
            "hooks": [
                {
                    "type": "command",
                    "command": command,
                    "commandWindows": windows_command(runtime, stage),
                }
            ],
        })
    if section:
        output["hooks"] = section
    else:
        output.pop("hooks", None)
    return output


def sync_hooks(repo_root: Path, specs: list[HookSpec], hooks_relpath: str) -> bool:
    path = repo_root / CONFIG
    current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    updated = merge_hooks(current, specs, hooks_relpath)
    if updated == current:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(updated, indent=2) + "\n")
    return True


def mismatches(
    repo_root: Path, specs: list[HookSpec], hooks_relpath: str
) -> list[tuple[Path, str]]:
    path = repo_root / CONFIG
    current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return (
        [(path, "managed Codex hooks out of sync")]
        if merge_hooks(current, specs, hooks_relpath) != current
        else []
    )
