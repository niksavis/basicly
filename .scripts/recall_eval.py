#!/usr/bin/env python3


from __future__ import annotations

import argparse
import dataclasses
import json
import re
import subprocess  # nosec B404
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from basicly import runner  # noqa: E402  (path set above)

RULES_TOML = Path(__file__).resolve().parent / "recall_rules.toml"

FAMILY_BASELINE = {
    "claude": Path(".claude/CLAUDE.md"),
    "copilot": Path(".github/copilot-instructions.md"),
}

GUIDANCE_PATHS = (
    Path("CLAUDE.md"),
    Path(".claude/CLAUDE.md"),
    Path("AGENTS.md"),
    Path(".github/copilot-instructions.md"),
    Path(".cursorrules"),
    Path(".windsurfrules"),
)

PROMPT = (
    "List every rule, convention, or constraint that governs how work is done in "
    "this repository. Output one rule per line as a short imperative sentence. "
    "Be exhaustive — include process rules, code rules, security rules, and "
    "anything about how to commit or verify work. "
    "Answer only from what is already available to you: do not read, search, or "
    "list any files."
)

ARM_BASELINE = "baseline"
ARM_CONTROL = "control"


@dataclass(frozen=True)
class Rule:
    rule_id: str
    text: str
    anchors: tuple[tuple[str, ...], ...]

    def recalled_by(self, response: str) -> bool:
        return all(
            any(re.search(term, response, re.IGNORECASE) for term in group)
            for group in self.anchors
        )


def derive_rules(baseline: Path) -> list[tuple[str, str]]:

    derived: list[tuple[str, str]] = []
    section: str | None = None
    index = 0
    for line in baseline.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section = re.sub(r"[^a-z0-9]+", "-", line[3:].strip().lower()).strip("-")
            index = 0
        elif line.strip().startswith("- ") and section:
            index += 1
            body = re.sub(r"[`*_]", "", line.strip()[2:])
            derived.append((f"{section}.{index}", re.sub(r"\s+", " ", body)))
    return derived


def load_rules(baseline: Path, known_ids: set[str] | None = None) -> list[Rule]:

    configured = tomllib.loads(RULES_TOML.read_text(encoding="utf-8"))["rules"]
    derived = derive_rules(baseline)
    derived_ids = {rule_id for rule_id, _ in derived}

    missing = sorted(derived_ids - set(configured))
    if missing:
        raise SystemExit(
            f"{len(missing)} baseline rule(s) have no anchors in {RULES_TOML.name} "
            f"and would score zero for free: {missing}"
        )
    orphaned = sorted(set(configured) - (known_ids or derived_ids))
    if orphaned:
        raise SystemExit(
            f"{len(orphaned)} anchor entr(ies) name a rule no measured baseline has: {orphaned}"
        )

    rules: list[Rule] = []
    drifted: list[str] = []
    for rule_id, text in derived:
        entry = configured[rule_id]
        if not text.startswith(entry["text"]):
            drifted.append(f"{rule_id}: stored {entry['text']!r} != live {text[:70]!r}")
            continue
        rules.append(
            Rule(rule_id, text, tuple(tuple(group) for group in entry["anchors"])),
        )
    if drifted:
        raise SystemExit(
            "rule text drifted under its anchors; re-review the anchors, then update "
            "'text':\n  " + "\n  ".join(drifted)
        )
    return rules


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
    subprocess.run(["git", "init", "-q"], cwd=cell_dir, check=True)  # nosec
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


def score(response: str, rules: list[Rule]) -> dict[str, bool]:
    return {rule.rule_id: rule.recalled_by(response) for rule in rules}


