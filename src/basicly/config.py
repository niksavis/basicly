"""Project path configuration for basicly."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import __version__, br, dropin, permissions, session, tree_schema
from .lane_log import DEFAULT_RETAINED_SESSIONS
from .models import ModelResolutionError
from .runner import (
    AGENT_TIER,
    AGENT_WINDOW,
    AUTO,
    BUILTIN_RUNNERS,
    DECLARED_WINDOW,
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_AGENT_PROCESSES,
    DEFAULT_QUIET_AFTER,
    DEFAULT_STALL_AFTER,
    DENY_STYLES,
    FALLBACK_WINDOW,
    FAMILY_DEFAULT_TIER,
    HEADLESS,
    PROMPT_VIA,
    USAGE_FORMATS,
    RunnerSpec,
    resolve_model,
)
from .schema import MODEL_TIERS, TECHNOLOGIES
from .tree_schema import Table

COPILOT_RUNNER = "copilot"

CONFIG_FILE = "basicly.toml"

# Gitignored per-machine overlay: keys here override CONFIG_FILE for the
# harness sections only ([worktree], [verify], [policy], [runner]). Projection
# config ([paths], [catalog]) shapes repo-committed outputs, so it stays
# repo-level and never reads the overlay.
LOCAL_CONFIG_FILE = "basicly.local.toml"

# Scaffolded into a consumer repo by `basicly install`. Kept next to the
# defaults below; test_config asserts parsing this yields exactly the built-in
# defaults, so the two can never drift apart.
DEFAULT_CONFIG_TOML = """\
# basicly path wiring. Managed core catalog is materialized and upgraded by
# `basicly install`; the overlay is always yours to edit.
#
# Per-machine harness settings ([worktree], [verify], [policy], [runner]) can
# be overridden in a gitignored basicly.local.toml next to this file — keys
# there win over this file, so machine-specific choices (e.g. runner default)
# stay out of the shared config. [paths] and [catalog] are repo-level only.
[paths]
core_fragments = ".basicly/core/fragments"
overlay_fragments = [".basicly-local/fragments"]
targets = ".basicly/core/targets"
templates = ".basicly/core/templates"
manifest = ".basicly/generated-manifest.json"

# Catalog technology selection. Absent = the full catalog ships. List the
# stack/environment tags this repo wants and technology-tagged sources outside
# it are skipped at projection time (untagged sources are universal and always
# ship). Recorded by `basicly install --technologies ...`.
#
# [catalog]
# technologies = ["python", "zsh"]

# Sibling git-worktree isolation for harness tracks.
[worktree]
# Branch new harness/<name> worktrees fork from. Empty = the current branch.
base_branch = ""
# Cap on how many worktrees may exist at once.
concurrency = 5
# Files this repo's convention has EVERY lane append its own entry to, which no
# bead's "## Scope" therefore names — a changelog, a release-notes file. Two
# lanes appending at the same anchor conflict when the later one rebases, so a
# declared path serializes the plan's children instead of letting the merge
# queue discover it. A child that really does not collide (it owns the entry, or
# writes a distinct section) declares the path in its own scope AND under
# "shared" to stay parallel. Absent = nothing is treated as append-only.
#
# append_only_paths = ["CHANGELOG.md"]
#
# Artifacts every lane's edit regenerates and no bead declares — a projection
# manifest, a lockfile. They collide the same way, but serializing the lanes buys
# nothing: the file is a function of the tree, so it simply needs rebuilding once.
# A landing rebase whose conflicts are ALL declared here is resolved by running each
# one's own command in the lane's worktree and continuing; a conflict touching any
# other path, or one the rebuild leaves a conflict marker in, still bounces to the
# lane, untouched. Keyed by path, so a path declared without a rebuild cannot exist.
#
# [worktree.regenerate_commands]
# ".basicly/generated-manifest.json" = ["basicly", "build"]

# Deterministic verify gate. Each check runs in the listed modes; a "staged"
# check with staged_suffix runs only against staged files of that suffix.
# No checks are enabled by default — declare the ones your stack actually has
# (an empty config passes vacuously; a configured command missing from PATH
# fails the run with a one-line message).
#
# A check may also declare fix_command — a deterministic, lossless repair the
# pre-commit hook applies to the staged files before gating (and that
# `basicly verify --fix` applies before the checks), so a mechanical repair like
# reformatting never costs a review cycle. The check still runs either way.
#
# Python examples:
#
# [[verify.checks]]
# name = "ruff"
# command = ["ruff", "check"]
# modes = ["fast", "full", "staged"]
# staged_suffix = ".py"
#
# [[verify.checks]]
# name = "ruff-format"
# command = ["ruff", "format", "--check"]
# fix_command = ["ruff", "format"]
# modes = ["fast", "full", "staged"]
# staged_suffix = ".py"
#
# [[verify.checks]]
# name = "pytest"
# command = ["pytest", "-q"]
# modes = ["full"]

# Loop gate/checkpoint policy: which gates block advancement and the rework cap.
[policy]
# Gate names (from [verify] / br gate report) that MUST pass to advance. Any
# recorded gate not listed here is advisory (never blocks).
required_gates = ["verify"]
# Rework retries allowed before a node escalates to a human.
max_rework = 2
# Units that may stand downstream of build - merged and parked in verify, or
# waiting on a ship checkpoint - before a further dispatch is refused. Bounds
# unlanded work, which [worktree] concurrency does not: that bounds how many
# lanes run at once, this bounds how much finished-but-unreviewed output piles
# up. Lower it when review, not machine capacity, is what runs out.
max_downstream_wip = 5

# Working-set sizing governor for decompose (factory D8). A child's context
# cost is estimated deterministically (instruction overhead + scope read-cost
# x per-class build factor, chars/4 tokens) and a plan outside the band is
# refused: above the max split into more packages; below the min (when the
# scope matches existing files) merge with a sibling in its scope group.
# [policy.sizing]
# working_set_min = 8000
# working_set_max = 64000
# calibration_min_samples = 10   # measured spend ratios replace the declared prior
#                                # only past this many paired records per class
# calibration_window = 50        # rolling run-record window per task class
# unsized_lane_quantile = 0.9    # bound for a lane with no readable scope: the
#                                # quantile of recent lane actuals, so at most one
#                                # lane in ten is expected to exceed it
# [policy.sizing.build_factor]
# task = 3.0
# bug = 2.0
# chore = 1.5

