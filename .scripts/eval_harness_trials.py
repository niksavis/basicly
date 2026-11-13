from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
from pathlib import Path
from typing import Any

from harness_trial_arms import VALUE_MODES, select_setup
from harness_trial_models import EFFORT_LEVELS, make_spec, validate_environment
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
        **{
            name: [[expand(arg) for arg in command] for command in case[name]]
            for name in ("setup", "task_setup", "harness_setup")
            if name in case
        },
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
                "harness_trial_models.py",
                "harness_trial_claude.py",
                "harness_trial_evidence.py",
                "harness_trial_results.py",
                "harness_trial_arms.py",
                "harness_trial_runner.py",
                "harness_trial_protocols.py",
                "harness_trial_transcripts.py",
            )
        },
    }


def load_arm(value: str) -> tuple[str, dict[str, str], str, str]:
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
    mode = payload.get("mode", "guidance")
    if mode not in {"guidance", *VALUE_MODES}:
        raise ValueError("unknown arm mode")
    if mode != "guidance" and files:
        raise ValueError("value arms cannot replace guidance files")
    return name, files, hashlib.sha256(raw).hexdigest(), mode


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


def load_arms(values: list[str], family: str) -> list[tuple[str, dict[str, str], str, str]]:
    if family not in {"codex", "claude"}:
        raise ValueError("value comparisons require a native Codex or Claude family")
    loaded = [load_arm(value) for value in values]
    modes = {name: mode for name, _, _, mode in loaded}
    if len(modes) != len(loaded):
        raise ValueError("duplicate arm names")
    value_comparison = any(mode != "guidance" for mode in modes.values())
    if value_comparison and (len(loaded) != 3 or set(modes.values()) != VALUE_MODES):
        raise ValueError("value comparison requires plain, gates-only and full-basicly modes")
    return loaded


def run_batch(args: argparse.Namespace) -> list[dict[str, Any]]:
    advisor_model = getattr(args, "advisor_model", None)
    validate_environment(args.family, args.effort, advisor_model, os.environ)
    validate_trial_location(args.output)
    loaded = load_arms(args.arm, args.family)
    arms = {name: files for name, files, _, _ in loaded}
    modes = {name: mode for name, _, _, mode in loaded}
    value_comparison = any(mode != "guidance" for mode in modes.values())
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
    for case in by_id.values():
        for mode in modes.values():
            select_setup(case, mode)
    planned = schedule(requested, list(arms), args.repetitions, seed=args.seed)
    spec = make_spec(args.family, args.model, args.effort, advisor_model)
    configuration_files = getattr(args, "host_config", [])
    for case_id in requested:
        for files in arms.values():
            if files.keys() & by_id[case_id]["files"].keys():
                raise ValueError(f"arm overwrites task fixture files for {case_id}")
    args.output.mkdir(parents=True, exist_ok=False)
    metadata = {
        "schema_version": 1,
        "experiment": "repository harness value comparison"
        if value_comparison
        else "guidance snapshot comparison; not a whole-harness speed claim",
        "arm_modes": modes,
        "stopping_limits": {"timeout_s": args.timeout, "repetitions": args.repetitions},
        "family": args.family,
        "model": args.model,
        "effort": args.effort,
        "advisor_model": advisor_model or "inherited",
        "seed": args.seed,
        "case_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
        "wheel_sha256": wheels,
        "arms": {name: digest for name, _, digest, _ in loaded},
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
            **select_setup(by_id[case_id], modes[arm]),
            "arm_mode": modes[arm],
            "guidance_files": arms[arm],
            "configuration_files": [str(path) for path in configuration_files],
        }
        identifier = f"trial-{index:04d}"
        identity = {
            "id": identifier,
            "family": args.family,
            "model": args.model,
            "effort": args.effort,
            "advisor_model": advisor_model or "inherited",
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
    parser.add_argument("--effort", choices=EFFORT_LEVELS, required=True)
    parser.add_argument(
        "--advisor-model", help="reviewed Fable model id, or off with the advisor disabled"
    )
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