def report(results: list[dict], rules_by_family: dict[str, list[Rule]]) -> str:

    families = sorted({r["family"] for r in results})
    lines: list[str] = []

    lines.append("| Family | Arm | Reps | Mean rules recalled | of | Mean recall |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    means: dict[tuple[str, str], float] = {}
    for family in families:
        total = len(rules_by_family[family])
        for arm in (ARM_BASELINE, ARM_CONTROL):
            cells = [r for r in results if r["family"] == family and r["arm"] == arm]
            if not cells:
                continue
            counts = [sum(c["scores"].values()) for c in cells]
            mean = sum(counts) / len(counts)
            means[(family, arm)] = mean
            lines.append(
                f"| {family} | {arm} | {len(cells)} | {mean:.1f} | {total} | {mean / total:.0%} |"
            )
    lines.append("")

    lines.append("| Family | Baseline recall | Control base rate | Lift attributable to the file |")
    lines.append("| --- | --- | --- | --- |")
    for family in families:
        total = len(rules_by_family[family])
        base = means.get((family, ARM_BASELINE))
        ctrl = means.get((family, ARM_CONTROL))
        if base is None or ctrl is None:
            continue
        lines.append(
            f"| {family} | {base / total:.0%} | {ctrl / total:.0%} "
            f"| {100 * (base - ctrl) / total:+.0f} pp |"
        )
    lines.append("")

    lines.append("Per-rule rates (reps recalling the rule / reps run):")
    lines.append("")
    columns = [(f, a) for f in families for a in (ARM_BASELINE, ARM_CONTROL)]
    lines.append("| Rule | " + " | ".join(f"{f} {a}" for f, a in columns) + " |")
    lines.append("| --- |" + " --- |" * len(columns))
    every_id = sorted({rule.rule_id for rules in rules_by_family.values() for rule in rules})
    for rule_id in every_id:
        cells = []
        for family, arm in columns:
            runs = [r for r in results if r["family"] == family and r["arm"] == arm]
            scored = [r for r in runs if rule_id in r["scores"]]
            if not scored:
                cells.append("n/a")
                continue
            hits = sum(1 for r in scored if r["scores"][rule_id])
            cells.append(f"{hits}/{len(scored)}")
        lines.append(f"| `{rule_id}` | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure always-on recall per agent family against a no-guidance control."
    )
    parser.add_argument("--families", default="claude,copilot", help="comma-separated")
    parser.add_argument("--reps", type=int, default=3, help="samples per cell")
    parser.add_argument("--dry-run", action="store_true", help="print cells and argv only")
    parser.add_argument("--inventory", action="store_true", help="check anchors and exit")
    parser.add_argument("--cells", type=Path, help="directory for throwaway cells")
    parser.add_argument("--out", type=Path, help="write raw results JSON here")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    families = [f.strip() for f in args.families.split(",") if f.strip()]
    union = {
        rule_id
        for family in families
        for rule_id, _ in derive_rules(REPO_ROOT / FAMILY_BASELINE[family])
    }
    rules_by_family = {
        family: load_rules(REPO_ROOT / FAMILY_BASELINE[family], union) for family in families
    }

    if args.inventory:
        for family, rules in rules_by_family.items():
            print(f"{family}: {len(rules)} rules, all anchored, no drift")
        return 0

    if args.cells is None:
        parser.error("--cells is required (a directory for throwaway repos)")

    results: list[dict] = []
    for family in families:
        baseline_source = REPO_ROOT / FAMILY_BASELINE[family]
        rules = rules_by_family[family]
        spec = confined_spec(family)
        for arm in (ARM_BASELINE, ARM_CONTROL):
            for rep in range(1, args.reps + 1):
                cell = args.cells / f"{family}-{arm}-{rep}"
                build_cell(cell, family, arm, baseline_source)
                assert_isolation(cell, family, arm)
                argv = runner.format_command(spec, PROMPT)
                if args.dry_run:
                    shown = ["<prompt>" if part == PROMPT else part for part in argv]
                    print(f"[{family}/{arm}/{rep}] cwd={cell}")
                    print("  " + " ".join(shown))
                    continue
                print(f"[{family}/{arm}/{rep}] dispatching…", flush=True)
                outcome = runner.run(spec, PROMPT, cell, timeout=args.timeout)
                response = outcome.stdout or ""
                results.append({
                    "family": family,
                    "arm": arm,
                    "rep": rep,
                    "returncode": outcome.returncode,
                    "duration_s": outcome.duration_s,
                    "chars": len(response),
                    "response": response,
                    "scores": score(response, rules),
                })

    if args.dry_run:
        return 0

    if args.out:
        args.out.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nraw results -> {args.out}")
    print("\n" + report(results, rules_by_family))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
