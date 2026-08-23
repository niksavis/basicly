"""What a lane has to read, priced in tokens.

One question, and it is the only one this module answers: given a repo and a set of
declared scope globs, how much material would an agent actually pull into context.
Two terms answer it — the projected instruction file every dispatch prompt points at
(:func:`instruction_overhead`) and the files the declared globs match
(:func:`scope_read_cost`) — and both are *measured* against the tree, never configured
(design section 6). Both are priced in the same chars/4 unit, which is calibrated on
prose and on nothing else: :func:`_text_tokens` records the measured error per payload
shape and the one use — comparing two serializations of the same data — that the unit
cannot support.

The boundary is measurement against judgement. Nothing here knows what a build factor
is, what the band is, or whether a plan is dispatchable: :mod:`basicly.decompose`
multiplies these numbers into a ``CostEstimate`` and ``policy.check_working_set``
decides what to do about the result. So this module imports nothing from the package,
which is the property that lets a number be taken from more than one place and still be
the same number — it is why ``.scripts/check_module_size.py`` imports
:data:`SCOPE_FILE_READ_CAP` and :func:`_text_tokens` rather than respelling chars/4 a
second time, and why doing so costs it no engine import.

Split out of :mod:`basicly.decompose` when that module crossed the module-size cap. The
seam was already there: this is the half of the estimator that touches the filesystem,
and the only half whose tests need a tree on disk.
"""

from __future__ import annotations

from pathlib import Path

# The projected agent-neutral instruction file every dispatch prompt points at;
# its size is context every lane pays before reading any scope material.
INSTRUCTIONS_FILE = "AGENTS.md"


def _text_tokens(text: str) -> int:
    """Deterministic chars/4 token estimate (design 7.5: no tokenizer dependency).

    It is calibrated on prose and on nothing else, and the second half of that
    sentence is a prohibition rather than a caveat. Measured 2026-08-08 against
    tiktoken's ``o200k_base`` over real payloads from this repo (basicly-u2hl.32)
    — each figure is the signed relative error against the tokenizer,
    ``(chars/4 - real) / real``, so a real count is recovered as
    ``estimate / (1 + error)``::

        payload                          n     chars/4 against o200k_base
        prose (skill bodies)                                       +1.6%
        beads issue json                50                        -10.7%
        run-record json                 50                        -16.4%
        run-record markdown headings    50                        -28.9%
        run-record tsv                  50                        -39.5%

    On prose the estimate is worth what it costs: +1.6% is far inside the resolution
    of everything downstream of it, since :data:`SCOPE_FILE_READ_CAP` is a
    4,000-token cap fitted to buckets hundreds of tokens wide and the govern band it
    feeds is 8K-64K.

    On structured text it under-counts by 10-40%, and the size of the miss is set by
    the *format* rather than by the content. So it cannot rank two renderings of the
    same data, which is the one use it must never be put to: correcting json by its
    measured -16.4% and tsv by its measured -39.5% multiplies the gap between them by
    1.38, so a format switch this estimator reports as a saving is a real saving only
    above ~28%, and under that the ranking inverts. ``tests/test_read_cost.py`` pins a
    pair of real renderings whose whole estimated gap sits inside that bias, and a
    second pair where the ranking survives but the saving is overstated by 14 points.

    A format comparison needs a real tokenizer over the real payloads, which is what
    produced the table above. Vendoring a BPE here is not on the table either: it is
    a confirmation-gated dependency change (factory design §6,
    "Estimate (at decompose)") and is refused outright under ``.basicly/core/kit``,
    which imports the standard library and nothing else.
    """
    return len(text) // 4


