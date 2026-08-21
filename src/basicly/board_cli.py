"""The ``basicly board`` command group: its grammar, its dispatch, and Mode A's artifact.

Extracted from `cli.py` because that module carries a frozen size ratchet and three parser
groups were queued behind it; `tracker_query.add_parsers` is the shape followed, so `cli.py`
keeps the registration call and the dispatch-table entry and nothing else.

**This is where the live-lock facts are read, and the layer is the reason.** The producer may
not call `supervise.read_holder` itself - the import would close
`supervise -> board_snapshot -> supervise`, since the supervisor emits a snapshot of its own.
This module sits above `supervise`, so it reads the lock and passes the facts down. The layer
above supplies the fact the layer below cannot honestly derive.

**Mode A writes two files, and the second is the point.** The page is for a human; the
snapshot beside it is the contract, and every other consumer - a foreign board, a kit-only
checkout, a script - reads that rather than scraping the HTML.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from . import (
    board_render,
    board_schema,
    board_sections,
    board_snapshot,
    checkout,
    decisions,
    loop_state,
    owned_store,
    policy,
    run_record,
    supervise,
    tracker_query,
    ui,
)

# The sidecar the transcript names. Beside the page rather than derived from its stem, so two
# pages written to one directory do not each claim their own copy of one contract.
SNAPSHOT_NAME = "board-snapshot.json"

# The C1 freshness claim, frozen into the help text. The word `real-time` is absent by rule,
# not by accident: C1's own operative sentence is "displays the snapshot's age and never uses
# the word real-time", and a surface that spends the word to deny it has still spent it.
FRESHNESS = (
    "The board is as fresh as the producer that wrote its snapshot, and it always shows "
    "how old that snapshot is. In wall mode with a live supervisor it refreshes on the "
    "supervisor's 15-second tick."
)

_BYTES_PER_KB = 1024

# How many records get a loop phase, and the number is a cost rather than a taste.
# `loop_state.read_node_state` is the only route to `derive_phase` and it reads the whole log
# seven times per record - 591 ms over 20 records, measured 2026-08-21 - so all 234 active
# records is 138 s against a 171 ms build. The ranked ready front is the cut because it is the
# column a wall board is read for; outside it `phase` stays absent rather than guessed.
PHASE_LIMIT = 8

# What every fact-gathering read below treats as "no answer": no kit installed, an unreadable
# ledger, a report whose shape moved. Each costs its own key and never the page.
_UNREADABLE = (owned_store.TrackerDivergenceError, OSError, ValueError, KeyError, TypeError)

# The loud-miss wording, spelled a second time because this module may not import `cli`, whose
# `_dispatch` owns it for every required subparser. The two are bound by
# `test_cli.py::test_a_registered_subcommand_with_no_handler_fails_loudly`, which derives its
# sites from the parser and so asserts this whole string through `cli.main` at this site too.
MISS = (
    "internal error: subcommand {name!r} is registered on the parser but has no handler "
    "\N{EM DASH} this is a bug in basicly, not in your invocation"
)


def _session_facts(repo_root: Path) -> board_snapshot.SessionFacts | None:
    """The supervisor lock's facts and the run's grant, or None where no lock names a root.

    None rather than a guessed root: the `session` section is then omitted and its panel says
    the producer did not emit it, which is true. A root invented here would be a claim about
    which pass is running, drawn on a wall.

    The grant rides along: it is a fact about the same run and `policy.active_grant` is one
    comment walk, 0.17 s measured. Nothing else here is that cheap, hence :func:`_grant_spend`.
    """
    held = supervise.read_holder(repo_root)
    if held is None or not held.root_issue:
        return None
    stale = held.age_s > supervise.STALE_AFTER_S
    grant = _grant(repo_root, held.root_issue)
    return board_snapshot.SessionFacts(
        root_issue=held.root_issue,
        supervised=not stale,
        session_id=held.session_id or "",
        age_s=held.age_s,
        stale=stale,
        grant_level=grant.level if grant is not None else "",
        token_budget=grant.token_budget if grant is not None else None,
        spent_tokens=_grant_spend(repo_root, held.root_issue, grant),
    )


def _grant(repo_root: Path, root_issue: str) -> policy.Grant | None:
    """The run's active grant, or None when there is none or the tracker will not answer."""
    try:
        return policy.active_grant(repo_root, root_issue)
    except _UNREADABLE:
        return None


