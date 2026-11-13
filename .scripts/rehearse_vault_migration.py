from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess  # nosec B404 - isolated consumer proof executes real local tools
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
KIT = Path(".basicly/core/kit")


def load() -> Any:
    name = "basicly_tracker_kit_vault_migrate"
    spec = importlib.util.spec_from_file_location(name, ROOT / KIT / "tracker/vault_migrate.py")
    if spec is None or spec.loader is None:
        raise ValueError("the migration kit is missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run(argv: list[str], cwd: Path, env: dict[str, str], output: Path) -> str:
    if argv[0] not in {"git", "uv", sys.executable}:
        raise ValueError("the rehearsal only executes git, uv and its Python interpreter")
    executable = shutil.which(argv[0])
    if executable is None:
        raise ValueError(f"the required tool is missing: {argv[0]}")
    result = subprocess.run(  # nosec B603 - fixed tools and separate argument values never use a shell
        [executable, *argv[1:]], cwd=cwd, env=env, capture_output=True, text=True, check=False
    )
    if result.returncode:
        failure = {
            "argv": argv,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        (output / "failure.json").write_text(json.dumps(failure, indent=2))
        raise ValueError("a rehearsal command failed; see failure.json in the output folder")
    return result.stdout


def copy_ledger(module: Any, source: Path, target: Path) -> None:
    target.mkdir(parents=True)
    for path in module.events.ledger_paths(source):
        shutil.copyfile(path, target / path.name)
    if (source / "template.json").is_file():
        shutil.copyfile(source / "template.json", target / "template.json")


def compare(module: Any, source: Path, target: Path) -> dict[str, int]:
    before = module.ledger_events(source)
    after = module.ledger_events(target)
    original = module.events.fold(before).records
    migrated = module.events.fold(after).records
    if set(original) != set(migrated):
        raise ValueError("record identities differ")
    reviews = confirmations = closed = 0
    for record, state in original.items():
        if state.status != migrated[record].status:
            raise ValueError("record status differs")
        left = module.canonical.process.confirmation_report(
            before, record, template=module.templates.load(source)
        )
        right = module.canonical.process.confirmation_report(
            after, record, template=module.templates.load(target)
        )
        for key in ("review_current", "confirmation_current"):
            if left[key] != right[key]:
                raise ValueError("process evidence differs")
        if left["review_current"] and left["owed"] != right["owed"]:
            raise ValueError("review readiness differs")
        reviews += bool(left["review_current"])
        confirmations += bool(left["confirmation_current"])
        closed += state.status == "closed"
    verdict = module.canonical.fsck.check(target)
    if verdict.exit_code:
        raise ValueError("canonical integrity check failed")
    return {
        "records": len(original),
        "events": len(after),
        "current_reviews": reviews,
        "current_confirmations": confirmations,
        "closed_records": closed,
        "fsck": 0,
    }


def beads(module: Any, repository: Path, source: Path) -> dict[str, int]:
    path = repository / ".beads/issues.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    ids = {row["id"] for row in rows}
    records = {event.record for event in module.ledger_events(source)}
    missing = ids - records
    if missing:
        raise ValueError(
            "old beads records are missing from the ledger; stop for a source decision"
        )
    if not ids or not records or len(ids) != len(rows):
        raise ValueError("the beads comparison lacks a nonempty unique-id control")
    return {
        "rows": len(rows),
        "unique_ids": len(ids),
        "already_imported": len(ids & records),
        "missing": len(missing),
    }


def human_count(module: Any, source: Path, repository: Path, env: dict[str, str], out: Path) -> int:
    name = run(["git", "config", "user.name"], repository, env, out).strip()
    if not name:
        raise ValueError("the source human identity is unknown")
    return sum(
        (event.kind == "created" and event.payload.get(module.events.HOLDER_FIELD) == name)
        or (
            event.kind == "field"
            and event.payload.get("name") == module.events.HOLDER_FIELD
            and event.payload.get("value") == name
        )
        for event in module.ledger_events(source)
    )


def destination(args: argparse.Namespace) -> Path:
    out = args.out.resolve()
    git = shutil.which("git")
    if git is None:
        raise ValueError("git is required for the rehearsal destination check")
    ignored = subprocess.run(  # nosec B603 - resolved git with a separated absolute path and no shell
        [git, "check-ignore", "-q", "--", str(out)],
        cwd=args.source_repo.resolve(),
        capture_output=True,
        check=False,
    )
    if ignored.returncode:
        raise ValueError("--out must be an ignored path in the source repository")
    alias = os.environ.get("BASICLY_REHEARSAL_HUMAN_ALIAS", "")
    if not alias.strip() or not alias.isprintable():
        raise ValueError("set BASICLY_REHEARSAL_HUMAN_ALIAS to the private human holder alias")
    return out


def part1(args: argparse.Namespace, module: Any, env: dict[str, str]) -> dict[str, Any]:
    out = destination(args)
    out.mkdir(parents=True, exist_ok=False)
    source = out / "workspace-source"
    copy_ledger(module, args.source_repo.resolve() / ".basicly/ledger", source)
    found = module.ledger_events(source)
    report: dict[str, Any] = {
        "result": "pending",
        "source_records": len({e.record for e in found}),
        "source_events": len(found),
        "map": "not-kept",
    }
    report["beads"] = beads(module, args.source_repo.resolve(), source)
    report["human_holder_events"] = human_count(
        module, source, args.source_repo.resolve(), env, out
    )
    genesis = out / "genesis"
    genesis.mkdir()
    run(["git", "init", "-q", "-b", "main"], genesis, env, out)
    initializer = module._load("vault_init.py", "basicly_tracker_kit_vault_init")
    for filename, body in initializer.genesis_files().items():
        (genesis / filename).write_text(body)
    run(["git", "add", "-A"], genesis, env, out)
    run(["git", "commit", "-q", "-m", "fixture genesis"], genesis, env, out)
    canonical = []
    results = []
    for index in range(2):
        bare = out / f"vault-{index}.git"
        run(["git", "clone", "-q", "--bare", str(genesis), str(bare)], out, env, out)
        clone = module.vault_clone.locate(out / f"state-{index}", str(bare))
        imported = module.ledger_to_vault(
            source, module.Import(clone, lambda i=index: 1000.0 + i, True)
        )
        if imported["mapping"] != "not-kept":
            raise ValueError("a private mapping was kept")
        view = module.vault_view.present(clone, module.vault_view.Want("dev"), clock=lambda: 1000.0)
        viewed = compare(module, source, view)
        target = out / f"export-{index}"
        module.from_vault(clone, "dev", target, clock=lambda: 1000.0)
        exported = compare(module, source, target)
        valid = module.vault_clone.valid_events(clone)
        carried = module.vault_view.carried(valid, module.EVERY_PROJECT)
        if len(carried) != len(found):
            raise ValueError("source event accounting differs")
        canonical.append(json.dumps(valid, sort_keys=True, ensure_ascii=False))
        results.append({
            "view": viewed,
            "export": exported,
            "carried_events": len(carried),
            "reminted": imported["reminted"],
            "digest": imported["digest"],
        })
    if canonical[0] != canonical[1]:
        raise ValueError("independent canonical bytes differ")
    report.update(runs=results, canonical_bytes_equal=True)
    report["standalone"] = standalone(module, source, env, out)
    report["template_proof"] = (
        run(
            [
                "uv",
                "run",
                "pytest",
                "tests/test_kit_vault_template.py",
                "-q",
                "--basetemp",
                str(out / "template-proof"),
            ],
            ROOT,
            env,
            out,
        )
        .strip()
        .splitlines()[-1]
    )
    report["human_fixture"] = human_fixture(module, out)
    report.update(result="pass", part2="pending", scratch_vault="vault-0.git")
    (out / "part1.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def human_fixture(module: Any, out: Path) -> dict[str, object]:
    alias = os.environ.get("BASICLY_REHEARSAL_HUMAN_ALIAS", "")
    if not alias.strip() or not alias.isprintable():
        raise ValueError("set BASICLY_REHEARSAL_HUMAN_ALIAS to the private human holder alias")
    fixture = out / "human-source"
    module.events.append(
        fixture,
        [
            module.events.Draft(
                "humanproof-a1",
                "created",
                {
                    "title": "Scratch human holder control",
                    "issue_type": "task",
                    "description": "## Context\nCheck a human holder in a local migration.",
                    module.events.HOLDER_FIELD: alias,
                },
            )
        ],
        actor=alias,
        clock=lambda: 1000.0,
    )
    clone = module.vault_clone.locate(out / "human-state", str(out / "vault-0.git"))
    module.ledger_to_vault(fixture, module.Import(clone, lambda: 1000.0, True))
    human = module.vault_view.carried(
        module.vault_clone.valid_events(clone), module.vault_view.Want("humanproof")
    )
    if not any(event.payload.get(module.events.HOLDER_FIELD) == alias for event in human):
        raise ValueError("the scratch human holder history differs from its alias")
    return {"source_holder_events": 1, "historical_holder_matches_alias": True}


def standalone(module: Any, source: Path, env: dict[str, str], out: Path) -> dict[str, int]:
    consumer = out / "workspace-consumer"
    shutil.copytree(ROOT / KIT, consumer / KIT, ignore=shutil.ignore_patterns(".env*"))
    shutil.copytree(source, consumer / ".basicly/ledger")
    cli = [sys.executable, "-I", str(KIT / "tracker/cli.py")]
    bare = out / "standalone.git"
    run(["git", "clone", "-q", "--bare", str(out / "genesis"), str(bare)], out, env, out)
    base = ["--vault", str(bare), "--state", str(out / "standalone-state")]
    preview = json.loads(
        run([*cli, "migrate", ".basicly/ledger", "--to-vault", "--preview"], consumer, env, out)
    )
    imported = json.loads(
        run(
            [*cli, "migrate", ".basicly/ledger", "--to-vault", *base, "--no-map-kept"],
            consumer,
            env,
            out,
        )
    )
    if preview["digest"] != imported["digest"]:
        raise ValueError("standalone preview and import digests differ")
    run(
        [*cli, "migrate", str(out / "standalone-export"), "--from-vault", *base, "--prefix", "dev"],
        consumer,
        env,
        out,
    )
    return compare(module, source, out / "standalone-export")


def control(args: argparse.Namespace, module: Any) -> dict[str, Any]:
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
        target / ".basicly/ledger", module.Import(clone, lambda: 1000.0, True)
    )
    after = {p.name: p.read_bytes() for p in module.events.ledger_paths(target / ".basicly/ledger")}
    if before != after or marker != (target / ".tracker-consumer-proof.json").read_bytes():
        raise ValueError("the prepared control source changed")
    for repository, prefix in ((target, "proof"), (out / "workspace-consumer", "dev")):
        (repository / ".basicly-tracker.toml").write_text(
            f'vault = "{out / "vault-0.git"}"\nprefix = "{prefix}"\n'
        )
    source = module.ledger_events(target / ".basicly/ledger")
    report = {
        "result": "pass",
        "records": len({event.record for event in source}),
        "source_events": len(source),
        "imported_events": imported["events"],
        "source_bytes_unchanged": True,
    }
    (out / "control.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def complete(args: argparse.Namespace) -> dict[str, Any]:
    names = ("part1", "control", "lifecycle", "ledger-compat")
    reports = {name: json.loads((args.out / f"{name}.json").read_text()) for name in names}
    if any(report.get("result") != "pass" for report in reports.values()):
        raise ValueError("a rehearsal part is not passing")
    return {"result": "pass", "parts": list(reports)}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rehearse migration using retained local scratch vaults."
    )
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--part", choices=["part1", "control", "complete"], default="part1")
    parser.add_argument("--control-repository", type=Path)
    args = parser.parse_args()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    }
    try:
        report = (
            complete(args)
            if args.part == "complete"
            else (part1(args, load(), env) if args.part == "part1" else control(args, load()))
        )
    except (ValueError, OSError) as error:
        print(f"rehearsal stopped: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
