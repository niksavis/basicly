from __future__ import annotations

import argparse
import dataclasses
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from basicly import checkout, runner

ROOT = Path(__file__).resolve().parent.parent

TARGETS = {
    "codex": "AGENTS.md",
    "claude": ".claude/CLAUDE.md",
    "copilot": ".github/copilot-instructions.md",
}


def grade(cases: list[dict], response: str) -> dict[str, bool]:
    text = response.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    if not text.startswith("{") and "\n{" in text:
        text = "{" + text.rsplit("\n{", 1)[1]
    answers = json.loads(text)
    if not isinstance(answers, dict) or set(answers) != {case["id"] for case in cases}:
        raise ValueError("response must contain exactly the scenario ids")
    return {case["id"]: answers[case["id"]] == case["expected"] for case in cases}


def probe(
    family: str,
    arm: str,
    baseline: str,
    directory: Path,
    model_map: Path | None = None,
) -> dict:
    cases = json.loads((ROOT / "tests/fixtures/baseline-decisions.json").read_text())
    cell = directory / f"{family}-{arm}"
    cell.mkdir(parents=True, exist_ok=False)
    checkout.git(["init", "-q"], cwd=cell)
    if model_map is not None:
        target = cell / ".basicly/core/models/model-map.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(model_map.read_text(), encoding="utf-8")
    path = cell / TARGETS[family]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(baseline, encoding="utf-8")
    spec = next(spec for spec in runner.BUILTIN_RUNNERS if spec.name == family)
    confined = runner.confine_for_decider(spec)
    if confined is None:
        raise ValueError(f"no read-only confinement for {family}")
    spec = dataclasses.replace(confined, tier="high", model=None)
    situations = [
        {key: value for key, value in case.items() if key != "expected"} for case in cases
    ]
    prompt = (
        "Read the repository's instruction file. For each independent situation, choose the "
        "next action that follows those instructions. Do not execute the described actions. "
        "Return only a JSON object mapping each scenario id to its choice letter.\n"
        + json.dumps(situations)
    )
    result = runner.run(spec, prompt, cell, capture_usage=True, timeout=180)
    response = runner.result_text(spec, result.stdout)
    report = {
        "family": family,
        "arm": arm,
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "duration_s": result.duration_s,
        "response": response,
        "stderr": result.stderr,
        "model_resolution": dataclasses.asdict(result.model_resolution)
        if result.model_resolution
        else None,
    }
    if result.returncode != 0 or result.timed_out:
        report["error"] = "runner did not complete successfully"
    else:
        try:
            report["checks"] = grade(cases, response)
        except (ValueError, TypeError) as exc:
            report["error"] = str(exc)
    (directory / f"{family}-{arm}.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-map", type=Path)
    args = parser.parse_args()
    before = json.loads(args.before.read_text())
    cases = json.loads((ROOT / "tests/fixtures/baseline-decisions.json").read_text())
    jobs = [
        (
            family,
            arm,
            before["targets"][path]["text"] if arm == "before" else (ROOT / path).read_text(),
        )
        for family, path in TARGETS.items()
        for arm in ("before", "after")
    ]
    if any(
        (parent / target).is_file()
        for parent in args.output.resolve().parents
        for target in TARGETS.values()
    ):
        raise ValueError(
            "probe output must be outside repositories with ancestor instruction files"
        )
    args.output.mkdir(parents=True, exist_ok=False)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(probe, family, arm, text, args.output, args.model_map)
            for family, arm, text in jobs
        ]
        reports = [future.result() for future in futures]
    (args.output / "results.json").write_text(json.dumps(reports, indent=2) + "\n")
    for report in reports:
        checks = report.get("checks", {})
        print(
            f"{report['family']}/{report['arm']}: {sum(checks.values())}/{len(cases)}; "
            f"{report.get('error', 'completed')}"
        )
    return int(
        any(
            "error" in report or not all(report.get("checks", {}).values())
            for report in reports
            if report["arm"] == "after"
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
