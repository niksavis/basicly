from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

KIT = Path(".basicly/core/kit")


def provenance(root: Path, helper: Path) -> dict[str, str]:
    paths = sorted((root / KIT).rglob("*"))
    digest = hashlib.sha256()
    for path in paths:
        if path.is_symlink():
            raise ValueError("the rehearsal kit contains a symlink")
        if path.is_file() and "__pycache__" not in path.parts:
            if any(part.startswith(".env") for part in path.relative_to(root).parts):
                continue
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    if not (root / KIT / "tracker/cli.py").is_file():
        raise ValueError("the rehearsal kit is missing its CLI")
    return {
        "kit_sha256": digest.hexdigest(),
        "helper_sha256": hashlib.sha256(
            helper.read_bytes() + Path(__file__).read_bytes()
        ).hexdigest(),
    }


def require_source(out: Path, source: dict[str, str]) -> dict[str, Any]:
    report = json.loads((out / "part1.json").read_text())
    if report.get("provenance") != source:
        raise ValueError("the rehearsal source changed; start with a new output directory")
    return report


def complete(
    args: argparse.Namespace,
    env: dict[str, str],
    root: Path,
    source: dict[str, str],
    run: Callable[[list[str], Path, dict[str, str], Path], str],
) -> dict[str, Any]:
    out = args.out.resolve()
    first = require_source(out, source)
    prepared = json.loads((out / "control.json").read_text())
    if prepared.get("provenance") != source:
        raise ValueError("the control source changed; start with a new output directory")
    if first.get("result") != "pass" or prepared.get("result") != "pass":
        raise ValueError("a rehearsal part is not passing")
    if not args.workspace_record:
        raise ValueError("complete needs --workspace-record from the copied development ledger")
    lifecycle = out / "lifecycle.json"
    compatibility = out / "ledger-compat.json"
    if lifecycle.exists() or compatibility.exists() or (out / "ledger-compat").exists():
        raise ValueError("consumer reports already exist; use a new complete rehearsal")
    run(
        [
            sys.executable,
            "-S",
            str(root / ".scripts/check_vault_lifecycle.py"),
            "check",
            "--kit",
            str(root / KIT / "tracker"),
            "--repository",
            str(out / "control-repository"),
            "--state",
            str(out / "lifecycle-state"),
            "--session",
            "consumer-proof",
            "--workspace-repository",
            str(out / "workspace-consumer"),
            "--workspace-record",
            args.workspace_record,
            "--out",
            str(lifecycle),
        ],
        root,
        env,
        out,
    )
    observed = run(
        [
            sys.executable,
            "-S",
            str(root / ".scripts/check_tracker_ledger_compat.py"),
            "--kit",
            str(root / KIT / "tracker"),
            "--out",
            str(out / "ledger-compat"),
        ],
        root,
        env,
        out,
    )
    compared = json.loads(observed)
    compatibility.write_text(json.dumps(compared, indent=2))
    checked = json.loads(lifecycle.read_text())
    if checked.get("result") != "pass" or compared.get("result") != "pass":
        raise ValueError("a consumer proof is not passing")
    return {
        "result": "pass",
        "provenance": source,
        "parts": ["part1", "control", "lifecycle", "ledger-compat"],
    }
