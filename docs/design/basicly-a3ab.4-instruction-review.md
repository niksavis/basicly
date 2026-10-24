# Baseline retrieval review

The three baseline outputs now share one formatting template. Commands appear first,
followed by Always do, Ask first and Never do. Catalog tags select the constraint group;
untagged custom catalogs keep their flat layout. Target filters and size caps are unchanged.

## Instruction accounting

The companion JSON accounts for 127 instruction units from all 24 fragments selected by
the original loader and planner. Units are paragraphs, bullets and fenced examples,
identified by original body line ranges and hashes. It records the baseline commit,
each destination and each reason. It is an audit, not an editable instruction source.

Approval categories, approval reuse, frozen scope, user work, security restrictions,
verification gates and append-only tracker history remain required. Gate protection moved
verbatim from the confirmation section to Never do. Shortened explanations retain their
required actions. Routine observed counts now name one checked source; independent
calculation remains required for high-risk quantities and material decisions. Absence
claims still require action-based search, a known-match control and an examined-count check.

Four former scoped rules now have unconditional baseline triggers and ordinary repository
file links on every target. Their full procedures live in existing projected skills:

| Removed generated rule | Canonical procedure |
| --- | --- |
| `.claude/rules/external-review.md` | `falsify-first/skill.yaml`, third-party recommendations |
| `.claude/rules/model-tier-routing.md` | `tier-injection/skill.yaml`, conditional tier-only policy |
| `.claude/rules/platform-hermetic-tests.md` | `test-discipline/skill.yaml`, platform forms |
| `.claude/rules/rendered-surfaces.md` | `validate-as-consumer/skill.yaml`, rendered pages |

Sources stay YAML. Both `.agents/skills/` and `.claude/skills/` receive the procedures.
A consumer reads the explicit repository path without relying on host-specific scope
loading. The overflow command is conditional on a repository supplying that checker;
the private source-checkout script is not assumed to exist in every installed consumer.
The no-comments skill already owned directive exceptions and the repair command.

## Measurements

Python measured `len(Path.read_bytes())` and newline-byte counts. `wc -lc` independently
checked the captured before files and current generated outputs. Every file ends with a
newline. These are this story's input and output, rather than older filing-time numbers.

| Target | Bytes before | Bytes after | Lines before | Lines after |
| --- | --- | --- | --- | --- |
| `AGENTS.md` | 14771 | 12001 | 234 | 198 |
| `.claude/CLAUDE.md` | 11892 | 11801 | 186 | 198 |
| `.github/copilot-instructions.md` | 11984 | 11893 | 187 | 199 |

Claude and Copilot gain section headings and specialist triggers; their line counts rise
while bytes fall. Codex loses the inlined full specialist procedures. No cap was raised.

## Decision probes and consumer run

The fixture `tests/fixtures/baseline-decisions.json` covers ordinary code work, an already
approved CI action, frozen scope, focused verification, source-to-projection editing,
unapproved destructive work, routine counts and absence controls. Expected answers are
withheld from the host prompt. `.scripts/eval_baseline_decisions.py` uses the existing
read-only runner adapter and declared high tier, with one isolated repository per arm.
Cells live outside the source checkout; the evaluator refuses ancestor baseline files.

| Native host | Before | After |
| --- | --- | --- |
| Codex | 7/8 | 8/8 |
| Claude | 8/8 | 8/8 |
| Copilot | Unscored: extra CLI output after JSON | 8/8 |

These are one sample per host and arm, using multiple-choice next-action decisions.
They do not execute the proposed actions and do not establish production adherence or
an improvement rate. Host-global customizations are not removed. The before Copilot
sample is not counted as a failure of the instructions. Initial experiments under the
source tree were discarded because ancestor instructions could contaminate them.

The production high-tier Copilot mapping was unavailable. The final paired experiment
used a separate map with `claude-opus-5.5`, established from the installed CLI and
[GitHub's current CLI reference](https://github.com/github/docs/blob/main/content/copilot/reference/copilot-cli-reference/cli-command-reference.md).
The production model map stays unchanged; adjacent bug `basicly-a3ab.13` is filed only.
Raw responses, durations, resolved models and the experimental map remain in ignored
usage evidence for this task.

A fresh repository with the shipped core and a consumer Commands overlay ran the public
`basicly build`, `skills-build`, `check` and `skills-check` commands. Every target rendered
the same four headings in order. Direct skill paths existed, including the complete
platform and directive procedures. The standalone comments kit ran there without
importing the engine and reported `0 prose comments in 1 files, 0 unreadable`.

The heading-parser regression failed when its fix was removed and passed after restoring
it. The focused suite passed 100 tests. Catalog lint passed its unchanged routing floor.
All projection checks passed. Retention scorer controls passed (100% verbatim, 0% noise);
its old positional paraphrase calibration missed all five surviving entries, so no
lexical retention score is used as behavioral evidence.

The first native full run found three integration failures: the condensed comment-ban
wording omitted the explicit directive example expected by its coverage test, the marker
test still read the confirmation fragment after its marker moved, and the audit document
was absent from the documentation index. All three reproduced together before repair.
The baseline now explicitly states the ban, names its enforcing hook and gives `noqa` as
the directive example. The marker test retains its exact assertion at the new canonical
source. The documentation index names this audit. The 66 focused repair tests and the
fresh consumer projection pass; final native verification remains mandatory.
