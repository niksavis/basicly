from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import platform
import shutil
from pathlib import Path
from typing import Any

from harness_trial_protocols import configuration_inputs
from harness_trial_results import summarize
from harness_trial_runner import execute_trial, schedule, validate_paths

from basicly import __version__, runner

ROOT = Path(__file__).resolve().parent.parent


def bind_case(case: dict[str, Any], parameters: dict[str, str]) -> dict[str, Any]:
    required = case.get("requires", [])
    if not isinstance(required, list) or any(
        name not in {"baseline_wheel", "candidate_wheel"} for name in required
    ):
        raise ValueError(f"invalid fixture requirements for {case['id']}")
    for name in required:
        if name not in parameters:
            raise ValueError(f"case {case['id']} requires --{name.replace('_', '-')}")

    def expand(value: str) -> str:
        for name, replacement in parameters.items():
            value = value.replace("{" + name + "}", replacement)
        return value

    return {
        **case,
        "prompt": expand(case["prompt"]),
        "setup": [[expand(arg) for arg in command] for command in case.get("setup", [])],
        "check": [expand(arg) for arg in case["check"]],
    }


def wheel_inputs(args: argparse.Namespace) -> tuple[dict[str, str], dict[str, str]]:
    parameters, digests = {}, {}
    for name in ("baseline_wheel", "candidate_wheel"):
        value = getattr(args, name, None)
        if value is None:
            continue
        path = Path(value).resolve()
        if any(part.casefold().startswith(".env") for part in (*Path(value).parts, *path.parts)):
            raise ValueError("protected wheel input")
        if path.suffix != ".whl" or not path.is_file():
            raise ValueError(f"--{name.replace('_', '-')} requires an existing wheel")
        parameters[name] = str(path)
        digests[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return parameters, digests


def runtime_manifest(spec: runner.RunnerSpec, configuration_files: list[Path]) -> dict[str, Any]:
    executable = shutil.which(spec.binary) if spec.binary else None
    return {
        "adapter_version": runner.adapter_version(spec),
        "executable_sha256": hashlib.sha256(Path(executable).read_bytes()).hexdigest()
        if executable
        else None,
        "harness_version": __version__,
        "python_version": platform.python_version(),
        "system": platform.system(),
        "machine": platform.machine(),
        "model": spec.model,
        "sandbox": spec.sandbox,
        "approval": spec.approval,
        "command": [Path(spec.command[0]).name, *spec.command[1:]] if spec.command else [],
        "host_configuration_verified": False,
        "declared_configuration": configuration_inputs(configuration_files),
        "configuration_limits": (
            "Declared local inputs and argv only; remote policy and inherited loading "
            "remain unverified."
        ),
        "source_sha256": {
            name: hashlib.sha256((ROOT / ".scripts" / name).read_bytes()).hexdigest()
            for name in (
                "eval_harness_trials.py",
                "harness_trial_results.py",
                "harness_trial_runner.py",
                "harness_trial_protocols.py",
                "harness_trial_transcripts.py",
            )
        },
    }


def load_arm(value: str) -> tuple[str, dict[str, str], str]:
    name, separator, filename = value.partition("=")
    if not separator or not name or not name.replace("-", "").isalnum():
        raise ValueError("arm must be NAME=SNAPSHOT.json with an alphanumeric or hyphenated name")
    raw = Path(filename).read_bytes()
    payload = json.loads(raw)
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, dict) or not all(
        isinstance(key, str) and isinstance(text, str) for key, text in files.items()
    ):
        raise ValueError("arm snapshot requires a files object mapping paths to text")
    validate_paths(list(files))
    return name, files, hashlib.sha256(raw).hexdigest()


def make_spec(family: str, model: str, effort: str) -> runner.RunnerSpec:
    spec = next(spec for spec in runner.BUILTIN_RUNNERS if spec.name == family)
    if family == "codex":
        command = (*spec.command, "-c", f'model_reasoning_effort="{effort}"')
        return dataclasses.replace(
            spec, command=command, model=model, sandbox="workspace-write", approval="never"
        )
    return dataclasses.replace(spec, command=(*spec.command, "--effort", effort), model=model)