# Agent-agnostic runner: how the harness invokes a coding agent headless to do a
# node's work in its worktree. "auto" detects claude -> codex -> copilot on PATH,
# else falls back to the "manual" handoff (no command is guessed for an unknown
# agent). Add or override an agent with [[runner.agents]]; verify any command
# with `basicly runner dry-run` before a live run.
[runner]
default = "auto"
# Ceiling on concurrently live agent processes across every class the engine
# spawns: lane runners draw on [worktree] concurrency reserved slots, the decider
# on one reserved slot, and read-only helpers queue on the remainder.
# max_agent_processes = 8
# [[runner.agents]]
# name = "opencode"
# command = ["opencode", "run", "{prompt}"]
# prompt_via = "arg"   # or "stdin"
# model = "opus"       # optional: injects `--model opus` after the binary,
#                      # or substitutes a `{model}` placeholder if the command has one
# tier = "high"        # optional, and preferred over `model`: a portable model tier
#                      # (low | medium | high | maximum) resolved to the concrete id
#                      # this family's surface accepts, via .basicly/core/models.
#                      # A tier that resolves to nothing refuses the dispatch rather
#                      # than falling back to another tier's model. `model` wins if both.
# vendor = "openai"    # optional: whose model a tier resolves to. Only meaningful on a
#                      # multi-vendor surface (copilot serves four); defaults per family.
# sandbox = "workspace-write"   # optional: injects `--sandbox workspace-write` (codex
#                               # defaults this); network is disabled by default in it
# approval = "never"            # optional: injects `-a never` (codex defaults this).
#                               # Validated against the CLI's own enum by
#                               # `basicly runner dry-run`: a value it rejects
#                               # fails every dispatch at argument parsing.
# git_name = "opencode-bot"        # optional bot git identity: dispatched commits
# git_email = "bot@example.com"    # use it (both keys or neither). Must satisfy
#                                  # basicly.identityAllowEmail when strict mode is on.
"""


# Default concurrency cap when no basicly.toml (or no [worktree]) is present.
# Five: it matches the scaffold above, and `DEFAULT_MAX_AGENT_PROCESSES` of 8 splits
# into exactly 5 lane slots plus the reserved decider and helper slots, so the worktree
# cap and the process budget agree instead of one silently throttling the other.
DEFAULT_WORKTREE_CONCURRENCY = 5

# Modes the verify runner understands.
VERIFY_MODES = ("fast", "full", "staged")

# Policy defaults when no basicly.toml (or no [policy]) is present.
DEFAULT_REQUIRED_GATES = ("verify",)
DEFAULT_MAX_REWORK = 2

# How many units may stand downstream of BUILD unlanded before a further dispatch
# is refused (requirements 3.1). Five matches DEFAULT_WORKTREE_CONCURRENCY so a
# first full-width pass is admitted and a *second* cohort is not until the first
# is reviewed — but the two are independent quantities, not one knob spelled
# twice: concurrency bounds how many lanes run at once, this bounds how much
# finished-but-unlanded output exists. Lower it to make review capacity, rather
# than slots or tokens, the constraint that binds first.
DEFAULT_MAX_DOWNSTREAM_WIP = 5

# Gate providers the engine itself records under — re-exported as
# ``verify.GATE_PROVIDER`` and ``rubrics.GATE_PROVIDER``, which are the only two
# call sites that write a gate result. They live here so ``policy.gate_status``
# can recognise them without importing either module, and so a rename cannot
# desynchronise the recogniser from the recorder (basicly-jr0l.51).
#
# A *required* gate counts only when its result carries one of these. ``br gate
# report`` authenticates nothing and a dispatched lane agent shares the real
# tracker through the worktree beads redirect, so without this filter one br
# call from inside a dispatch satisfies a required gate — the constraint that no
# model holds authority over a required gate would hold only by agent good
# behaviour. Forging one of these provider strings is still possible; that is
# the same acknowledged class as grant and checkpoint marker forgery, and this
# is the narrowest hardening available without authenticated gate results.
#
# Both are engine-owned, not just the verify one: the deterministic pre-flight
# half of the rubric gate is documented as promotable into [policy]
# required_gates by a consumer (rubrics.RUBRIC_GATE), and filtering it out would
# make that promotion permanently unsatisfiable.
VERIFY_GATE_PROVIDER = "basicly-verify"
RUBRIC_GATE_PROVIDER = "basicly-rubric"
ENGINE_GATE_PROVIDERS = frozenset({VERIFY_GATE_PROVIDER, RUBRIC_GATE_PROVIDER})

# Working-set sizing defaults (factory design D8/section 6, basicly-kjc5.2):
# the govern band is absolute tokens of material to reason over, NOT a fraction
# of the context window (the 50-70 percent folk rule was researched and refuted).
#
# The ceiling is DERIVED, not chosen (basicly-3w44), and it is derived FROM THE
# ESTIMATOR — so it moves whenever the estimator does. The rule, so the next reader
# can recompute rather than trust the number: size every headless dispatch this
# engine has recorded by the current formula, take the largest estimate on one that
# completed, and round up to the nearest multiple of WORKING_SET_MIN. Today that is
# basicly-tcmy.5's own dispatch at 130_780, so 132_000.
#
# It has now been derived five times, and every move but the last two was a defect in
# the *measure* surfacing rather than new evidence about how big a lane can be:
#
# * 64_000 was chosen, never checked, and wrong 18 times out of 18 — eighteen
#   recorded lanes exceeded it and every one completed (basicly-3w44).
# * 112_000 came from the same rule applied to whole-file scope sizing, and only to
#   the completed lanes whose records happened to carry a `scope_tokens` int.
# * 56_000 was that rule applied to the read-capped measure (basicly-fcls), over both
#   outcome populations sized the same way. It is LOWER than its predecessors because
#   the estimator shrank, not because the engine got more timid: the same
#   basicly-kjc5.42 dispatch that sized 136_668 as three whole files sizes 12_000 as
#   the material a lane reads out of them.
# * 72_000 is the same rule one dispatch later, and the dispatch is this bead's own:
#   56_000 was derived from basicly-tcmy.31 at 53_004 while the lane deriving it was
#   still running, and the record it wrote on finishing — 72_000, nine globs at the
#   bug seed — immediately contradicted the constant it had just committed. The gate
#   caught it, which is the gate working; basicly-z2wi is the same shape, where ten
#   successful dispatches are what disabled the calibration. Anything derived from
#   the dispatch record is only ever true as of the last dispatch.
#
# Two consequences, so neither is rediscovered. This rule is a RATCHET: it can only
# ever be dragged up, by whichever lane declared the widest scope and finished. And
# because the estimate is a function of the lane's own `## Scope`, a lane that widens
# its scope mid-flight — as this one did, from three globs to nine — raises the
# global ceiling for every future lane without anyone deciding to. That is tracked
# separately (basicly-qorx); it is a property of deriving a shared constant from
# per-bead self-declared data, not of this number.
#
# What the number is, precisely: a SEPARATING boundary — it must admit every size
# observed to complete and refuse every size observed to die that no larger success
# explains away. Both halves are live in
# `test_the_ceiling_separates_the_sizes_that_completed_from_the_sizes_that_failed`,
# which fails and names the required value when the constant and the record disagree.
#
# The upper half currently has nothing to refuse, and that is a finding rather than
# an omission: basicly-kjc5.42 and basicly-kjc5.44 declare the identical class and
# the identical scope, and one completed while the other was SIGTERMed. No function
# of (class, scope) can separate that pair, so no ceiling can be credited with
# refusing kjc5.44 — the previous derivation appeared to only because the
# completed-side query dropped kjc5.42's success on the `scope_tokens` filter that
# basicly-ipx2 had just removed from the failure side. Two prior paragraphs here have
# now been artifacts of an optional field being filtered on; the third fix is
# `RunRecord.context_tokens`, which measures the quantity instead of re-deriving it.
#
# 132_000, derived 2026-08-03 from basicly-tcmy.5 at 130_780. This is the second
# derivation that is real evidence rather than a measure defect, and it is the
# ratchet above firing again with a consequence the previous instance did not have:
# tcmy.5 widened its own scope mid-flight from the eight globs it was ADMITTED on
# (66_780) to sixteen (130_780), completed the work, and its finishing record then
# failed the separating gate. Because every lane in a supervised pass shares one
# `.beads` through the redirect, that failure was not local — the gate asserts over
# the whole tracker, so basicly-tcmy.6 and basicly-tcmy.22 failed verify on tcmy.5's
# declaration and each was charged a rework attempt for a defect in neither diff.
# That cross-lane blast radius is filed on basicly-qorx alongside the ratchet.
#
# Two things this number is NOT, stated because both are tempting. It is not a
# judgment that a 132_000 lane is a good idea: the same lane cost 15_752_919 tokens,
# and the band is a context bound, not a spend bound (the spend gate is the grant).
# And it is not evidence that the ceiling's premise held — the premise is that a
# lane this size cannot run, and this one ran. What licenses 132_000 is exactly that
# completion, which is the rule at the top of this block applied honestly; what the
# rule cannot do is stop a lane from moving it.
#
# 200_000, derived 2026-08-08 from basicly-u2hl.14 at 197_646. **Third instance of the
# same ratchet, and the first where the widening was the operator's rather than the
# lane's.** u2hl.14 was admitted on a 13-entry scope (78_709), and that scope was wrong:
# it named the ten source modules the naming gate covers and omitted the ten test files
# the gate obliges the lane to *create*, so the merge gate refused the landing twice for
# editing ground it had not declared. Correcting the declaration to the 27 paths the lane
# genuinely touches took the estimate to 197_646, and the completed record then failed
# the separating gate exactly as tcmy.5's did.
#
# So the honest reading is narrower than "a 197_646 lane runs fine". What moved is the
# *declaration*, not the work: the same diff was always this wide and the band was
# measuring an under-declared scope. That is an argument for the ceiling being a poor
# instrument here rather than for the lane being large — the band prices what a scope
# *reads*, and basicly-esxp carries the case that read-cost has not once predicted change
# cost on this repo. Until that lands this number keeps moving whenever a scope is
# corrected upward, which is a property worth stating rather than a fact worth trusting.
#
# 248_000, derived the same day, from the same bead, at 245_466 — and the second raise
# inside one landing is the evidence, not an embarrassment to be smoothed over. Nothing
# about the change grew between 197_646 and 245_466: the landing's scope-collision gate
# named eight further paths the diff touched, declaring them satisfied that gate, and
# declaring them is what moved the estimate. **One field was serving two masters.** A
# `## Scope` entry was read both as "the ground this lane owns" (collision detection,
# which wants the declaration complete) and as "the material this lane reads" (the band,
# which prices it), so declaring honestly for the first necessarily inflated the second.
#
# basicly-efw2 split them: the band now prices a bead's `## Working Set` when it declares
# one, and `## Scope` only as the fallback (`decompose.WORKING_SET_HEADING`). Two things
# follow for this block. **Every derivation above measured declaration completeness, not
# working set** — the trend is not evidence that lanes are getting bigger, and none of
# these numbers may be cited as a lane size. And a raise is no longer the answer to a
# refusal a corrected declaration caused: the lane declares what it must read instead,
# which leaves this constant answerable to lanes that really are that large.
DEFAULT_WORKING_SET_MIN = 8_000
DEFAULT_WORKING_SET_MAX = 256_000
# Per-task-class multiplier on scope read-cost. Seeds, and they stay seeds: the
# telemetry calibration that once overwrote them measured whole-lane spend, which is
# a different quantity from a working set, and basicly-z2wi removed it. An unlisted
# class uses the task seed (the most conservative of the three).
DEFAULT_BUILD_FACTOR_SEEDS = {"task": 3.0, "bug": 2.0, "chore": 1.5}
DEFAULT_BUILD_FACTOR = 3.0
DEFAULT_CALIBRATION_MIN_SAMPLES = 10
DEFAULT_CALIBRATION_WINDOW = 50
# Fraction of the runner's context window a dispatch's final occupancy is reported
# against (basicly-kjc5.6). Observability since D23, never a fill target: it fired at a
# fifth of its intended point for months and has no correct firing on record, so the
# number is kept, recorded and surfaced rather than retuned a third time.
DEFAULT_CONTEXT_CEILING = 0.6
# Quantile of recent measured lane actuals used to bound a lane whose scope cannot be
# read (basicly-jr0l.58). A *ceiling* wants a high quantile, not a central estimate:
# the median it replaced was exceeded by 47% of this repo's own 17 recorded lane
# actuals, which is what let a pass forecast at 16316972 tokens spend 43599830. At 0.9
# the target is that no more than one lane in ten exceeds its bound.
DEFAULT_UNSIZED_LANE_QUANTILE = 0.9

# The three human checkpoints the loop enforces (architecture §12.2).
CHECKPOINTS = ("classify", "decompose", "ship")

# The loop's phases, in the order they run (architecture §12.2). ``done`` is the
# terminal state, not a phase with a transition out of it, so it is absent. Named
# here rather than in ``loop`` so ``[policy.evidence]`` can be validated against
# the same set ``loop._HANDLERS`` dispatches on — the jr0l.51 stance: a rename
# must not be able to desynchronise a validator from the thing it validates, and
# a test pins the two together. ``validate`` sits after ``verify`` per D1: the two
# are sequential states, and its gate binds at L3 only (basicly-u2hl.54.1).
LOOP_PHASES = ("intake", "classify", "decompose", "build", "verify", "validate", "ship")

# Autonomy levels for the session grant ledger (factory design D3,
# basicly-kjc5.3), lowest to highest. [policy] autonomy is the repo's grantable
# ceiling; the default keeps every checkpoint human (today's behavior).
AUTONOMY_LEVELS = ("L0", "L1", "L2", "L3")
DEFAULT_AUTONOMY = "L0"

# Runaway-loop guard for the decider agent (factory design §6, basicly-kjc5.4):
# delegated answers per session beyond this are human-only.
DEFAULT_DECIDER_MAX_DECISIONS = 50

# Sanity bound on a lane's sequential sub-task beads (factory design §6/D7,
# basicly-kjc5.9). The sizing governor is the real limit; this only stops a
# runaway lane decomposition from turning one package into dozens of dispatches.
DEFAULT_MAX_SUBTASKS_PER_LANE = 10

# What a landing does when a lane's committed changes reach outside its declared
# ``## Scope`` *and* into another live lane's declared scope (basicly-jr0l.44).
# The out-of-scope paths are recorded as evidence either way; this governs only
# the collision, which is the case that actually produces a merge conflict later.
# "block" refuses before the merge (deterministic checks are authoritative);
# "warn" lands anyway, for a repo whose plans are routinely incomplete and would
# rather pay the conflict than the rework cycle.
SCOPE_COLLISION_POLICIES = ("block", "warn")
DEFAULT_SCOPE_COLLISION = "block"

# The fixed br work classes the classifier may assign (architecture §12.1).
# bug/chore are leaf tracks; task/feature/epic nest fractally.
WORK_TYPES = ("bug", "chore", "task", "feature", "epic")


@dataclass(frozen=True)
class ProjectPaths:
    """Resolved paths used by the projector CLI."""

    core_fragments_dir: Path
    overlay_fragments_dirs: tuple[Path, ...]
    targets_dir: Path
    templates_dir: Path
    manifest_path: Path
    legacy_fragments_dir: Path

    @property
    def core_root(self) -> Path:
        """Root of the managed core catalog, derived from the fragments dir.

        Every command that touches the core tree (init materialization, hooks
        projection) must use this single notion so a custom `core_fragments`
        in basicly.toml relocates the whole catalog consistently.
        """
        return self.core_fragments_dir.parent

    @property
    def state_path(self) -> Path:
        """Install provenance file, sibling of the core root (§9).

        `.basicly/core` -> `.basicly/state/install.json`; follows a custom
        core location the same way the core root itself does.
        """
        return self.core_root.parent / "state" / "install.json"


_OPEN_TABLE = Table(open_keys=True)

_VERIFY_CHECK_TABLE = Table(
    keys=frozenset({"name", "command", "modes", "staged_suffix", "fix_command"})
)

# One lane's contribution to a ratchet, in its own basicly.d fragment. `frozen` is open
# because its keys are the ratcheted entries themselves — a module path, a ruff rule code —
# which no vocabulary at this layer could enumerate.
_RATCHET_TABLE = Table(keys=frozenset({"count_delta"}), tables={"frozen": _OPEN_TABLE})

_RUNNER_AGENT_TABLE = Table(
    keys=frozenset({
        "name",
        "command",
        "prompt_via",
        "model",
        "tier",
        "vendor",
        "sandbox",
        "approval",
        "deny_style",
        "git_name",
        "git_email",
        "usage_format",
        "context_window",
    })
)

_SIZING_TABLE = Table(
    keys=frozenset({
        "working_set_min",
        "working_set_max",
        "calibration_min_samples",
        "calibration_window",
        "context_ceiling",
        "unsized_lane_quantile",
    }),
    tables={"build_factor": _OPEN_TABLE},
)

# Every section and key basicly.toml / basicly.local.toml may declare (basicly-1piy).
#
# An allowlist rather than a denylist of known-bad names, because a denylist is
# silent on exactly the case that produced this bead: a key nobody thought to list.
# The two entries here whose reader is not this module are the reason a denylist
# could not work either way — `[[verify.checks]]` is re-parsed by the pre-commit
# hook runner and `[[privacy.denied]]` is read *only* by
# `.basicly/core/hooks/internal-info-scan.py`, so a schema derived from this
# module's own loaders would have started rejecting a working machine-local gate.
#
# FORWARD COMPATIBILITY — the stance, and why this one:
#
# An unrecognised name is a hard error, in both files, with no warn-then-error
# staging and no near-miss narrowing. The cost is real and accepted: a repo pinned
# to an older basicly whose config carries a key added since fails every command
# until it upgrades the engine or removes the key.
#
# That is the correct answer rather than a regression. An engine that cannot honour
# a key and runs anyway leaves the config stating one behaviour and the engine
# performing another, with no diff to review — which is this bead, and is the same
# reasoning `[runner] context_windows` (basicly-23ep) and `[catalog] technologies`
# already ship. A declaration whose only symptom is the silent default it was
# written to replace is the defect it was meant to fix.
#
# The two alternatives were weighed and rejected:
#
# * Warn-then-error. A warning line is what gets skimmed, and the reported incident
#   already had a visibly wrong number in the output — `forecast: ... all 5 lanes`
#   after the override was written to make it 2 — which was read straight past. A
#   warning would have been read the same way. Staging also needs a release to
#   graduate, and this engine ships from `main`, so the warn phase would have no
#   defined end.
# * Erroring only on a near-miss of a known key. It would have caught this bead's
#   own case, but it leaves a genuinely novel key silent, which is the same hole one
#   generation on, and it makes the gate's coverage depend on a distance threshold
#   no author can predict from their own typo.
#
# What makes the strict stance survivable is the message: it names the file, the
# name, the sections that do accept it, and this engine's version, so the
# version-skew case is diagnosed by the failure itself rather than investigated.
CONFIG_SCHEMA: dict[str, Table] = {
    "paths": Table(
        keys=frozenset({"core_fragments", "overlay_fragments", "targets", "templates", "manifest"})
    ),
    "catalog": Table(keys=frozenset({"technologies", "rank1_floor", "rank1_floor_high_water"})),
    "worktree": Table(
        keys=frozenset({"base_branch", "concurrency", "append_only_paths"}),
        # Open because its keys are the generated paths; `_regenerate_commands` validates.
        tables={"regenerate_commands": _OPEN_TABLE},
    ),
    "verify": Table(arrays={"checks": _VERIFY_CHECK_TABLE}),
    # Per-lane ratchet deltas, composed by :func:`basicly.dropin.compose` and read by the
    # gates under `.scripts/`, never by this module — the same reason [[privacy.denied]] is
    # here. A gate whose baseline this schema refused to carry would have to parse the
    # fragments itself, which is how two readers of one convention start disagreeing.
    "ratchet": Table(
        tables={
            "comment_density": _RATCHET_TABLE,
            "module_size": _RATCHET_TABLE,
            "noqa_debt": _RATCHET_TABLE,
        }
    ),
    "policy": Table(
        keys=frozenset({
            "required_gates",
            "max_rework",
            "autonomy",
            "notify_command",
            "decider_max_decisions",
            "max_subtasks_per_lane",
            "max_downstream_wip",
            "scope_collision",
        }),
        tables={"evidence": _OPEN_TABLE, "sizing": _SIZING_TABLE},
    ),
    "runner": Table(
        keys=frozenset({
            "default",
            "decider",
            "runner_timeout",
            "max_agent_processes",
            "stall_after",
            "quiet_after",
            "lane_log_sessions",
            "default_tier",
            "copilot_session_store",
        }),
        tables={"context_windows": _OPEN_TABLE},
        arrays={"agents": _RUNNER_AGENT_TABLE},
    ),
    # Which step of the work-tracker cutover this repo is on (basicly-vkh0.19).
    # Read by :func:`load_tracker_mode` and acted on inside `basicly.br`.
    "tracker": Table(keys=frozenset({"mode"})),
    # Read by .basicly/core/hooks/internal-info-scan.py, never by this module: the
    # denylist is machine-local by design, so the only file it can live in is the
    # gitignored overlay this schema also governs.
    "privacy": Table(arrays={"denied": Table(keys=frozenset({"name", "token"}))}),
}

_ROOT_TABLE = Table(tables=CONFIG_SCHEMA)


def _validation_schema(repo_root: Path) -> Table:
    """The root table *repo_root*'s config is checked against (basicly-69az).

    A tree that ships its own ``src/basicly/config.py`` is checked against *that*
    schema, not against the schema of whichever engine happens to be running. The
    landing is why: ``loop advance`` runs from the base checkout, so the process
    validating a lane's ``basicly.toml`` is the pre-merge engine, and a lane whose
    single commit adds a ``CONFIG_SCHEMA`` entry *and* declares it could not land —
    the file was refused for a name the code beside it introduces. Resolving the
    schema from the tree under test asks the only question that matters: will the
    engine this config ships with honour the name?

    It does not weaken basicly-1piy. In a checkout with no schema change the tree's
    schema *is* the running engine's, so a typo is refused exactly as before; in a
    consumer repo there is no engine source and nothing changes at all.
    """
    schema = tree_schema.read(repo_root)
    return _ROOT_TABLE if schema is None else Table(tables=schema)


def _config_documents(repo_root: Path) -> dict[str, dict]:
    """Every config file that exists, parsed, keyed by filename, lowest layer first.

    The drop-in fragments sit between the two files: each carries one lane's own additions
    (:mod:`basicly.dropin`), while basicly.local.toml stays the top layer because it is the
    machine's override of whatever the tree declares.
    """
    documents: dict[str, dict] = {}
    base = repo_root / CONFIG_FILE
    if base.exists():
        documents[CONFIG_FILE] = tomllib.loads(base.read_text(encoding="utf-8"))
    documents.update(dropin.documents(repo_root))
    local = repo_root / LOCAL_CONFIG_FILE
    if local.exists():
        documents[LOCAL_CONFIG_FILE] = tomllib.loads(local.read_text(encoding="utf-8"))
    return documents


def unknown_config_keys(repo_root: Path) -> list[str]:
    """Every name in the config files this engine does not recognise (basicly-1piy).

    One message per offending name, each naming the file, the containing section,
    what that section does accept, and — the part that turns a refusal into a fix —
    which sections accept a name like it. Empty when both files are clean.

    Public because ``basicly loop preflight`` reports it as a blocker rather than
    letting the loaders raise mid-report: preflight's whole contract is a checklist
    ending in a verdict, and an exception thrown out of the middle of it answers
    none of the remaining questions.
    """
    return _problems(_config_documents(repo_root), _validation_schema(repo_root))


@dataclass(frozen=True)
class _Pass:
    """What stays constant while one config file is walked: the file, and its schema.

    The root table is an argument rather than the module global because it is
    resolved per tree (:func:`_validation_schema`), and it has to reach the message
    as well as the walk — a refusal lists the file's sections and says where else
    the name would be accepted, both of which are answers about *that* schema.
    """

    filename: str
    root: Table


def _problems(documents: dict[str, dict], root: Table) -> list[str]:
    """Every name across already-parsed *documents* that *root*'s schema rejects."""
    return [
        problem
        for filename, data in documents.items()
        for problem in _unknown_in_table(_Pass(filename, root), data, root, "")
    ]