def _grant_spend(repo_root: Path, root_issue: str, grant: policy.Grant | None) -> int | None:
    """Spend under *grant* - the only figure its `token_budget` bounds - or None.

    **The window is the whole point.** Publishing lifetime spend beside a grant's ceiling is how
    a display comes to draw 177970761/4000000 with nothing spent under that grant
    (basicly-e2mz.13); `policy.tokens_under_grant` is the subtraction that makes the pair
    comparable.

    Two guards, each omitting the key rather than reporting a zero: no grant is no window, and
    no run-record file means this checkout cannot see the spend at all - a fresh worktree has
    none - where `spend_status` answers 0 and renders as a session that spent nothing. Behind
    both sits `policy.session_issue_ids` at 13.1 s, so the walk runs only where it is worth it.
    """
    if grant is None or not run_record.load_run_records(repo_root):
        return None
    try:
        status = policy.spend_status(repo_root, root_issue, grant=grant)
    except _UNREADABLE:
        return None
    return policy.tokens_under_grant(status.spent_tokens, grant)


def _repo_facts(repo_root: Path) -> board_sections.RepoFacts | None:
    """Which checkout and which commit, from git, or None when git will not answer.

    Here rather than in the producer because `dirty` is `git status` and the producer spawns no
    subprocess, pinned by a spy in `tests/test_board_snapshot.py`. `--porcelain=v1 -b` answers
    the branch and the dirt at once, its header line carrying the branch.
    """
    try:
        state = checkout.git(["status", "--porcelain=v1", "-b"], cwd=repo_root, check=False)
        head = checkout.git(["rev-parse", "--short", "HEAD"], cwd=repo_root, check=False)
    except OSError:
        return None
    if state.returncode != 0:
        return None
    lines = state.stdout.splitlines()
    header = lines[0].removeprefix("## ") if lines else ""
    # A detached HEAD reports `## HEAD (no branch)`, which is not a branch name and is omitted
    # rather than published as one. The `...upstream` suffix is not this checkout's branch.
    branch = "" if header.startswith("HEAD (no branch)") else header.partition("...")[0]
    return board_sections.RepoFacts(
        branch=branch,
        head=head.stdout.strip() if head.returncode == 0 else "",
        dirty=any(line.strip() for line in lines[1:]),
    )


def _readiness(repo_root: Path) -> board_sections.Readiness | None:
    """The tracker's own ready and blocked sets, or None when it will not answer.

    Read at this layer because `ready` is a derivation over a status vocabulary and the whole
    edge population that the kit's `queries` owns - the answer `basicly tracker ready` prints,
    reached through `tracker_query` so this module is not a second one on the store's seam.
    """
    try:
        return board_sections.Readiness(
            ready=frozenset(
                str(row["record"]) for row in tracker_query.ready_report(repo_root)["records"]
            ),
            blocked=frozenset(
                str(row["record"]) for row in tracker_query.blocked_report(repo_root)["records"]
            ),
        )
    except _UNREADABLE:
        return None


def _phases(repo_root: Path) -> dict[str, str]:
    """A loop phase for the front of the ready queue, keyed by record; empty on no answer.

    Bounded by :data:`PHASE_LIMIT`, and read through `loop_state.read_node_state` so
    `derive_phase` stays the one derivation - a phase folded out of the ledger alone diverges
    from the engine's for any unit owing validation, and renders identically.
    """
    found: dict[str, str] = {}
    try:
        front = tracker_query.ready_report(repo_root, PHASE_LIMIT)["records"]
        config = loop_state.load_policy_config(repo_root)
        for row in front:
            record = str(row["record"])
            found[record] = loop_state.read_node_state(repo_root, record, config).phase
    except _UNREADABLE:
        return {}
    return found


def _questions(repo_root: Path, document: dict[str, object]) -> dict[str, str]:
    """The wording behind each pending ask in *document*, keyed by wait id.

    **Read off the document's own asks, and the cost is why.** A request marker carries no prose
    - `policy.record_wait_request` writes an id, a kind and `requested` - so the wording lives
    only on the decision queue, which `decisions.pending` reaches through
    `policy.session_issue_ids` at 13.1 s. The asks already found name the records to ask about
    instead, so the read is one per pending ask and none when nothing is pending.

    Paired on the checkpoint name appearing in the question, `decisions.settle_checkpoint`'s own
    rule: the wording lives at the enqueue site, so keying on a reconstruction of it would stop
    pairing the moment an ask is reworded.
    """
    asks = document.get("asks")
    if not isinstance(asks, list):
        return {}
    found: dict[str, str] = {}
    for ask in asks:
        wait_id, issue = str(ask.get("wait_id", "")), str(ask.get("issue", ""))
        subject = str(ask.get("subject", ""))
        if not (wait_id and issue and subject):
            continue
        try:
            items = decisions.items_on(repo_root, issue)
        except _UNREADABLE:
            continue
        for item in items:
            if item.pending and subject in item.question:
                found[wait_id] = item.question
    return found


