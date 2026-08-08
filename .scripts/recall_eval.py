#!/usr/bin/env python3
"""Measure always-on recall per agent family against a no-guidance control.

Plan §4 Phase 1 item 1a.
The question is not whether the baseline is *well-formed* — every existing gate
answers that — but whether a rule in it is still being attended to at the size
the file has reached. Anything the agent cannot recall is not doing work.

**Why a control arm is not optional.** Ask a guidance-free agent to list the
rules it works under and it will volunteer "never commit secrets" and "keep
diffs small" from its priors alone. Crediting the baseline for those would
manufacture recall. So both arms get the *same* prompt in the *same* cell shape
and differ in exactly one bit: whether the family's always-on file is present.
Per-rule lift (baseline minus control) is the only figure that says anything
about the file.

**Why reads are denied.** With a file-read tool available the agent can simply
open the baseline and transcribe it, which measures nothing — recall becomes
`cat`. Every read path is blocked so the answer can only come from context
loaded at session start, which is the mechanism under test.

Run::

    .scripts/recall_eval.py --inventory          # derive rule ids, check anchors
    .scripts/recall_eval.py --dry-run            # print the cells and argv
    .scripts/recall_eval.py --reps 3             # execute (costs tokens)
"""

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

# Where each family's always-on file is projected, relative to a repo root. The
# cell reproduces this exact path: testing the file at some other location would
# not be testing what we ship.
FAMILY_BASELINE = {
    "claude": Path(".claude/CLAUDE.md"),
    "copilot": Path(".github/copilot-instructions.md"),
}

# Every path in a cell that any family would load as guidance. The isolation
# check asserts the set present equals the arm's declaration — the contamination
# bug worth fearing is a *second* guidance file nobody meant to ship into the
# cell.
GUIDANCE_PATHS = (
    Path("CLAUDE.md"),
    Path(".claude/CLAUDE.md"),
    Path("AGENTS.md"),
    Path(".github/copilot-instructions.md"),
    Path(".cursorrules"),
    Path(".windsurfrules"),
)

# Identical across arms, or the control measures a different question. The
# no-file-reading instruction is belt to the tool denial's braces: the denial is
# what actually enforces it.
PROMPT = (
    "List every rule, convention, or constraint that governs how work is done in "
    "this repository. Output one rule per line as a short imperative sentence. "
    "Be exhaustive — include process rules, code rules, security rules, and "
    "anything about how to commit or verify work. "
    "Answer only from what is already available to you: do not read, search, or "
    "list any files."
)

ARM_BASELINE = "baseline"  # the always-on file is present
ARM_CONTROL = "control"  # no guidance at all


@dataclass(frozen=True)
class Rule:
    """One baseline rule and the anchors that decide whether a response recalls it."""

    rule_id: str
    text: str
    anchors: tuple[tuple[str, ...], ...]

    def recalled_by(self, response: str) -> bool:
        """True when every anchor group matches *response* (case-insensitive regex)."""
        return all(
            any(re.search(term, response, re.IGNORECASE) for term in group)
            for group in self.anchors
        )


def derive_rules(baseline: Path) -> list[tuple[str, str]]:
    """Every ``(rule_id, text)`` in *baseline*, as ``<section-slug>.<n>``.

    Derived from the file rather than listed in the TOML so that a rule added to
    the catalog cannot silently escape scoring: ``load_rules`` errors when a
    derived id has no anchors.
    """
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
    """The scored rule set, refusing to proceed when the inventory has drifted.

    Three failures are distinguished because they need different fixes: a rule
    with no anchors (author them), an anchor entry for a rule that no longer
    exists (delete it), and a rule whose text changed under its anchors
    (re-review, then update ``text``). The third is the quiet one — the anchors
    still match *something*, so without this the number stays plausible and
    stops being about the rule.

    *known_ids* is the union of ids derivable across every measured family. Each
    family's baseline carries its own family-specific section, so an id absent
    from *this* baseline is not necessarily orphaned; without the union the
    orphan check would reject a correct inventory.
    """
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
    """The family's adapter with every read, write and shell path denied.

    Built on ``runner.confine_for_decider`` rather than a private list so the
    two confinements cannot drift apart. Copilot's ``read`` is added here
    because the shipped list omits it — verified deniable by live probe, tracked
    as basicly-jr0l.27; once that lands this extra becomes a no-op rather than
    a second source of truth.
    """
    spec = next(s for s in runner.BUILTIN_RUNNERS if s.name == family)
    confined = runner.confine_for_decider(spec)
    if confined is None:
        raise SystemExit(f"family {family!r} has no known tool confinement; refusing to dispatch")
    if family == "copilot" and "read" not in confined.deny_tools:
        confined = dataclasses.replace(confined, deny_tools=(*confined.deny_tools, "read"))
    return confined


def build_cell(cell_dir: Path, family: str, arm: str, baseline_source: Path) -> None:
    """Materialise one throwaway repo containing only *arm*'s guidance."""
    cell_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=cell_dir, check=True)  # nosec
    if arm == ARM_BASELINE:
        target = cell_dir / FAMILY_BASELINE[family]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(baseline_source.read_text(encoding="utf-8"), encoding="utf-8")


def assert_isolation(cell_dir: Path, family: str, arm: str) -> None:
    """Fail unless the guidance actually present equals what the arm declared.

    Read back rather than assumed: ponytail shipped a falsely tiny effect because
    a hook fired on every arm, so "the baseline was secretly running ponytail".
    An arm that cannot prove what guidance was live cannot be reported.
    """
    expected = {FAMILY_BASELINE[family]} if arm == ARM_BASELINE else set()
    present = {path for path in GUIDANCE_PATHS if (cell_dir / path).is_file()}
    if present != expected:
        raise SystemExit(
            f"cell isolation violated for {family}/{arm}: expected guidance "
            f"{sorted(map(str, expected))}, found {sorted(map(str, present))}"
        )


def score(response: str, rules: list[Rule]) -> dict[str, bool]:
    """Which rules *response* recalls."""
    return {rule.rule_id: rule.recalled_by(response) for rule in rules}


def report(results: list[dict], rules_by_family: dict[str, list[Rule]]) -> str:
    """A markdown summary: per-family aggregate, the lift, then the per-rule table.

    The denominator is per family: each baseline carries its own family-specific
    section, so the rule counts differ and a shared denominator would misstate
    one of them.
    """
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
                cells.append("n/a")  # not a rule in this family's baseline
                continue
            hits = sum(1 for r in scored if r["scores"][rule_id])
            cells.append(f"{hits}/{len(scored)}")
        lines.append(f"| `{rule_id}` | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> int:
    """Check the inventory, or build and dispatch every cell and report."""
    parser = argparse.ArgumentParser(description=__doc__)
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
                    # Elide the prompt by identity, not by index: the deny flags
                    # sit between the binary and the prompt, so a fixed index
                    # blanks a flag and makes the confinement look absent.
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