def _unknown_in_table(walk: _Pass, table: dict, schema: Table, path: str) -> list[str]:
    """Recursively collect the names *table* declares that *schema* does not accept."""
    problems: list[str] = []
    for name, value in table.items():
        child = f"{path}.{name}" if path else str(name)
        if name in schema.tables and isinstance(value, dict):
            problems += _unknown_in_table(walk, value, schema.tables[name], child)
        elif name in schema.arrays and isinstance(value, list):
            for entry in value:
                if isinstance(entry, dict):
                    problems += _unknown_in_table(walk, entry, schema.arrays[name], child)
        elif name in schema.keys or name in schema.tables or name in schema.arrays:
            # A recognised name carrying the wrong TOML type: the loader that reads
            # it decides what to do with that, and every one of them either falls
            # back with a documented stance or raises. Not this pass's question.
            continue
        elif not schema.open_keys:
            problems.append(_unknown_message(walk, name, value, schema, path))
    return problems


def _unknown_message(walk: _Pass, name: object, value: object, schema: Table, path: str) -> str:
    """The refusal for one unrecognised *name*, including where it would be accepted."""
    kind = "section" if isinstance(value, dict) else "key"
    if path:
        accepted = ", ".join(sorted(schema.keys | set(schema.tables) | set(schema.arrays)))
        message = (
            f"{walk.filename}: unknown {kind} {name!r} in [{path}]; [{path}] accepts {accepted}"
        )
    else:
        sections = ", ".join(f"[{section}]" for section in sorted(walk.root.tables))
        message = f"{walk.filename}: unknown {kind} {name!r}; this file's sections are {sections}"

    hints = _accepting_clause(name, "", walk.root)
    # A misplaced *section* is the reported failure — `[loop] concurrency` written
    # for `[worktree] concurrency` — and there the name worth locating is the one
    # inside it, not the section that has no home at all.
    if isinstance(value, dict):
        hints += [clause for key in value for clause in _accepting_clause(key, "its ", walk.root)]
    if hints:
        message += " - " + "; ".join(hints)
    return message


