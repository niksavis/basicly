#!/usr/bin/env python3


from __future__ import annotations

import argparse
import dataclasses
import json
import subprocess  # nosec B404
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from basicly import retention, runner  # noqa: E402  (path set above)

FAMILY_BASELINE = {
    "claude": Path(".claude/CLAUDE.md"),
    "copilot": Path(".github/copilot-instructions.md"),
    "codex": Path("AGENTS.md"),
}

GUIDANCE_PATHS = (
    Path("CLAUDE.md"),
    Path(".claude/CLAUDE.md"),
    Path("AGENTS.md"),
    Path(".github/copilot-instructions.md"),
)

PROMPT = (
    "List every rule, convention, or constraint that governs how work is done in "
    "this repository. Output one rule per line as a short imperative sentence. "
    "Be exhaustive. Answer only from what is already available to you: do not "
    "read, search, or list any files."
)

ARM_BASELINE = "baseline"
ARM_CONTROL = "control"

CALIBRATION = {
    "core-rules.1": "Keep changes small and focused; avoid refactoring unrelated code.",
    "core-rules.8": "Tests must be deterministic, and every bug fix needs a regression test.",
    "secure-coding.2": "Use parameterized queries and commands rather than string concatenation.",
    "git-discipline.1": "Run git commit on its own; never chain a push or tracker update after it.",
    "quality-gate.3": (
        "Read the pass/fail summary to confirm success, since truncated output can hide failures."
    ),
    "decision-protocol.4": "Prefer safety and security boundaries when rules conflict.",
}


def confined_spec(family: str) -> runner.RunnerSpec:
    spec = next(s for s in runner.BUILTIN_RUNNERS if s.name == family)
    confined = runner.confine_for_decider(spec)
    if confined is None:
        raise SystemExit(f"family {family!r} has no known tool confinement; refusing to dispatch")
    if family == "copilot" and "read" not in confined.deny_tools:
        confined = dataclasses.replace(confined, deny_tools=(*confined.deny_tools, "read"))
    return confined


def build_cell(cell_dir: Path, family: str, arm: str, baseline_source: Path) -> None:
    cell_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=cell_dir, check=True)  # nosec B603 B607
    if arm == ARM_BASELINE:
        target = cell_dir / FAMILY_BASELINE[family]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(baseline_source.read_text(encoding="utf-8"), encoding="utf-8")


def assert_isolation(cell_dir: Path, family: str, arm: str) -> None:
    expected = {FAMILY_BASELINE[family]} if arm == ARM_BASELINE else set()
    present = {path for path in GUIDANCE_PATHS if (cell_dir / path).is_file()}
    if present != expected:
        raise SystemExit(
            f"cell isolation violated for {family}/{arm}: expected guidance "
            f"{sorted(map(str, expected))}, found {sorted(map(str, present))}"
        )