def _kb(path: Path) -> str:
    """*path*'s size as the transcript prints it."""
    return f"{path.stat().st_size / _BYTES_PER_KB:.0f} KB"


def cmd_validate(args: argparse.Namespace) -> int:
    """Report whether one snapshot is readable by this consumer, and exit on the answer.

    The whole verb, because the judgement is `board_schema`'s: a major-version mismatch
    is a different contract and refuses, an unknown key is reported and admitted.
    """
    verdict = board_schema.validate_file(Path.cwd(), args.path)
    ui.say(verdict.summary)
    return verdict.exit_code


def _document(repo_root: Path) -> dict[str, object]:
    """One snapshot of *repo_root*, with every fact this layer can supply supplied.

    **The second build is a fact this layer cannot gather first, not a retry.** A wait's wording
    is keyed by wait id and only the producer knows which waits are pending, so
    :func:`_questions` reads the first document to learn what to ask about. Folding again is
    171 ms and only when an ask is pending, against the 13.1 s walk asking blind would cost. The
    producer's guarantee is untouched: each call folds the log once.
    """
    facts = board_snapshot.Facts(
        session=_session_facts(repo_root),
        repo=_repo_facts(repo_root),
        phases=_phases(repo_root),
        readiness=_readiness(repo_root),
    )
    document = board_snapshot.build_document(repo_root, facts=facts)
    questions = _questions(repo_root, document)
    if not questions:
        return document
    return board_snapshot.build_document(repo_root, facts=replace(facts, questions=questions))


def cmd_emit(args: argparse.Namespace) -> int:
    """Mode A: fold a snapshot, write the page and the contract beside it, say what it holds.

    A document the contract refuses is not drawn. Its verdict is printed and its exit code
    returned instead, because a page rendered over a document with no valid age on it is the
    one output this design has no honest use for.
    """
    if args.out is None:
        ui.warn("board: --out is required to write the page, or name a subcommand")
        return 2
    repo_root = Path.cwd()
    started = time.perf_counter()
    document = _document(repo_root)
    verdict = board_schema.verdict(repo_root, document)
    took = (time.perf_counter() - started) * 1000
    if not verdict.readable:
        ui.say(verdict.summary)
        return verdict.exit_code
    page = board_render.page(document, verdict, now=datetime.now(UTC))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page, encoding="utf-8")
    sidecar = args.out.parent / SNAPSHOT_NAME
    sidecar.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    ui.say(f"board: {board_schema.VERSION} snapshot built in {took:.0f} ms")
    ui.say(
        f"board: sections  {len(verdict.renderable)} rendered, "
        f"{len(verdict.withheld)} withheld, {len(verdict.absent)} not emitted"
    )
    ui.say(f"board: wrote {args.out} (self-contained, {_kb(args.out)}) - open it in a browser")
    ui.say(f"board: wrote {sidecar} ({_kb(sidecar)}) - the contract, for any other consumer")
    return verdict.exit_code


_HANDLERS = {None: cmd_emit, "validate": cmd_validate}


def cmd_board(args: argparse.Namespace) -> int:
    """Dispatch the harness board's subcommands; no subcommand is Mode A's artifact.

    Its own dispatch rather than `cli._dispatch`, which documents that every subparser in that
    file is required. This group's is not - `basicly board --out X` is the documented Mode A
    invocation - so the None key is a registered route here and a miss is still loud.
    """
    chosen = getattr(args, "board_command", None)
    handler = _HANDLERS.get(chosen)
    if handler is None:
        name = f"board {chosen}".strip()
        ui.warn(MISS.format(name=name))
        return 2
    return handler(args)


def add_parsers(subparsers: argparse._SubParsersAction) -> None:
    """Register `basicly board` — the harness board's page, and its snapshot surface."""
    board = subparsers.add_parser(
        "board",
        help="The harness board: the factory and the tracker, on one page",
        description=f"Write the harness board as one self-contained HTML file. {FRESHNESS}",
    )
    board.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"Write the page here, with {SNAPSHOT_NAME} beside it",
    )
    board_sub = board.add_subparsers(dest="board_command", required=False)
    validate = board_sub.add_parser(
        "validate", help="Check a board snapshot against the schema this consumer reads"
    )
    validate.add_argument("path", type=Path, help="The snapshot file to read")