def _accepting_clause(name: object, prefix: str, root: Table) -> list[str]:
    """``["its 'concurrency' is accepted in [worktree]"]``, or empty if nothing accepts it."""
    where = _accepting(name, root)
    return [f"{prefix}{name!r} is accepted in {', '.join(where)}"] if where else []


def _accepting(name: object, root: Table) -> list[str]:
    """Rendered paths of every schema table that accepts a key called *name*."""
    found: list[str] = []

    def walk(schema: Table, path: str, array: bool) -> None:
        if name in schema.keys:
            found.append(f"[[{path}]]" if array else f"[{path}]")
        for child, table in schema.tables.items():
            walk(table, f"{path}.{child}" if path else child, False)
        for child, table in schema.arrays.items():
            walk(table, f"{path}.{child}" if path else child, True)

    walk(root, "", False)
    return sorted(found)


def _validated_documents(repo_root: Path) -> dict[str, dict]:
    """:func:`_config_documents`, refusing any file that declares a name we cannot honour.

    The schema comes from :func:`_validation_schema`, so a basicly source checkout is
    judged by the schema *it* ships. When that tree ships a schema this reader could
    not parse, the refusal falls back to the running engine's — and says so, naming
    the ordering rule, because that fallback is the one case where the refusal may be
    about nothing worse than a lane being one commit ahead (basicly-69az).
    """
    documents = _config_documents(repo_root)
    if problems := _problems(documents, _validation_schema(repo_root)):
        unreadable = (
            tree_schema.ships_engine_source(repo_root) and tree_schema.read(repo_root) is None
        )
        raise ValueError(
            "\n".join(problems)
            + f"\nbasicly {__version__} refuses a config name it cannot honour rather than "
            "ignoring it, because an ignored key leaves the file stating one behaviour and "
            "the engine performing another. Remove or correct the name, or upgrade basicly "
            "if it comes from a newer version."
            + (f"\n{tree_schema.ORDERING_RULE}" if unreadable else "")
        )
    return documents


def _harness_section(repo_root: Path, name: str) -> dict:
    """The named harness section, with later layers overriding earlier ones.

    Three layers, lowest first: basicly.toml, the gitignored basicly.local.toml,
    and this process's session overrides (:mod:`basicly.session`).

    Key-level shallow merge: a key set in a later layer replaces the same key
    wholesale (so a local ``checks`` or ``agents`` list is taken as-is, not
    concatenated). A missing file or a non-table section contributes nothing. Only
    harness sections go through this merge — projection config ([paths],
    [catalog]) reads basicly.toml alone.

    Both files are schema-checked here, on every load and whichever section is
    asked for: the whole point of basicly-1piy is that a key in a section this
    caller never reads is exactly the one that goes unnoticed.
    """
    merged: dict = {}
    for data in _validated_documents(repo_root).values():
        section = data.get(name, {})
        if isinstance(section, dict):
            merged.update(section)
    merged.update(session.overrides_for(name))
    return merged


def load_tracker_mode(repo_root: Path) -> str:
    """Which step of the work-tracker cutover ``[tracker] mode`` declares.

    The ladder is `docs/requirements/work-tracker.md` §5 and its values are
    :data:`basicly.br.TRACKER_MODES`; the module that acts on the answer owns the
    vocabulary, so there is one spelling of ``dual`` in the engine.

    Absent means :data:`basicly.br.DEFAULT_TRACKER_MODE` — the pre-cutover behaviour,
    which is what a consumer who has never heard of the owned tracker must get.

    An unrecognised value is refused rather than defaulted, for the reason
    :data:`CONFIG_SCHEMA` refuses an unrecognised *name*: a mode the engine cannot
    honour leaves the file stating one behaviour and the engine performing another,
    and here the two behaviours differ in which store answers a read.

    Raises:
        ValueError: ``mode`` is set to something outside :data:`basicly.br.TRACKER_MODES`.
    """
    mode = _harness_section(repo_root, "tracker").get("mode")
    if mode is None:
        return br.DEFAULT_TRACKER_MODE
    if mode not in br.TRACKER_MODES:
        raise ValueError(
            f"[tracker] mode = {mode!r} is not one of {', '.join(br.TRACKER_MODES)}; "
            f"the work-tracker cutover has no other step (docs/requirements/work-tracker.md §5)"
        )
    return mode


# The engine's answer to "which store is authoritative" is installed into the seam
# that acts on it, rather than imported by it. `basicly.br` cannot import this module:
# `config -> runner -> run_record -> br` already runs the other way, so the import
# would close a cycle. :func:`basicly.br.set_mode_reader` documents the inversion; this
# is the one line that performs it, and `tests/test_br_seam.py` asserts that importing
# this module is what puts the seam in a repo's declared mode.
br.set_mode_reader(load_tracker_mode)


@dataclass(frozen=True)
class WorktreeConfig:
    """Settings for sibling git-worktree isolation."""

    # None means "fork from the branch currently checked out".
    base_branch: str | None
    concurrency: int
    # The paths this repo's conventions have every lane append its own entry to
    # (basicly-o8p0). Declared once here rather than per bead, because a per-bead
    # declaration cannot work for a path every bead touches and no bead mentions:
    # `CHANGELOG.md` appeared in no `## Scope`, so it was invisible to decompose's
    # grouping and to preflight, and three lanes with provably disjoint scopes
    # discovered it as a rebase conflict in the merge queue instead.
    #
    # Named ``append_only_paths`` and not ``shared_paths`` on purpose: a plan's
    # ``shared`` declaration *removes* a serialization edge, and this list *adds*
    # one, so the two must not read as the same word.
    #
    # Empty by default, so the mechanism is inert until a consumer names a path.
    append_only_paths: tuple[str, ...] = ()
    # The artifacts every lane's edit regenerates and no bead declares — the second
    # variety of the class above (basicly-lyro) — each mapped to the rebuild that
    # produces it, run in the lane's worktree with the stopped rebase resolved.
    # `.basicly/generated-manifest.json` collides for the same reason
    # `CHANGELOG.md` does, but none of that list's remedies fit: the conflict is not
    # semantic (the file is a function of the tree), so serializing the lanes buys
    # nothing, giving one lane the entry is meaningless because every lane's edit
    # legitimately changes it, and a union merge would corrupt it outright — it is
    # JSON, not a line-oriented log. The remedy is to rebuild it once on the merged
    # tree, so this *removes* a bounce rather than adding a serialization edge, and
    # must not be folded into `append_only_paths`.
    #
    # Keyed by path and not one repo-wide argv (basicly-3w51): the second artifact is a
    # marked block in `docs/plan/implementation-plan.md`, which `basicly build` cannot
    # write, so one command rebuilt one artifact and was a no-op for the other.
    regenerate_commands: dict[str, tuple[str, ...]] = field(default_factory=dict)


def load_worktree_config(repo_root: Path) -> WorktreeConfig:
    """Load ``[worktree]`` settings (basicly.toml + local overlay), with defaults."""
    defaults = WorktreeConfig(base_branch=None, concurrency=DEFAULT_WORKTREE_CONCURRENCY)

    section = _harness_section(repo_root, "worktree")

    base = section.get("base_branch")
    base_branch = base.strip() if isinstance(base, str) and base.strip() else None

    concurrency = section.get("concurrency")
    if not (isinstance(concurrency, int) and not isinstance(concurrency, bool) and concurrency > 0):
        concurrency = defaults.concurrency

    raw_paths = section.get("append_only_paths")
    append_only = _append_only_paths(raw_paths) if isinstance(raw_paths, list) else ()

    raw_generated = section.get("regenerate_commands")
    regenerate = _regenerate_commands(raw_generated) if isinstance(raw_generated, dict) else {}

    return WorktreeConfig(
        base_branch=base_branch,
        concurrency=concurrency,
        append_only_paths=append_only,
        regenerate_commands=regenerate,
    )