def validate_trial_location(output: Path) -> None:
    markers = (
        ".git",
        "AGENTS.md",
        "AGENTS.override.md",
        "CLAUDE.md",
        "CLAUDE.local.md",
        ".claude",
        ".agents",
    )
    for parent in output.resolve().parents:
        found = [marker for marker in markers if (parent / marker).exists()]
        if found:
            raise ValueError(
                f"trial output inherits repository context from {parent}: {', '.join(found)}; "
                "choose a directory outside existing repositories and instruction trees"
            )


def run_batch(args: argparse.Namespace) -> list[dict[str, Any]]:
    validate_trial_location(args.output)
    loaded = [load_arm(value) for value in args.arm]
    arms = {name: files for name, files, _ in loaded}
    if len(arms) != len(loaded):
        raise ValueError("duplicate arm names")
    fixture_bytes = args.cases.read_bytes()
    cases = json.loads(fixture_bytes)["cases"]
    by_id = {case["id"]: case for case in cases}
    if len(by_id) != len(cases):
        raise ValueError("duplicate case ids")
    requested = args.case or list(by_id)
    if set(requested) - set(by_id):
        raise ValueError("unknown case selection")
    parameters, wheels = wheel_inputs(args)
    by_id = {name: bind_case(by_id[name], parameters) for name in requested}
    planned = schedule(requested, list(arms), args.repetitions, seed=args.seed)
    spec = make_spec(args.family, args.model, args.effort)
    configuration_files = getattr(args, "host_config", [])
    for case_id in requested:
        for files in arms.values():
            if files.keys() & by_id[case_id]["files"].keys():
                raise ValueError(f"arm overwrites task fixture files for {case_id}")
    args.output.mkdir(parents=True, exist_ok=False)
    metadata = {
        "schema_version": 1,
        "experiment": "guidance snapshot comparison; not a whole-harness speed claim",
        "family": args.family,
        "model": args.model,
        "effort": args.effort,
        "seed": args.seed,
        "case_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
        "wheel_sha256": wheels,
        "arms": {name: digest for name, _, digest in loaded},
        "schedule": planned,
        "dry_run": args.dry_run,
        "runtime": runtime_manifest(spec, configuration_files),
        "host_configuration": "Inherited host defaults; record and reconcile before causal claims.",
    }
    (args.output / "experiment.json").write_text(json.dumps(metadata, indent=2) + "\n")
    if args.dry_run:
        return []
    reports = []
    for index, (repetition, case_id, arm) in enumerate(planned):
        case = {
            **by_id[case_id],
            "guidance_files": arms[arm],
            "configuration_files": [str(path) for path in configuration_files],
        }
        identifier = f"trial-{index:04d}"
        identity = {
            "id": identifier,
            "family": args.family,
            "model": args.model,
            "effort": args.effort,
            "arm": arm,
        }
        interrupted = False
        try:
            report = execute_trial(
                spec, case, args.output / identifier, identity, timeout=args.timeout
            )
        except KeyboardInterrupt:
            interrupted = True
            retained = args.output / identifier / "trial.json"
            report = (
                json.loads(retained.read_text())
                if retained.is_file()
                else {
                    **identity,
                    "case": case_id,
                    "outcome": "interrupted",
                    "duration_s": None,
                    "tokens": None,
                    "cost": None,
                }
            )
        report["repetition"] = repetition
        reports.append(report)
        with (args.output / "trials.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps(report, allow_nan=False) + "\n")
        print(f"{identifier}: {case_id}/{arm}: {report['outcome']}", flush=True)
        (args.output / "summary.json").write_text(json.dumps(summarize(reports), indent=2) + "\n")
        if interrupted:
            raise KeyboardInterrupt
    return reports


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run isolated paired guidance trials with explicit model pins."
    )
    parser.add_argument("--family", choices=["codex", "claude"], required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", choices=["low", "medium", "high"], required=True)
    parser.add_argument("--arm", action="append", required=True)
    parser.add_argument("--cases", type=Path, default=ROOT / "tests/fixtures/harness-trials.json")
    parser.add_argument("--case", action="append")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--host-config", type=Path, action="append", default=[])
    parser.add_argument("--baseline-wheel", type=Path)
    parser.add_argument("--candidate-wheel", type=Path)
    args = parser.parse_args()
    try:
        reports = run_batch(args)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(2, f"eval-harness-trials: {error}\n")
    return int(any(report["outcome"] != "passed" for report in reports))


if __name__ == "__main__":
    raise SystemExit(main())