def self_check(baseline: Path) -> int:
    rules = retention.derive_rules_from(baseline)
    if not rules:
        raise SystemExit(f"{baseline}: derived no rules; the scorer would report a free zero")

    verbatim = retention.score_response(rules, "\n".join(f"- {rule.text}" for rule in rules))
    noise = "\n".join("The quick brown fox jumps over the lazy dog." for _ in rules)
    unrelated = retention.score_response(rules, noise)
    half = retention.score_response(
        rules, "\n".join(f"- {rule.text}" for rule in rules[: len(rules) // 2])
    )

    by_id = {rule.rule_id: rule for rule in rules}
    scored: list[tuple[str, float]] = []
    for rule_id, paraphrase in CALIBRATION.items():
        if rule_id not in by_id:
            print(f"calibration: {rule_id} is no longer a rule in {baseline}", file=sys.stderr)
            continue
        report = retention.score_response(rules, paraphrase)
        match = next(m for m in report.matches if m.rule.rule_id == rule_id)
        scored.append((rule_id, match.score))

    print(f"baseline           {baseline} ({len(rules)} rules)")
    print(f"positive control   {verbatim.rate:6.1%}  (verbatim recall, must be 100%)")
    print(f"negative control   {unrelated.rate:6.1%}  (unrelated prose, must be 0%)")
    print(f"half control       {half.rate:6.1%}  (first half verbatim, must be near 50%)")
    if scored:
        mean = sum(score for _, score in scored) / len(scored)
        passed = sum(1 for _, score in scored if score >= retention.RETAINED_THRESHOLD)
        print(f"paraphrase floor   {passed}/{len(scored)} pass, mean {mean:.2f}")
        for rule_id, score in sorted(scored, key=lambda item: item[1]):
            flag = "ok  " if score >= retention.RETAINED_THRESHOLD else "MISS"
            print(f"  {flag} {score:.2f}  {rule_id}")

    broken = []
    if verbatim.rate < 1.0:
        broken.append(f"positive control is {verbatim.rate:.1%}, not 100%")
    if unrelated.rate > 0.0:
        broken.append(f"negative control is {unrelated.rate:.1%}, not 0%")
    if broken:
        print("\nSCORER BROKEN: " + "; ".join(broken), file=sys.stderr)
        return 1
    print("\nscorer controls OK")
    return 0


def render(baseline: Path, report: retention.Report, arm: str) -> str:
    lines = [
        f"{baseline} [{arm}]: {report.retained}/{report.total} rules retained "
        f"({report.rate:.1%}) from a {report.response_lines}-line response",
        "",
        "Retention by position in the file (earliest bucket first):",
        "",
        "| bucket | retained | of | rate |",
        "| --- | --- | --- | --- |",
    ]
    for index, kept, total in report.by_position():
        lines.append(f"| {index + 1} | {kept} | {total} | {kept / total:.0%} |")
    lines.extend([
        "",
        "Retention by section:",
        "",
        "| section | retained | of |",
        "| --- | --- | --- |",
    ])
    for section, (kept, total) in sorted(report.by_section().items()):
        lines.append(f"| `{section}` | {kept} | {total} |")

    forgotten = report.forgotten()
    if forgotten:
        lines.extend(["", f"Not retained ({len(forgotten)}), lowest score first:", ""])
        for match in forgotten:
            lines.append(f"- `{match.rule.rule_id}` [{match.score:.2f}] {match.rule.text}")
            lines.append(f"    closest line: {match.evidence[:120] or '(nothing)'}")
    lines.extend([
        "",
        "A score is lexical, not semantic: it asks whether the distinctive words of a "
        "rule appear together in some line of the response. The calibration set puts the "
        "false-negative rate near 1 in 6 on a distant paraphrase, so treat the absolute "
        "rate as a floor. A change in the rate on an unchanged rule set is the signal; "
        "the rate itself is not comparable across edits to the rules.",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure how much of an always-on instruction file a model retains."
    )
    parser.add_argument("--family", default="claude", choices=sorted(FAMILY_BASELINE))
    parser.add_argument(
        "--self-check", action="store_true", help="run the scorer controls and exit"
    )
    parser.add_argument(
        "--score", type=Path, help="score this response file instead of dispatching"
    )
    parser.add_argument("--control", action="store_true", help="also run the no-guidance arm")
    parser.add_argument(
        "--content",
        type=Path,
        help="use this file's content at the family's own guidance path, holding the host "
        "mechanism constant so length is the only variable",
    )
    parser.add_argument("--cells", type=Path, help="directory for throwaway repos")
    parser.add_argument("--out", type=Path, help="write the raw report JSON here")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    baseline = REPO_ROOT / FAMILY_BASELINE[args.family]
    if getattr(args, "content", None) is not None:
        baseline = args.content if args.content.is_absolute() else REPO_ROOT / args.content

    if args.self_check:
        return self_check(baseline)

    rules = retention.derive_rules_from(baseline)

    if args.score is not None:
        report = retention.score_response(rules, args.score.read_text(encoding="utf-8"))
        print(render(baseline, report, "scored"))
        return 0

    if args.cells is None:
        parser.error("--cells is required (a directory for throwaway repos)")

    spec = confined_spec(args.family)
    arms = [ARM_BASELINE, ARM_CONTROL] if args.control else [ARM_BASELINE]
    payload: list[dict[str, object]] = []

    for arm in arms:
        cell = args.cells / f"{args.family}-{arm}"
        build_cell(cell, args.family, arm, baseline)
        assert_isolation(cell, args.family, arm)
        print(f"[{args.family}/{arm}] dispatching…", flush=True)
        outcome = runner.run(spec, PROMPT, cell, timeout=args.timeout)
        response = outcome.stdout or ""
        report = retention.score_response(rules, response)
        print("\n" + render(baseline, report, arm) + "\n")
        payload.append({
            "family": args.family,
            "arm": arm,
            "rate": report.rate,
            "retained": report.retained,
            "total": report.total,
            "duration_s": outcome.duration_s,
            "by_position": report.by_position(),
            "response": response,
        })

    if len(payload) == 2:
        lift = payload[0]["rate"] - payload[1]["rate"]  # type: ignore[operator]
        print(f"lift attributable to the file: {lift:+.1%}")

    if args.out:
        args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"raw report -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