# Glob metacharacters a ``[worktree]`` path declaration may not contain (the set
# ``decompose.globs_overlap`` acts on).
_PATH_LIST_WILDCARDS = "*?["

# Why each ``[worktree]`` path list refuses a glob. Both refuse, for mirror-image
# reasons, and the message has to carry the right one or the author cannot tell which
# rule they hit from the failure alone.
_GLOB_REFUSALS = {
    "append_only_paths": (
        "an append-only path must be one literal path, because this list adds "
        "serialization edges and a wildcard would serialize every lane over a subtree "
        "nobody can name"
    ),
    "regenerate_commands": (
        "a generated path must be one literal path, because this table authorises the "
        "engine to overwrite both sides of a conflict on it, and a wildcard would extend "
        "that authority over source files nobody listed"
    ),
}


def _append_only_paths(entries: list) -> tuple[str, ...]:
    """The validated ``[worktree] append_only_paths`` list, blanks dropped.

    A glob is refused for the mirror of the reason ``decompose._parse_shared``
    refuses one — that list *removes* serialization edges and this one *adds* them,
    so a wildcard here would serialize every child against every other over a
    subtree nobody can enumerate, and the plan would collapse for no nameable path.
    """
    return _literal_paths(entries, "append_only_paths")


def _literal_paths(entries: list, key: str) -> tuple[str, ...]:
    """The validated ``[worktree] <key>`` literal-path list, blanks dropped.

    Raises rather than dropping what it cannot honour, on the same grounds as the
    unknown-name refusal above: a silently ignored entry leaves the file naming a
    path the engine does not act on, and the only symptom is the merge-queue conflict
    the declaration exists to handle. An empty string names nothing, so it is the one
    entry that can be dropped without hiding a declaration.
    """
    paths: list[str] = []
    for entry in entries:
        if not isinstance(entry, str):
            raise ValueError(
                f"[worktree] {key} entries must be paths, got {type(entry).__name__}: {entry!r}"
            )
        path = entry.strip()
        if not path:
            continue
        if any(char in path for char in _PATH_LIST_WILDCARDS):
            raise ValueError(f"[worktree] {key} entry {path!r} is a glob; {_GLOB_REFUSALS[key]}")
        paths.append(path)
    return tuple(paths)


def _regenerate_commands(section: dict) -> dict[str, tuple[str, ...]]:
    """The validated ``[worktree.regenerate_commands]`` map of path to rebuild argv.

    Declared rather than inferred: how a repo rebuilds its artifacts is the repo's fact,
    and an engine that guessed would auto-resolve a conflict with a command nobody wrote
    down. Per path rather than once, because two artifacts in one repo are legitimately
    rebuilt by different commands and the wrong one is a silent no-op (basicly-3w51).

    An argv list and never a shell string: the rebuild runs unattended inside a lane's
    worktree, so the repo's "parameterize, never concatenate" rule applies to it. Blanks
    are dropped; a non-string entry and an empty argv are refused.
    """
    table = {path.strip(): argv for path, argv in section.items()}
    commands: dict[str, tuple[str, ...]] = {}
    for path in _literal_paths(list(table), "regenerate_commands"):
        label = f"[worktree.regenerate_commands] {path!r}"
        entries = table[path]
        if not isinstance(entries, list) or not all(isinstance(word, str) for word in entries):
            raise ValueError(f"{label} must be an argv list of strings, got {entries!r}")
        argv = tuple(word.strip() for word in entries if word.strip())
        if not argv:
            raise ValueError(
                f"{label} declares no command; the engine cannot rebuild an artifact it has "
                "no command for, so the declaration would silently do nothing and the "
                "conflict would still bounce the lane"
            )
        commands[path] = argv
    return commands


@dataclass(frozen=True)
class VerifyCheck:
    """A single configured verify check."""

    name: str
    command: tuple[str, ...]
    modes: frozenset[str]
    # When set and running in "staged" mode, run only against staged files with
    # this suffix (and skip when none are staged).
    staged_suffix: str | None = None
    # Deterministic, lossless repair for this check (e.g. a formatter's write
    # mode). Declared only where the repair is purely mechanical: the pre-commit
    # hook applies it to the staged files, and `basicly verify --fix` applies it
    # before the checks, so no cycle is ever spent on a repair a script can make.
    # The check itself always still runs — the fix never replaces the verdict.
    fix_command: tuple[str, ...] | None = None


@dataclass(frozen=True)
class VerifyConfig:
    """The consumer's configured verify checks."""

    checks: tuple[VerifyCheck, ...]

    def for_mode(self, mode: str) -> tuple[VerifyCheck, ...]:
        """Return the checks that participate in *mode*, in configured order."""
        return tuple(check for check in self.checks if mode in check.modes)


def load_verify_config(repo_root: Path) -> VerifyConfig:
    """Load ``[verify].checks`` (basicly.toml + basicly.d fragments + local overlay).

    A fragment's entries are **appended**, in filename order, which is the one place the
    layering concatenates rather than replaces: a fragment is one lane's own addition, so
    replacing would mean the last lane to land silently deleted every earlier lane's gate
    (basicly-ef7t). basicly.local.toml and a session override still replace the whole list,
    unchanged — a machine override of a repo's gates is not an addition to them.

    Returns an empty config when the files or section are absent. Raises ``ValueError`` on a
    malformed check entry rather than silently dropping it — a lost gate must never pass
    unnoticed.
    """
    declared: list[object] = []
    for filename, data in _validated_documents(repo_root).items():
        section = data.get("verify")
        checks = section.get("checks") if isinstance(section, dict) else None
        if isinstance(checks, list):
            declared = list(checks) if filename == LOCAL_CONFIG_FILE else [*declared, *checks]
    override = session.overrides_for("verify").get("checks")
    if isinstance(override, list):
        declared = list(override)
    return VerifyConfig(tuple(_parse_verify_check(entry) for entry in declared))


def _parse_verify_check(entry: object) -> VerifyCheck:
    if not isinstance(entry, dict):
        raise ValueError(f"[verify.checks] entry must be a table, got {type(entry).__name__}")

    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("[verify.checks] entry is missing a non-empty 'name'")

    command = entry.get("command")
    if not (isinstance(command, list) and command and all(isinstance(a, str) for a in command)):
        raise ValueError(f"verify check {name!r} needs a non-empty 'command' list of strings")

    modes = entry.get("modes")
    if not (isinstance(modes, list) and modes and all(isinstance(m, str) for m in modes)):
        raise ValueError(f"verify check {name!r} needs a non-empty 'modes' list of strings")
    unknown = [m for m in modes if m not in VERIFY_MODES]
    if unknown:
        raise ValueError(
            f"verify check {name!r} has unknown mode(s) {unknown}; allowed: {list(VERIFY_MODES)}"
        )

    staged_suffix = entry.get("staged_suffix")
    if staged_suffix is not None and not isinstance(staged_suffix, str):
        raise ValueError(f"verify check {name!r} 'staged_suffix' must be a string")

    fix_command = entry.get("fix_command")
    if fix_command is not None and not (
        isinstance(fix_command, list)
        and fix_command
        and all(isinstance(a, str) for a in fix_command)
    ):
        raise ValueError(f"verify check {name!r} 'fix_command' must be a non-empty list of strings")

    return VerifyCheck(
        name=name.strip(),
        command=tuple(command),
        modes=frozenset(modes),
        staged_suffix=staged_suffix or None,
        fix_command=tuple(fix_command) if fix_command else None,
    )


@dataclass(frozen=True)
class PolicyConfig:
    """Loop gate/checkpoint policy settings."""

    required_gates: tuple[str, ...]
    max_rework: int
    # The highest grant level this repo allows issuing (factory design D3);
    # autonomy is opt-in, so the default ceiling makes grants unissuable.
    autonomy: str = DEFAULT_AUTONOMY
    # Consumer command fired per new human-required decision (design 7.3), as
    # an argv list — the decision id and question are appended. Empty: disabled.
    notify_command: tuple[str, ...] = ()
    # Delegated decider answers allowed per session (design §6).
    decider_max_decisions: int = DEFAULT_DECIDER_MAX_DECISIONS
    # Sanity bound on the sub-task beads one lane may run in sequence (D7).
    max_subtasks_per_lane: int = DEFAULT_MAX_SUBTASKS_PER_LANE
    # Units allowed to stand downstream of BUILD unlanded (requirements 3.1).
    max_downstream_wip: int = DEFAULT_MAX_DOWNSTREAM_WIP
    # Per-phase evidence artifact declarations (basicly-m4zv.13): loop phase ->
    # repo-relative path that must exist and be non-empty before the loop may
    # advance past that phase. Empty by default, so the mechanism is inert until a
    # consumer declares something.
    evidence: dict[str, str] = field(default_factory=dict)
    # How the build->verify landing answers an out-of-scope edit that reaches into
    # another live lane's declared scope (basicly-jr0l.44).
    scope_collision: str = DEFAULT_SCOPE_COLLISION