def instruction_overhead(repo_root: Path) -> int:
    """Fixed per-repo instruction overhead: the projected AGENTS.md, tokenized.

    Computed by tokenizing the projected instructions, never configured
    (design section 6). A repo without the file contributes zero; non-UTF-8
    content still counts by size via replacement (same stance as scope files).
    """
    try:
        path = repo_root / INSTRUCTIONS_FILE
        return _text_tokens(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return 0


# Directory names that are never a lane's working set: the VCS store, a
# virtualenv, a dependency tree, and byte/tool caches. Matched by *name* at any
# depth rather than by a leading dot, because the dot-directories this project
# authors — ``.basicly``, ``.claude``, ``.github`` — are legitimate
# scope and excluding them would silently zero their read-cost, which is the
# failure the ``./`` handling below already guards against.
#
# Deliberately conservative. ``dist``, ``build`` and ``site`` are *not* here:
# basicly is installed into consumer repositories where any of those can be a
# real source package, and a wrong exclusion is worse than a wrong inclusion —
# it under-reads a lane and admits work the band should have refused
# (basicly-jr0l.63).
SCOPE_EXCLUDED_DIRS: frozenset[str] = frozenset({
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".import_linter_cache",
    ".doctor",
})


def _is_excluded(repo_root: Path, path: Path) -> bool:
    """True when *path* sits under a directory no declared scope should read."""
    try:
        parts = path.relative_to(repo_root).parts
    except ValueError:
        # Outside the repo entirely — glob cannot produce this, but a caller
        # passing an absolute pattern could; treat it as unreadable material.
        return True
    # parts[:-1] — the *directories*, never the filename, so a file that happens
    # to be named `venv` is still read.
    return any(part in SCOPE_EXCLUDED_DIRS for part in parts[:-1])


def _scope_files(repo_root: Path, scope: tuple[str, ...]) -> set[Path]:
    """The existing files matching any of the declared scope globs.

    Only a literal ``./`` prefix is stripped — a bare ``lstrip`` would eat the
    leading dot of a dot-directory scope (``.claude/**``) and silently zero its
    read-cost. A leading ``/`` is relativized; a pattern the glob engine still
    rejects (e.g. drive-anchored on Windows) is skipped, never fatal — the
    governor treats it as unreadable material, matching the scope_read_cost
    stance.

    Paths under :data:`SCOPE_EXCLUDED_DIRS` are dropped. Without that, a scope of
    ``**/*.py`` measured 2229 files here of which 2077 were the virtualenv — an
    estimate describing the machine rather than the work, and one that pushes a
    lane past ``working_set_max`` where the refusal holds it pending a human
    (basicly-jr0l.63).
    """
    files: set[Path] = set()
    for pattern in scope:
        normalized = pattern.strip().replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        normalized = normalized.lstrip("/")
        if not normalized:
            continue
        try:
            matches = list(repo_root.glob(normalized))
        except ValueError, NotImplementedError, OSError:
            continue
        for path in matches:
            if path.is_file() and not _is_excluded(repo_root, path):
                files.add(path)
    return files


# How much of one file a lane actually reads (basicly-fcls).
#
# This used to be "all of it", and that made a scope naming `cli.py` cost 45_556
# tokens for a three-line change — while the harness's own always-on `tool-usage`
# guidance tells the same agent to "find files by name, localize with focused
# search, read only the ranges you need". The estimator and the instructions
# described different agents, and the estimator was the one holding the gate.
#
# Measured over 185 (lane, file) pairs from 24 headless lane transcripts, taking
# the *union* of line ranges each lane read out of each file, against the file's
# size in this module's own chars/4 unit:
#
#   file tokens        n    median tokens read    median fraction
#        73-  987     20                   357              1.000
#       993- 2454     20                  1101              1.000
#      2676- 3823     20                  2779              1.000
#      4494- 6967     20                  1350              0.219
#      7353-12843     20                  1524              0.147
#     12843-17460     20                  1356              0.094
#     17460-22829     20                  2224              0.122
#     22829-32519     20                  1372              0.053
#     32519-45556     25                  ~1000             0.022-0.068
#
# 78% of `Read` calls carried an offset or a limit. The shape is not a gentle
# taper: below roughly 4_000 tokens a lane reads the file whole, and above it the
# material it takes out is *flat* at ~1_500 tokens however large the file gets.
# So the model is a cap, not a curve, and 4_000 is where the whole-file band ends
# (last whole-read bucket tops out at 3_823, first partial bucket starts at 4_494).
#
# Set at the transition rather than at the ~1_500 plateau, deliberately: the cap
# then covers the material actually read in 86% of the measured pairs and
# over-states the large end by about 1.5x. That is the same stance
# SCOPE_EXCLUDED_DIRS takes above — over-reading costs a false refusal a human can
# see, under-reading admits work the band should have refused (basicly-jr0l.63).
#
# The cap alone is *not* the whole answer and must not be read as one. A lane's
# real context occupancy correlates with its declared scope at R^2 = 0.095 over
# those same 24 lanes (against 0.863 for turn count), and six lanes declaring no
# scope at all still occupied 106k-209k tokens — so the term this formula is
# really missing is a large ambient one, not a better read model. Fitting that
# needs a measurement, which is why `RunRecord.context_tokens` lands with this
# change and why no ambient constant is invented here: a factor fitted before the
# measurement existed is exactly how basicly-z2wi's 216x happened.
SCOPE_FILE_READ_CAP = 4_000


def scope_read_cost(repo_root: Path, scope: tuple[str, ...]) -> int:
    """Tokenized material a lane reads out of the files matching its scope globs.

    Each file contributes its own size or :data:`SCOPE_FILE_READ_CAP`, whichever
    is smaller — a small file is read whole, a large one is localized into
    (basicly-fcls). Capping per *file* rather than per scope is what keeps a lane
    naming three large modules costing more than one naming a single large module,
    which a cap on the total would flatten away.

    A glob matching nothing — a file the child will create — contributes zero:
    there is nothing to read yet. Unreadable files are skipped (telemetry-grade
    input, never fatal); binary content still counts by size via replacement.
    """
    total = 0
    for path in _scope_files(repo_root, scope):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        total += min(_text_tokens(text), SCOPE_FILE_READ_CAP)
    return total
