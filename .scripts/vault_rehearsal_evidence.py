from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

KIT = Path(".basicly/core/kit")
type Runner = Callable[[list[str], Path, dict[str, str], Path], str]


def files_digest(root: Path, paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        if path.is_symlink():
            raise ValueError("a rehearsal source contains a symlink")
        if path.is_file() and "__pycache__" not in path.parts:
            if any(part.startswith(".env") for part in path.relative_to(root).parts):
                continue
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def provenance(root: Path, helper: Path) -> dict[str, str]:
    if not (root / KIT / "tracker/cli.py").is_file():
        raise ValueError("the rehearsal kit is missing its CLI")
    return {
        "kit_sha256": files_digest(root, (root / KIT).rglob("*")),
        "helper_sha256": files_digest(helper.parent, [helper, Path(__file__)]),
    }


def require_source(out: Path, source: dict[str, str]) -> dict[str, Any]:
    report = json.loads((out / "part1.json").read_text())
    if report.get("provenance") != source:
        raise ValueError("the rehearsal source changed; start with a new output directory")
    snapshot = out / "workspace-source"
    if not snapshot.is_dir() or report.get("source_sha256") != files_digest(
        snapshot, snapshot.rglob("*")
    ):
        raise ValueError("the copied ledger changed; start with a new output directory")
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


def freeze_consumer(
    repository: Path,
    prefix: str,
    out: Path,
    root: Path,
    run: Callable[[list[str], Path], str],
) -> dict[str, object]:
    ledger = repository / ".basicly/ledger"
    original = [path.relative_to(ledger) for path in ledger.rglob("*") if path.is_file()]
    if any(part.startswith(".env") for path in original for part in path.parts):
        raise ValueError(
            "the scratch ledger contains protected environment files; use a clean fixture"
        )
    before = files_digest(ledger, (ledger / path for path in original))
    result = json.loads(
        run(
            [
                sys.executable,
                "-I",
                str(root / KIT / "tracker/cli.py"),
                "migrate",
                ".basicly/ledger",
                "--to-vault",
                "--freeze",
                "--prefix",
                prefix,
                "--vault",
                str(out / "vault-0.git"),
                "--state",
                str(out / "freeze-state" / prefix),
            ],
            repository,
        )
    )
    archive = ledger.with_name("ledger-archive")
    if result.get("frozen") is not True or not ledger.is_file() or not archive.is_dir():
        raise ValueError("the consumer freeze did not install its file guard and archive")
    if before != files_digest(archive, (archive / path for path in original)):
        raise ValueError("the consumer freeze changed original ledger bytes")
    current = {path.relative_to(archive) for path in archive.rglob("*") if path.is_file()}
    if current - set(original) - {Path(".events.lock")}:
        raise ValueError("the consumer freeze added unexpected archive files")
    return {"prefix": prefix, "frozen": True, "original_files_unchanged": True}


def control(
    args: argparse.Namespace, module: Any, root: Path, env: dict[str, str], run: Runner
) -> dict[str, Any]:
    out = args.out.resolve()
    if args.control_repository is None:
        raise ValueError("control needs --control-repository")
    target = out / "control-repository"
    shutil.copytree(
        args.control_repository.resolve(), target, ignore=shutil.ignore_patterns(".env*")
    )
    marker = (target / ".tracker-consumer-proof.json").read_bytes()
    before = {
        p.name: p.read_bytes() for p in module.events.ledger_paths(target / ".basicly/ledger")
    }
    clone = module.vault_clone.locate(out / "control-state", str(out / "vault-0.git"))
    imported = module.ledger_to_vault(
        target / ".basicly/ledger",
        module.Import(clone, lambda: 1000.0, True, module.vault_personal.email_scan),
    )
    after = {p.name: p.read_bytes() for p in module.events.ledger_paths(target / ".basicly/ledger")}
    if before != after or marker != (target / ".tracker-consumer-proof.json").read_bytes():
        raise ValueError("the prepared control source changed")
    source = module.ledger_events(target / ".basicly/ledger")

    def invoke(argv: list[str], cwd: Path) -> str:
        return run(argv, cwd, env, out)

    frozen = []
    for repository, prefix in ((target, "proof"), (out / "workspace-consumer", "dev")):
        frozen.append(freeze_consumer(repository, prefix, out, root, invoke))
    if marker != (target / ".tracker-consumer-proof.json").read_bytes():
        raise ValueError("the prepared control marker changed during freezing")
    return {
        "result": "pass",
        "records": len({event.record for event in source}),
        "source_events": len(source),
        "imported_events": imported["events"],
        "source_bytes_unchanged": True,
        "frozen_consumers": frozen,
    }