def load_policy_config(repo_root: Path) -> PolicyConfig:
    """Load ``[policy]`` settings (basicly.toml + local overlay), with defaults."""
    defaults = PolicyConfig(required_gates=DEFAULT_REQUIRED_GATES, max_rework=DEFAULT_MAX_REWORK)

    section = _harness_section(repo_root, "policy")

    raw_gates = section.get("required_gates")
    if isinstance(raw_gates, list) and all(isinstance(g, str) for g in raw_gates):
        required_gates = tuple(g.strip() for g in raw_gates if g.strip())
    else:
        required_gates = defaults.required_gates

    max_rework = section.get("max_rework")
    if not (isinstance(max_rework, int) and not isinstance(max_rework, bool) and max_rework >= 0):
        max_rework = defaults.max_rework

    autonomy = section.get("autonomy")
    if not (isinstance(autonomy, str) and autonomy.strip() in AUTONOMY_LEVELS):
        autonomy = DEFAULT_AUTONOMY
    else:
        autonomy = autonomy.strip()

    raw_notify = section.get("notify_command")
    notify_command: tuple[str, ...] = ()
    if (
        isinstance(raw_notify, list)
        and raw_notify
        and all(isinstance(a, str) and a.strip() for a in raw_notify)
    ):
        notify_command = tuple(raw_notify)

    # Every declared entry is carried through as a string, including a nonsense
    # one. Dropping what this loader cannot make sense of would turn a typo into a
    # gate that silently does not apply, which is the one failure mode
    # [policy.evidence] exists to prevent; ``policy.evidence_status`` refuses an
    # unusable declaration instead, so a bad value blocks rather than disappears.
    raw_evidence = section.get("evidence")
    evidence = (
        {str(phase): str(path).strip() for phase, path in raw_evidence.items()}
        if isinstance(raw_evidence, dict)
        else {}
    )

    # Unlike [policy.evidence], an unrecognised value falls back to the default
    # rather than refusing: the strict half of this mechanism is the *evidence*,
    # which is recorded whatever the policy says, so a typo here cannot make a
    # landing look checked when it was not.
    raw_collision = section.get("scope_collision")
    scope_collision = (
        raw_collision.strip()
        if isinstance(raw_collision, str) and raw_collision.strip() in SCOPE_COLLISION_POLICIES
        else DEFAULT_SCOPE_COLLISION
    )

    return PolicyConfig(
        required_gates=required_gates,
        max_rework=max_rework,
        autonomy=autonomy,
        notify_command=notify_command,
        evidence=evidence,
        scope_collision=scope_collision,
        decider_max_decisions=_positive_int(
            section.get("decider_max_decisions"), DEFAULT_DECIDER_MAX_DECISIONS
        ),
        max_subtasks_per_lane=_positive_int(
            section.get("max_subtasks_per_lane"), DEFAULT_MAX_SUBTASKS_PER_LANE
        ),
        max_downstream_wip=_positive_int(
            section.get("max_downstream_wip"), DEFAULT_MAX_DOWNSTREAM_WIP
        ),
    )


@dataclass(frozen=True)
class SizingConfig:
    """Working-set sizing governor settings (factory design D8, basicly-kjc5.2)."""

    working_set_min: int
    working_set_max: int
    # Per-task-class multiplier on scope read-cost. Seeds, and they stay seeds —
    # nothing measures a working-set factor (basicly-z2wi).
    build_factors: dict[str, float]
    calibration_min_samples: int
    calibration_window: int
    # Which of ``build_factors`` a repo declared in ``[policy.sizing.build_factor]``
    # rather than inheriting from the seeds. Provenance recorded where it is known
    # instead of inferred later by comparing the value against the seed: a repo that
    # declares the seed's own number would read back as never having declared one, and
    # this feeds the source stamped on a dispatch record (basicly-tcmy.5).
    configured_build_factors: frozenset[str] = frozenset()
    # Fraction of the runner's context window a finished dispatch's occupancy is
    # reported against (basicly-kjc5.6; observability since D23).
    context_ceiling: float = DEFAULT_CONTEXT_CEILING
    # Quantile of recent lane actuals that bounds an unsizeable lane (basicly-jr0l.58).
    unsized_lane_quantile: float = DEFAULT_UNSIZED_LANE_QUANTILE


def load_sizing_config(repo_root: Path) -> SizingConfig:
    """Load ``[policy.sizing]`` settings (basicly.toml + local overlay), with defaults.

    Every key falls back to its D8 default on a missing or wrong-typed value
    (same stance as the other harness loaders). An inverted band (min >= max)
    would refuse every decomposition, so it falls back to the default band
    rather than wedging the engine.
    """
    section = _harness_section(repo_root, "policy").get("sizing")
    if not isinstance(section, dict):
        section = {}

    working_set_min = _positive_int(section.get("working_set_min"), DEFAULT_WORKING_SET_MIN)
    working_set_max = _positive_int(section.get("working_set_max"), DEFAULT_WORKING_SET_MAX)
    if working_set_min >= working_set_max:
        working_set_min = DEFAULT_WORKING_SET_MIN
        working_set_max = DEFAULT_WORKING_SET_MAX

    factors = dict(DEFAULT_BUILD_FACTOR_SEEDS)
    configured: set[str] = set()
    raw_factors = section.get("build_factor")
    if isinstance(raw_factors, dict):
        for task_class, value in raw_factors.items():
            number = isinstance(value, int | float) and not isinstance(value, bool)
            if isinstance(task_class, str) and task_class.strip() and number and value > 0:
                factors[task_class.strip()] = float(value)
                # Only an accepted entry counts as configured: a rejected one leaves
                # the seed in force, so calling it configured would misattribute the
                # number actually used.
                configured.add(task_class.strip())

    return SizingConfig(
        working_set_min=working_set_min,
        working_set_max=working_set_max,
        build_factors=factors,
        configured_build_factors=frozenset(configured),
        calibration_min_samples=_positive_int(
            section.get("calibration_min_samples"), DEFAULT_CALIBRATION_MIN_SAMPLES
        ),
        calibration_window=_positive_int(
            section.get("calibration_window"), DEFAULT_CALIBRATION_WINDOW
        ),
        context_ceiling=_window_fraction(section.get("context_ceiling")),
        unsized_lane_quantile=_quantile_fraction(section.get("unsized_lane_quantile")),
    )


def _quantile_fraction(value: object) -> float:
    """*value* when it is a usable quantile (0 < x <= 1), else the default.

    Same fallback stance as :func:`_window_fraction`. A quantile at or below 0 would
    bound every lane at the cheapest run ever recorded, which is a ceiling in name
    only — the failure this replaced (basicly-jr0l.58).
    """
    if isinstance(value, int | float) and not isinstance(value, bool) and 0 < value <= 1:
        return float(value)
    return DEFAULT_UNSIZED_LANE_QUANTILE


def _window_fraction(value: object) -> float:
    """*value* when it is a usable ceiling fraction (0 < x <= 1), else the default.

    Zero or negative would report every run as over the ceiling; above 1 can never
    report one — both are config mistakes, so they fall back rather than silently
    blinding the meter (same stance as the sizing band).
    """
    if isinstance(value, int | float) and not isinstance(value, bool) and 0 < value <= 1:
        return float(value)
    return DEFAULT_CONTEXT_CEILING


def _positive_int(value: object, default: int) -> int:
    """*value* when it is a positive int (bool excluded), else *default*."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return default


def _positive_float(value: object, default: float) -> float:
    """*value* as a float when it is a positive number (bool excluded), else *default*."""
    if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
        return float(value)
    return default


def load_technology_selection(repo_root: Path) -> frozenset[str] | None:
    """Load the ``[catalog] technologies`` selection from basicly.toml.

    Returns ``None`` when no selection is recorded (everything ships). Raises
    ``ValueError`` on a malformed or out-of-vocabulary selection — a typo that
    silently dropped catalog content must never pass unnoticed — and on an
    unrecognised name in *either* config file, including the overlay this loader
    does not itself read (basicly-1piy): `basicly check` reaches the loaders on
    this side, and it is where a bad overlay has to surface.
    """
    data = _validated_documents(repo_root).get(CONFIG_FILE)
    if data is None:
        return None

    section = data.get("catalog", {})
    if not isinstance(section, dict) or "technologies" not in section:
        return None

    raw = section["technologies"]
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ValueError("[catalog] technologies must be a list of strings")
    selection = frozenset(item.strip() for item in raw if item.strip())
    unknown = sorted(selection - TECHNOLOGIES)
    if unknown:
        raise ValueError(
            f"[catalog] technologies contains unknown value(s): {', '.join(unknown)} "
            f"(allowed: {', '.join(sorted(TECHNOLOGIES))})"
        )
    return selection


def _rate(section: Mapping[str, object], key: str) -> float | None:
    """A ``[catalog]`` rate key as a float in [0, 1], or None when absent.

    Strict rather than defaulting, on the same grounds as every other key in
    this file: a rank-1 floor that silently reads as 0 because it was written
    ``"0.8"`` is a gate that passes on any catalog at all, and a gate that
    cannot fail is indistinguishable from one that was deleted.
    """
    if key not in section:
        return None
    value = section[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"[catalog] {key} must be a number between 0 and 1")
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"[catalog] {key} must be between 0 and 1 (got {value})")
    return float(value)


def load_routing_floor(repo_root: Path) -> tuple[float | None, float | None]:
    """Load ``[catalog] rank1_floor`` and its high-water mark from basicly.toml.

    Returns ``(floor, high_water)``, either of which is ``None`` when
    undeclared. The pair exists so the floor can be *ratcheted*: `catalog lint`
    refuses a floor below the high-water mark, which makes lowering a threshold
    to green a regression a diff that says what it is instead of one that reads
    like maintenance.
    """
    data = _validated_documents(repo_root).get(CONFIG_FILE)
    if data is None:
        return None, None
    section = data.get("catalog", {})
    if not isinstance(section, dict):
        return None, None
    return _rate(section, "rank1_floor"), _rate(section, "rank1_floor_high_water")


def record_technology_selection(repo_root: Path, technologies: list[str]) -> None:
    """Record the technology selection as ``[catalog] technologies`` in basicly.toml.

    Rewrites the existing ``technologies`` line in place when a ``[catalog]``
    section already carries one; otherwise appends a fresh section. The rest of
    the (user-owned) file is left untouched — the result is parsed back before
    writing, and on an unsupported layout the file is left as-is and a
    ``ValueError`` names the manual edit to make instead.
    """
    config_path = repo_root / CONFIG_FILE
    wanted = sorted(set(technologies))
    rendered = "[" + ", ".join(f'"{tech}"' for tech in wanted) + "]"
    line = f"technologies = {rendered}\n"
    section = f"\n# Catalog technology selection (see docs: technology scoping).\n[catalog]\n{line}"

    if not config_path.exists():
        config_path.write_text(DEFAULT_CONFIG_TOML + section, encoding="utf-8")
        return

    original = config_path.read_text(encoding="utf-8")
    text = _splice_technologies(original, line, section)
    try:
        recorded = tomllib.loads(text).get("catalog", {}).get("technologies")
    except tomllib.TOMLDecodeError as exc:
        recorded = exc
    if not isinstance(recorded, list) or sorted(recorded) != wanted:
        raise ValueError(
            f"cannot record the technology selection in {CONFIG_FILE} (unsupported "
            f"[catalog] layout); set 'technologies = {rendered}' under [catalog] by hand"
        )
    config_path.write_text(text, encoding="utf-8")


def _splice_technologies(text: str, line: str, section: str) -> str:
    """Return *text* with the ``[catalog] technologies`` line replaced or added."""
    lines = text.splitlines(keepends=True)
    in_catalog = False
    header_index: int | None = None
    for index, current in enumerate(lines):
        stripped = current.strip()
        if stripped.startswith("["):
            in_catalog = stripped == "[catalog]"
            if in_catalog and header_index is None:
                header_index = index
        elif in_catalog and stripped.startswith("technologies"):
            lines[index] = line
            return "".join(lines)
    if header_index is not None:
        lines.insert(header_index + 1, line)
        return "".join(lines)
    return text.rstrip("\n") + "\n" + section


@dataclass(frozen=True)
class RunnerConfig:
    """Agent runner settings: the available adapters and the default selection."""

    specs: tuple[RunnerSpec, ...]
    default: str
    # Runner used for decider invocations (design 7.1); None falls back to the
    # session default.
    decider: str | None = None
    # Backstop hard kill per dispatch, in seconds (design section 6,
    # basicly-kjc5.7); a killed lane routes to the decision queue as a stall flag.
    # No longer the working bound (basicly-lpsf): `quiet_after` below and the
    # spend ceiling bind first, and this is what is left for the pathological case
    # neither can see — a process that hangs holding the pipe, or a stream that
    # stops while the process does not exit. Set it where it never fires in normal
    # operation; calibrating it against the work distribution is what made it kill
    # working lanes.
    runner_timeout: float = 3600.0
    # Ceiling on concurrently live agent processes across every class the engine
    # spawns (design section 6, component 8). One global number rather than
    # multiplicative per-level caps; the rule of thumb is 2x [worktree]
    # concurrency (one average helper per lane) and the bound is API/RAM, not CPU.
    max_agent_processes: int = DEFAULT_MAX_AGENT_PROCESSES
    # Seconds of no activity before a dispatch is *flagged* possibly-stuck to the
    # decision queue (design section 6). A flag, not a kill, and the earliest of
    # the three bounds on purpose: a human sees the wedge while intervening is
    # still their call, before `quiet_after` makes it terminal.
    stall_after: float = DEFAULT_STALL_AFTER
    # Seconds of a *silent event stream* before a dispatch is killed as wedged
    # (basicly-lpsf). Terminal, where `stall_after` only flags — and reachable at
    # all only because the dispatch's own stream is now read as it arrives
    # (basicly-rupz): an event is proof of life whether or not a file changed,
    # which is the question the git-state probe behind `stall_after` cannot answer.
    quiet_after: float = DEFAULT_QUIET_AFTER
    # Sessions of lane transcripts kept on disk before the oldest rotate away
    # (basicly-rrah). A bound rather than unbounded growth, because the directory
    # is the audit surface an operator greps and every pass adds a lane file per
    # dispatch.
    lane_log_sessions: int = DEFAULT_RETAINED_SESSIONS
    # Family fallback model tier (basicly-kjc5.59), used for an agent that
    # declares none. None means no tier is implied at all, which leaves the
    # dispatch unpinned exactly as before — a default tier here would silently
    # start pinning models for every existing consumer.
    default_tier: str | None = None


def load_runner_config(repo_root: Path) -> RunnerConfig:
    """Load ``[runner]`` settings, merging config overrides onto the built-in adapters.

    Reads basicly.toml plus the local overlay. Returns the built-in adapters
    with ``default = "auto"`` when the files or section are absent. Each
    ``[[runner.agents]]`` entry overrides a built-in by name or adds a new
    agent. Raises ``ValueError`` on a malformed entry rather than silently
    dropping it — a lost adapter must never pass unnoticed.
    """
    section = _harness_section(repo_root, "runner")

    default_tier = section.get("default_tier")
    if default_tier is not None and default_tier not in MODEL_TIERS:
        raise ValueError(
            f"[runner] default_tier {default_tier!r} is not a known model tier; "
            f"allowed: {list(MODEL_TIERS)}"
        )

    specs = {spec.name: spec for spec in BUILTIN_RUNNERS}
    raw_agents = section.get("agents")
    if isinstance(raw_agents, list):
        for entry in raw_agents:
            spec = _parse_runner_agent(entry)
            specs[spec.name] = spec

    _inject_copilot_deny_tools(specs)
    _inject_copilot_session_store(specs, section)
    _apply_context_windows(specs, section)
    _apply_default_tier(specs, default_tier)

    default = section.get("default")
    default = default.strip() if isinstance(default, str) and default.strip() else AUTO

    decider = section.get("decider")
    decider = decider.strip() if isinstance(decider, str) and decider.strip() else None

    raw_timeout = section.get("runner_timeout")
    if (
        not isinstance(raw_timeout, int | float)
        or isinstance(raw_timeout, bool)
        or raw_timeout <= 0
    ):
        raw_timeout = 3600.0
    timeout = float(raw_timeout)

    return RunnerConfig(
        specs=tuple(specs.values()),
        default=default,
        decider=decider,
        runner_timeout=timeout,
        max_agent_processes=_positive_int(
            section.get("max_agent_processes"), DEFAULT_MAX_AGENT_PROCESSES
        ),
        stall_after=_positive_float(section.get("stall_after"), DEFAULT_STALL_AFTER),
        quiet_after=_positive_float(section.get("quiet_after"), DEFAULT_QUIET_AFTER),
        lane_log_sessions=_positive_int(
            section.get("lane_log_sessions"), DEFAULT_RETAINED_SESSIONS
        ),
        default_tier=default_tier,
    )


def _inject_copilot_deny_tools(specs: dict[str, RunnerSpec]) -> None:
    """Fold the baseline deny-list into the copilot runner as ``--deny-tool`` specs.

    Invocation-time enforcement of the permissions.yaml deny-list for Copilot,
    which has no config-file deny (basicly-lqz5). Sourced from the same catalog
    manifest as the projected Claude deny (:mod:`basicly.permissions`), so the
    guardrail has one authoring home. A non-headless override under the name is
    left untouched — a handoff has no argv to carry flags.
    """
    spec = specs.get(COPILOT_RUNNER)
    if spec is None or spec.kind != HEADLESS:
        return
    deny = permissions.copilot_deny_specs(permissions.load_deny_rules())
    if deny:
        specs[COPILOT_RUNNER] = replace(spec, deny_tools=tuple(deny))


def _inject_copilot_session_store(specs: dict[str, RunnerSpec], section: dict) -> None:
    """Point the copilot runner at a non-default session store (basicly-2rn9).

    ``[runner] copilot_session_store`` overrides the base directory
    ``runner.extract_usage`` reads a dispatch's measured usage from. A
    ``[runner]`` key rather than a ``[[runner.agents]]`` one for two reasons: the
    section merges the gitignored ``basicly.local.toml`` overlay, which is the
    only place a machine-specific store path belongs, and redirecting one path
    should not force a consumer to restate the whole built-in adapter. A ``~`` is
    left in place — the reader expands it, so a portable value stays portable.
    Absent leaves the home-relative default.
    """
    value = section.get("copilot_session_store")
    spec = specs.get(COPILOT_RUNNER)
    if spec is None or not isinstance(value, str) or not value.strip():
        return
    specs[COPILOT_RUNNER] = replace(spec, session_store=Path(value.strip()))


def _parse_runner_agent(entry: object) -> RunnerSpec:
    if not isinstance(entry, dict):
        raise ValueError(f"[[runner.agents]] entry must be a table, got {type(entry).__name__}")

    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("[[runner.agents]] entry is missing a non-empty 'name'")

    command = entry.get("command")
    if not (isinstance(command, list) and command and all(isinstance(a, str) for a in command)):
        raise ValueError(f"runner agent {name!r} needs a non-empty 'command' list of strings")

    prompt_via = entry.get("prompt_via", "arg")
    if prompt_via not in PROMPT_VIA:
        raise ValueError(
            f"runner agent {name!r} has unknown prompt_via {prompt_via!r}; "
            f"allowed: {list(PROMPT_VIA)}"
        )

    model = entry.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ValueError(f"runner agent {name!r} has a 'model' that must be a non-empty string")
    model = model.strip() if isinstance(model, str) else None

    tier, vendor = _parse_model_tier(entry, name)

    # Optional sandbox/approval guardrail overrides (basicly-t0kt), injected as
    # `--sandbox <mode>` / `-a <policy>` by format_command. An explicit override
    # replaces the builtin default (e.g. codex's), so a null is not re-defaulted.
    sandbox = entry.get("sandbox")
    approval = entry.get("approval")
    for key, value in (("sandbox", sandbox), ("approval", approval)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"runner agent {name!r} has a {key!r} that must be a non-empty string")
    sandbox = sandbox.strip() if isinstance(sandbox, str) else None
    approval = approval.strip() if isinstance(approval, str) else None

    # Optional tool-deny wire form (basicly-kjc5.16). Needed by a custom agent
    # that wraps one of the big-3 CLIs: without it the decider has no confinement
    # overlay for that agent and refuses to dispatch it, so this is the escape
    # hatch that keeps autonomous deciding reachable behind a wrapper.
    deny_style = entry.get("deny_style")
    if deny_style is not None and deny_style not in DENY_STYLES:
        raise ValueError(
            f"runner agent {name!r} has unknown deny_style {deny_style!r}; "
            f"allowed: {list(DENY_STYLES)}"
        )

    # Optional opt-in bot git identity (basicly-smzg): both keys or neither.
    git_name = entry.get("git_name")
    git_email = entry.get("git_email")
    for key, value in (("git_name", git_name), ("git_email", git_email)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"runner agent {name!r} has a {key!r} that must be a non-empty string")
    git_name = git_name.strip() if isinstance(git_name, str) else None
    git_email = git_email.strip() if isinstance(git_email, str) else None
    if (git_name is None) != (git_email is None):
        raise ValueError(
            f"runner agent {name!r} must set both 'git_name' and 'git_email' or neither "
            "(a bot git identity needs a name and an email)"
        )

    # Optional usage-report format for token telemetry (basicly-kjc5.1). An
    # entry replaces a builtin wholesale (no re-defaulting, same stance as
    # sandbox/approval), so an override of claude/codex must restate the format
    # to keep exact usage extraction; absent falls back to the chars/4 estimate.
    usage_format = entry.get("usage_format")
    if usage_format is not None and usage_format not in USAGE_FORMATS:
        raise ValueError(
            f"runner agent {name!r} has unknown usage_format {usage_format!r}; "
            f"allowed: {list(USAGE_FORMATS)}"
        )

    context_window, context_window_source = _context_window(entry, name)

    return RunnerSpec(
        name=name.strip(),
        kind=HEADLESS,
        command=tuple(command),
        prompt_via=prompt_via,
        model=model,
        tier=tier,
        vendor=vendor,
        tier_source=AGENT_TIER if tier is not None else None,
        deny_style=deny_style,
        sandbox=sandbox,
        approval=approval,
        git_name=git_name,
        git_email=git_email,
        usage_format=usage_format,
        context_window=context_window,
        context_window_source=context_window_source,
    )


def _apply_default_tier(specs: dict[str, RunnerSpec], default_tier: str | None) -> None:
    """Fold ``[runner] default_tier`` onto every spec that declares no tier.

    Applied to the spec, not passed at dispatch: every call site that invokes a
    runner then honours the default for free, and one added later cannot forget
    to thread it. A spec with its own ``tier`` keeps it — most specific wins — and
    a spec pinning an explicit ``model`` is left alone because the pin would win
    anyway, so giving it a tier would only misreport its provenance.
    """
    if default_tier is None:
        return
    for name, spec in specs.items():
        if spec.tier is None and spec.model is None:
            specs[name] = replace(spec, tier=default_tier, tier_source=FAMILY_DEFAULT_TIER)


def _parse_model_tier(entry: dict, name: str) -> tuple[str | None, str | None]:
    """The entry's portable model ``tier`` and resolving ``vendor`` (basicly-kjc5.59).

    Validated at load rather than at dispatch: a typo in a tier name would
    otherwise surface only once a lane was already running, and the point of a
    tier is that the dispatch never has to guess. The vendor is only meaningful on
    a multi-vendor surface, so it is checked for shape and left to
    ``models.FAMILY_MODEL_SURFACES`` to default.
    """
    tier = entry.get("tier")
    if tier is not None and tier not in MODEL_TIERS:
        raise ValueError(
            f"runner agent {name!r} has unknown model tier {tier!r}; allowed: {list(MODEL_TIERS)}"
        )
    vendor = entry.get("vendor")
    if vendor is not None and (not isinstance(vendor, str) or not vendor.strip()):
        raise ValueError(f"runner agent {name!r} has a 'vendor' that must be a non-empty string")
    return tier, vendor.strip() if isinstance(vendor, str) else None


def untiered_metered_runners(config: RunnerConfig, *, repo_root: Path | None = None) -> list[str]:
    """Every runner that would bill a metered dispatch to a model nobody named.

    The falsifier for a tier declaration (basicly-tcmy.35). An *absent* tier is
    invisible to every other gate here, and that is not an oversight in them: it is
    indistinguishable from a deliberate no-tier choice, and
    :class:`models.ModelResolutionError` — the refusal that catches a tier which
    resolves to nothing — fires only once a tier *was* declared. So the repo that
    ships the tier vocabulary itself ran 48 metered claude dispatches, an exact
    313.30 USD of recorded spend, with ``model``, ``model_tier``, ``model_source``
    and ``tier_honoured`` null on every one of them, and nothing went red.

    This is the check that goes red. Every headless runner meters its spend — exactly
    where the adapter reports usage, as a flagged chars/4 estimate otherwise — so
    every one of them attributes a cost to some model, and a cost attributed to
    nothing is what makes a per-model forecast or a cost-per-landed-package figure a
    number about nothing. The handoff runner is skipped rather than flagged: it
    spawns no process, spends nothing, and has no argv to pin a model onto.

    Each message names the config key that fixes it, because the reader of a failure
    needs to know what to declare, not which field came back null. Empty means every
    metered dispatch this config can make resolves a model it can name.

    Takes the loaded config as a value rather than reading it from disk, so the check
    can be run against a config with the declaration *removed* — which is the only
    way to know it still binds.
    """
    problems: list[str] = []
    for spec in config.specs:
        if spec.kind != HEADLESS:
            continue
        if spec.tier is None and spec.model is None:
            problems.append(
                f"runner {spec.name!r} meters what it spends but declares no model tier and no "
                f"model, so its cost lands on a model nobody named and the tier refusal cannot "
                f"fire; declare [runner] default_tier, or a 'tier' on [[runner.agents]] for "
                f"{spec.name!r}"
            )
            continue
        try:
            resolution = resolve_model(spec, repo_root=repo_root)
        except ModelResolutionError as exc:
            problems.append(f"runner {spec.name!r} declares a tier that cannot be pinned: {exc}")
            continue
        if resolution.model is None:
            problems.append(
                f"runner {spec.name!r} declares tier {resolution.tier!r} ({resolution.source}) but "
                f"no model was pinned, so the dispatch is metered against the session's own "
                f"model: {resolution.note or 'the family cannot express a model'}"
            )
    return problems


def _context_window(entry: dict, name: str) -> tuple[int, str]:
    """The entry's ``context_window`` and its provenance (basicly-kjc5.6, basicly-23ep).

    Same replaces-wholesale stance as ``usage_format``: an override of a builtin
    must restate its window or it falls to the conservative default. Malformed
    values raise — a silently shrunken window would mis-trigger the finalize
    protocol on every long run.

    The source travels with the value because the two answers are not equally
    trustworthy: a declared window is a figure someone checked against the model
    this agent dispatches, and a defaulted one is a figure nobody has.
    """
    value = entry.get("context_window", DEFAULT_CONTEXT_WINDOW)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"runner agent {name!r} has a 'context_window' that must be an integer")
    if value <= 0:
        raise ValueError(f"runner agent {name!r} has a 'context_window' that must be positive")
    source = AGENT_WINDOW if "context_window" in entry else FALLBACK_WINDOW
    return value, source


def _apply_context_windows(specs: dict[str, RunnerSpec], section: dict) -> None:
    """Fold ``[runner] context_windows`` onto the named specs (basicly-23ep).

    A ``[runner]`` sub-table rather than a ``[[runner.agents]]`` key, for the reason
    ``copilot_session_store`` is one: an entry there replaces a builtin wholesale, so
    declaring a window would force a consumer to restate the whole adapter — command,
    usage format, deny style — and a restatement that drops one of those is a worse
    defect than the window it fixed. Declaring the window is the *point* of this bead,
    so the cheap path has to be the correct one.

    An unknown agent name raises rather than being ignored. A window declared for an
    agent that does not exist is a typo whose only symptom would be the silent default
    it was written to replace, which is precisely the failure basicly-23ep is.
    """
    windows = section.get("context_windows")
    if not isinstance(windows, dict):
        return
    for name, value in windows.items():
        spec = specs.get(name)
        if spec is None:
            raise ValueError(
                f"[runner] context_windows declares a window for unknown agent {name!r}; "
                f"known agents: {sorted(specs)}"
            )
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(
                f"[runner] context_windows {name!r} must be a positive integer of tokens"
            )
        specs[name] = replace(spec, context_window=value, context_window_source=DECLARED_WINDOW)


def load_project_paths(repo_root: Path) -> ProjectPaths:
    """Load path settings from basicly.toml, falling back to defaults.

    Schema-checks the overlay as well as basicly.toml (basicly-1piy), even though
    [paths] is repo-level and never reads it: this is the loader `basicly build`
    and `basicly check` go through, so it is the one that has to notice.
    """
    defaults = ProjectPaths(
        core_fragments_dir=Path(".basicly/core/fragments"),
        overlay_fragments_dirs=(Path(".basicly-local/fragments"),),
        targets_dir=Path(".basicly/core/targets"),
        templates_dir=Path(".basicly/core/templates"),
        manifest_path=Path(".basicly/generated-manifest.json"),
        legacy_fragments_dir=Path(".basicly/fragments"),
    )

    data = _validated_documents(repo_root).get(CONFIG_FILE)
    if data is None:
        return defaults

    paths = data.get("paths", {})
    if not isinstance(paths, dict):
        return defaults

    core_fragments_dir = _parse_path_value(paths, "core_fragments", defaults.core_fragments_dir)
    targets_dir = _parse_path_value(paths, "targets", defaults.targets_dir)
    templates_dir = _parse_path_value(paths, "templates", defaults.templates_dir)
    manifest_path = _parse_path_value(paths, "manifest", defaults.manifest_path)

    overlay_fragments = _parse_overlay_paths(paths)
    if overlay_fragments is None:
        overlay_fragments_dirs = defaults.overlay_fragments_dirs
    else:
        overlay_fragments_dirs = tuple(overlay_fragments)

    return ProjectPaths(
        core_fragments_dir=core_fragments_dir,
        overlay_fragments_dirs=overlay_fragments_dirs,
        targets_dir=targets_dir,
        templates_dir=templates_dir,
        manifest_path=manifest_path,
        legacy_fragments_dir=defaults.legacy_fragments_dir,
    )


def _parse_path_value(paths: dict, key: str, default: Path) -> Path:
    value = paths.get(key)
    if isinstance(value, str) and value.strip():
        return Path(value)
    return default


def _parse_overlay_paths(paths: dict) -> list[Path] | None:
    value = paths.get("overlay_fragments")
    if value is None:
        return None

    if isinstance(value, str) and value.strip():
        return [Path(value)]

    if isinstance(value, list):
        parsed = [Path(item) for item in value if isinstance(item, str) and item.strip()]
        return parsed or None

    return None
