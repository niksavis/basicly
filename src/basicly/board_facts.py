"""The derivations a board caller supplies because the producer may not make them.

`board_sections.units` states the rule: "both stay absent until a caller supplies them". A
second spelling of `loop_state.derive_phase` inside the producer is how two derivations come
to disagree, so every fact it refuses is gathered here, at the tier that may honestly derive
it. Split out of `board_cli` when two lanes landed on it in one day and the merge crossed the
size ratchet at 4108 of 4000 tokens - each branch green, the merge red (basicly-nwx4ku).

Every read fails to an absence, never a zero: an unfillable section is omitted and its panel
says the producer did not emit it, which is true.
"""

# module-size-waiver: cost(basicly-0bj8q1): 4219 of 4000 tokens. Filling the lane card from
# the three tiers that hold its figures added 641; 113 came back out of two docstrings before
# the rest was the reasoning the cap exists to protect. The nameable cut - `_lane_fact` and
# its three coercions into `board_lane.py` - needs a line in `.importlinter`, whose 116
# entries leave no module unlisted, and that file is unlanded scope of `basicly-rn0o.6`.

# comment-density-waiver: cohesion: 56.1% because the split moved the code and its reasons
# together, and every comment left is a measurement or an incident rather than narration -
# `phases` carries the 591 ms per record that once capped it, `grant_spend` carries the
# 177970761/4000000 a lifetime figure once drew against a grant ceiling, and `questions`
# carries why a wait marker holds no prose. Architecture 34.1: the size and density ratchets
# are jointly unsatisfiable on a split, so this is the priced outcome, not an unmeasured one.
# The code half went the other way - `board_cli` left the headroom report entirely.

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from . import (
    board_advance,
    board_sections,
    board_serve,
    board_snapshot,
    checkout,
    decisions,
    loop_state,
    owned_store,
    policy,
    run_record,
    supervise,
    tracker_query,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from typing import Any

# What every fact-gathering read below treats as "no answer": no kit installed, an unreadable
# ledger, a report whose shape moved. Each costs its own key and never the page.
UNREADABLE = (owned_store.TrackerDivergenceError, OSError, ValueError, KeyError, TypeError)

# What a *session* derivation additionally treats as no answer: `tracker.require_record` raises
# `RuntimeError` for a root it cannot show and `supervise.LaneSelectionError` is one for a
# selector naming nothing. Named rather than spelled in the clause, because `except *UNREADABLE,
# RuntimeError:` - the paren-free house form - parses as an `except*` group and is a SyntaxError.
NO_SESSION = (*UNREADABLE, RuntimeError)


def session_facts(repo_root: Path) -> board_snapshot.SessionFacts | None:
    """The supervisor lock's facts and the run's grant, or None where no lock names a root.

    Two lanes wrote this at once and the merge kept both halves. The lock reading lives in
    `board_serve` - the tier immediately below - because Mode A and Mode B both need it and a
    second reader is a second answer to "which pass is running". The grant is added here
    rather than there, because it is a fact about the run and not about the lock.

    None rather than a guessed root: the `session` section is then omitted and its panel says
    the producer did not emit it, which is true. A root invented here would be a claim about
    which pass is running, drawn on a wall.

    The grant rides along: `policy.active_grant` is one comment walk, 0.17 s measured. Nothing
    else here is that cheap, hence :func:`grant_spend`.
    """
    facts = board_serve.session_facts(repo_root)
    if facts is None:
        return None
    grant = active_grant(repo_root, facts.root_issue)
    return replace(
        facts,
        grant_level=grant.level if grant is not None else "",
        token_budget=grant.token_budget if grant is not None else None,
        spent_tokens=grant_spend(repo_root, facts.root_issue, grant),
    )


def active_grant(repo_root: Path, root_issue: str) -> policy.Grant | None:
    """The run's active grant, or None when there is none or the tracker will not answer."""
    try:
        return policy.active_grant(repo_root, root_issue)
    except UNREADABLE:
        return None


@dataclass(frozen=True)
class SpendSources:
    """The stores a spend figure can come from, each read once for the whole tracker.

    Read together because a caller metering several roots pays each read once rather than
    once per root: re-reading cost 5.18 s of the 6.14 s `basicly session start` took over
    eighteen live grants [measured 2026-09-01, 1190 records].
    """

    population: dict[str, dict]
    # The machine-local run records, or None where this checkout has no file at all. None
    # and `{}` are different facts: no file is a checkout that cannot see its own spend,
    # and an empty file is one that has spent nothing.
    local: dict[str, list] | None
    # The `[harness-run]` markers the ledger commits - the only copy that travels, and
    # therefore the one a clone reads.
    ledger: dict[str, list]
    # Both, deduplicated per dispatch by `run_record.dispatch_history` rather than here: a
    # second copy of that rule would be a second answer to what one dispatch is.
    union: dict[str, list]


@dataclass(frozen=True)
class SpendSplit:
    """One root's spend under its grant, and what each store says on its own.

    Both, because they disagree for a reason worth seeing rather than resolving: the local
    file holds dispatches not yet committed, and the ledger holds dispatches other machines
    ran. `tokens` is the union and is never below either.
    """

    tokens: int
    # None where this checkout has no run-record file, which is not the same fact as a file
    # holding nothing: the stores can only be said to disagree where both of them answered.
    local: int | None
    ledger: int
    # `policy.SpendMeter.dispatches_seen`'s spelling, and for its reason: the bare name
    # `dispatches` retires a suppression that is still genuine.
    dispatches_seen: int


def spend_sources(repo_root: Path) -> SpendSources:
    """Both spend stores and the record population, read once."""
    return SpendSources(
        population=policy.records_by_id(repo_root),
        local=run_record.load_run_records(repo_root),
        ledger=run_record.tracker_history(repo_root),
        union=run_record.dispatch_history(repo_root),
    )


def grant_split(
    repo_root: Path,
    root_issue: str,
    grant: policy.Grant | None,
    *,
    sources: SpendSources | None = None,
) -> SpendSplit | None:
    """Spend under *grant* per store, or None where this checkout can see no dispatch.

    **The window is the whole point.** Publishing lifetime spend beside a grant's ceiling is how
    a display comes to draw 177970761/4000000 with nothing spent under that grant
    (basicly-e2mz.13); `policy.tokens_under_grant` is the subtraction that makes the pair
    comparable.

    None on two facts and never on a third: no grant is no window, and no dispatch under the
    root is a spend this checkout cannot see, where a zero would render as a session that
    spent nothing. A root whose dispatches really did cost nothing reports 0, which is why
    the guard counts dispatches rather than tokens.

    The reported figure is the union, so this checkout and a clone of it answer alike once
    the markers are committed. It is deliberately **not** what D3 halts on: `spend_status`
    still meters the local file, and 6 of this repository's 18 live grants are over budget
    on the travelling figure [measured 2026-09-01], so moving the ceiling onto it halts them
    and is a decision to take knowingly rather than as a side effect of a display.
    """
    if grant is None:
        return None
    held = sources if sources is not None else spend_sources(repo_root)
    try:
        ids = policy.session_issue_ids(repo_root, root_issue, population=held.population)
        union = policy.session_spend(repo_root, root_issue, ids=ids, history=held.union)
        ledger = policy.session_spend(repo_root, root_issue, ids=ids, history=held.ledger)
        local = (
            None
            if held.local is None
            else policy.session_spend(repo_root, root_issue, ids=ids, history=held.local)
        )
    except UNREADABLE:
        return None
    if not union.dispatches_seen:
        return None
    under = policy.tokens_under_grant
    return SpendSplit(
        tokens=under(union.measured_tokens, grant),
        local=None if local is None else under(local.measured_tokens, grant),
        ledger=under(ledger.measured_tokens, grant),
        dispatches_seen=union.dispatches_seen,
    )


def grant_spend(
    repo_root: Path,
    root_issue: str,
    grant: policy.Grant | None,
    *,
    sources: SpendSources | None = None,
) -> int | None:
    """Spend under *grant* - the only figure its `token_budget` bounds - or None.

    :func:`grant_split` without the split, for the callers that report one number.
    """
    split = grant_split(repo_root, root_issue, grant, sources=sources)
    return None if split is None else split.tokens


def live_grant_spend(session: board_snapshot.SessionFacts) -> int | None:
    """*session*'s recorded spend plus what the lanes running now have reported, or None.

    **The one number that moves while the pass runs.** `grant_spend` only advances when a
    lane's run record lands, so a nine-minute lane reports 0% for the whole of it
    (basicly-wctp0g); `supervise.inflight_spend` holds the same lanes' tokens-so-far and is the
    only source that can. A separate key rather than a replacement of `spent_tokens`, because
    the two are different denominations - the recorded figure is what D3's gate binds on
    (`policy.spend_status`, never read through this module) and this changes what is reported,
    not what is enforced.

    None below a budget: with no `token_budget` there is no ceiling to show the estimate
    against. None where nothing is live too: a producer that is not the supervisor sees no
    process-local stream at all, and a zero here would read as a lane that spent nothing
    rather than as a fact this checkout cannot see.
    """
    if session.token_budget is None:
        return None
    live = sum(supervise.inflight_spend().values())
    if not live:
        return None
    return max(0, (session.spent_tokens or 0) + live)


def _with_live_spend(
    built: dict[str, object], session: board_snapshot.SessionFacts | None
) -> dict[str, object]:
    """*built*, its session section carrying the live estimate beside the recorded figure.

    Marked as an over-estimate with the measured direction attached
    (`supervise.LIVE_OVERREPORT_BOUND`), rather than left for a reader to assume the two
    figures are comparable at face value - they are not, and the measured direction is that
    the live one reads high.
    """
    if session is None:
        return built
    section = built.get("session")
    if not isinstance(section, dict):
        return built
    live = live_grant_spend(session)
    if live is None:
        return built
    section["spent_tokens_live"] = live
    section["spent_tokens_live_over_estimate"] = True
    section["spent_tokens_live_bound"] = supervise.LIVE_OVERREPORT_BOUND
    return built


def repo_facts(repo_root: Path) -> board_sections.RepoFacts | None:
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


def readiness(repo_root: Path) -> board_sections.Readiness | None:
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
    except UNREADABLE:
        return None


def phases(repo_root: Path) -> dict[str, str]:
    """A loop phase for every live record, keyed by record; empty on no answer.

    Unbounded since basicly-s1vqq2, and `loop_state.phase_map` is why: it folds the event
    log once for the whole population instead of the seven reads per record
    `read_node_state` costs, which is what capped this at the eight-record ready front and
    left the loop region reading `intake 8` over 234 units. `derive_phase` is still the one
    derivation - a phase folded out of the ledger alone diverges from the engine's for any
    unit owing validation, and renders identically.
    """
    try:
        return loop_state.phase_map(repo_root)
    except UNREADABLE:
        return {}


def questions(repo_root: Path, document: dict[str, object]) -> dict[str, str]:
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
        except UNREADABLE:
            continue
        for item in items:
            if item.pending and subject in item.question:
                found[wait_id] = item.question
    return found


def lane_facts(
    repo_root: Path,
    root_issue: str,
    phase_map: Mapping[str, str],
    *,
    lane_label: str | None = None,
) -> tuple[board_sections.LaneFacts, ...] | None:
    """The pass's in-flight lanes as board rows, or None where the session will not derive.

    **`IN FLIGHT` had no producer at all until this.** `supervise.observe` already builds these
    views for `loop session`; the facts existed and never reached the board. `supervise.lane_view`
    is reused rather than widened a second time, so there stays one answer to what a lane last ran.

    The phase comes out of *phase_map*, which the caller has folded once for the whole
    population, so a lane costs no read of its own - the per-lane `loop_state.read_node_state`
    that priced this section out is not on the path.

    *lane_label* is the selector the supervisor was started with. Without it a label pass reports
    the root's children instead, which is a truthful view of a *different* session.

    None rather than `()`: an empty tuple is the claim that lanes are visible and there are none,
    and a derivation this checkout could not run has made no claim at all.
    """
    try:
        state = supervise.derive_session(repo_root, root_issue, lane_label=lane_label)
        views = [supervise.lane_view(repo_root, lane) for lane in state.adopted]
    except NO_SESSION:
        return None
    spending = supervise.inflight_spend()
    doing = supervise.inflight_activity()
    issued = supervise.inflight_dispatch()
    standings = supervise.lane_standings()
    runs = run_record.load_run_records(repo_root) or {}
    return tuple(
        _lane_fact(
            view,
            phase_map,
            spending,
            doing,
            runs.get(view.issue_id) or [],
            dispatch=issued,
            standing=standings.get(view.issue_id),
        )
        for view in views
    )


def _lane_fact(  # noqa: PLR0913 - one parameter per tier the card draws a figure from
    view: supervise.LaneView,
    phase_map: Mapping[str, str],
    spending: Mapping[str, int],
    doing: Mapping[str, str],
    runs: Sequence[Mapping[str, Any]],
    *,
    dispatch: Mapping[str, supervise.LaneStream] | None = None,
    standing: supervise.LaneStanding | None = None,
) -> board_sections.LaneFacts:
    """One lane's card, each figure from the tier that holds it.

    The live stream (:func:`supervise.inflight_spend`, :func:`supervise.inflight_activity`)
    holds a running lane's spend and its last word; it is process-local to the supervisor,
    so it answers only where the producer *is* the tick and is empty elsewhere rather than
    zero. The last run record holds a finished dispatch's cost, occupancy and duration.
    :class:`supervise.LaneView` holds what the tracker binds.

    **`running` is presence in the live stream, not `view.live` (basicly-ze0po3).**
    `view.live` only says the worktree session record still exists on disk, which a schema
    consumer reads as "still running" - so a lane whose agent exited hours ago and whose
    worktree nobody has cleaned up was drawn as work in flight (basicly-rn0o.6 v. -fi1i7z,
    the same pass, one carrying a note and tokens and the other neither). `spending` names
    exactly the lanes with a live stream registered right now, so membership in it is the
    fact the schema's `live` promises; the worktree fact still matters and travels apart as
    `provisioned`, because a lane waiting for its next dispatch is a real, distinct state.

    **A running lane does not inherit the previous dispatch's cost or occupancy.** Those are
    per-dispatch, so carrying them forward prints last run's spend as this run's under a
    heading saying the lane runs now. `agent` and `model` do carry: a lane keeps its runner.

    **The running dispatch answers for itself where it can (basicly-1bsfx3).** *dispatch* is
    :func:`supervise.inflight_dispatch`, whose meter was minted at the spawn, so a lane's
    *first* dispatch names its agent, its model and its own start - the run record that
    carried those is written after the process stops, and until then the card drew none of
    them. `started_at` and `elapsed_s` therefore change source rather than merely gaining
    one: a running lane took the previous dispatch's stamp as this run's, which is the same
    defect the cost and occupancy rules above already refuse.

    `cost_usd` and `context_used` stay absent while a lane runs, and that is the omit-never-
    estimate rule rather than an omission: `runner.claude_turn_usage` reports no per-turn
    cost - `total_cost_usd` is on the terminating result event alone - and nothing in the
    stream reports occupancy at all, so the meter holds neither to publish.

    `tokens` obeys the same rule as cost and occupancy rather than an exception to it: a
    **running lane never shows a figure from a previous dispatch.** While a lane runs the live
    stream is the only admissible source, and where it has nothing to say the card says
    nothing. Two windows produce that silence and both must stay silent - a stream published
    the instant a dispatch starts and not yet metered, which reports a real `0`, and a lane
    the supervisor has not yet registered a stream for at all. Falling back on either hands
    the window to the last dispatch's total, which reads as a lane that has already spent
    millions the second it starts.

    Where the live figure does speak, it over-reports the record it becomes by a factor
    :mod:`supervise` measures, so it rises toward a known-larger number - the safe direction
    for a reader watching a budget.

    **`state` is where the lane stands in the pass, and the live stream outranks the
    standing (basicly-ncday7).** *standing* is `supervise.lane_standings`, published by the
    frame that knows each fact: the WIP bound names its refusals, the pool names the lanes
    queued behind the cap, and the serial landing loop names the queue and the one landing
    it is running. None of those reaches a store, so a pass doing four things published
    `live` true-or-false and a reader saw one bit. `running` is derived here instead of
    published, because a live stream registered *is* the lane running and a second writer
    would be a second answer - a lane that started while a `queued` standing still stood
    would otherwise read as waiting for the slot it is already inside.
    """
    running = view.issue_id in spending
    last = runs[-1] if runs else {}
    spent = spending.get(view.issue_id)
    meter = (dispatch or {}).get(view.issue_id) if running else None
    state = _standing_state(standing, running=running)
    return board_sections.LaneFacts(
        id=view.issue_id,
        phase=phase_map.get(view.issue_id, ""),
        status=view.status,
        state=state[0],
        state_detail=state[1],
        state_since=state[2],
        agent=(meter.agent if meter else "") or view.last_agent or _text(last.get("agent")),
        live=running,
        provisioned=view.live,
        started_at=meter.started_at if meter else (view.last_run_at or ""),
        tokens=(spent or None) if running else view.last_tokens,
        branch=view.branch,
        model=(meter.model if meter else "") or _text(last.get("model")),
        note=doing.get(view.issue_id, ""),
        cost_usd=None if running else _number(last.get("cost")),
        elapsed_s=meter.elapsed_s
        if meter
        else (None if running else _number(last.get("duration_s"))),
        context_used=None if running else _whole(last.get("context_tokens")),
        context_window=None if running else _whole(last.get("context_window")),
    )


def _standing_state(
    standing: supervise.LaneStanding | None, *, running: bool
) -> tuple[str, str, str]:
    """A lane's pass state, its reason and when it entered it - each empty where unknown.

    A live stream outranks any standing: it is the running lane's own registration, so a
    `queued` standing the pool has not retired yet, or a `landing` one from a route that
    lost its lock, cannot state the opposite of what the supervisor is holding open. With
    no stream and no standing the three stay empty and the row omits all of them, which is
    a producer that cannot place this lane rather than one placing it in `queued`.
    """
    if running:
        # No stamp: `live_lane` retires the `queued` standing as it registers the stream, so
        # the only stamp left would be the dispatch's own, which `started_at` already is.
        return supervise.LANE_RUNNING, "", ""
    if standing is None:
        return "", "", ""
    return standing.state, standing.detail, standing.since


def _text(held: object) -> str:
    """*held* as a string, else empty."""
    return held if isinstance(held, str) else ""


def _number(held: object) -> float | None:
    """*held* as a float, else None. A bool is not a measurement of anything."""
    return float(held) if isinstance(held, int | float) and not isinstance(held, bool) else None


def _whole(held: object) -> int | None:
    """*held* as an int, else None. Excludes bool for the same reason."""
    return held if isinstance(held, int) and not isinstance(held, bool) else None


def _visible_asks(
    asks: Sequence[Mapping[str, Any]], live_ids: frozenset[str], grant_level: str
) -> list[dict[str, object]]:
    """*asks* minus a checkpoint no human can, or need, answer (basicly-0i86tl).

    A record ``document`` no longer lists in ``units`` is closed or tombstoned - the same
    "active population" cut `board_snapshot._read_and_fold` draws - and a wait pinned to it
    is unanswerable by anyone, however old its stamp. `policy.GRANT_COVERAGE` is the table
    `approve_checkpoint_guarded` already checks before ever asking a human, so a checkpoint
    it covers under the session's own grant needs no ask either. A decision carries no
    checkpoint name to cover, so it clears the second test by construction and renders
    whenever its record is live - the grant is never consulted for it.
    """
    covered = policy.GRANT_COVERAGE.get(grant_level, ())
    visible = []
    for ask in asks:
        if str(ask.get("issue", "")) not in live_ids:
            continue
        if ask.get("kind") == "checkpoint" and ask.get("subject") in covered:
            continue
        visible.append(dict(ask))
    return visible


def _hide_unanswerable(built: dict[str, object]) -> dict[str, object]:
    """*built*, with :func:`_visible_asks` applied to its ``asks`` section, if any."""
    asks = built.get("asks")
    if not isinstance(asks, list):
        return built
    units = built.get("units")
    live_ids = (
        frozenset(str(row["id"]) for row in units if isinstance(row, dict) and "id" in row)
        if isinstance(units, list)
        else frozenset()
    )
    session = built.get("session")
    grant_level = str(session.get("grant_level", "")) if isinstance(session, dict) else ""
    built["asks"] = _visible_asks(asks, live_ids, grant_level)
    return built


def document(
    repo_root: Path, *, lane_label: str | None = None, in_flight: bool = False
) -> dict[str, object]:
    """One snapshot of *repo_root*, with every fact this layer can supply supplied.

    **The second build is a fact this layer cannot gather first, not a retry.** A wait's wording
    is keyed by wait id and only the producer knows which waits are pending, so
    :func:`questions` reads the first document to learn what to ask about. Folding again is
    171 ms and only when an ask is pending, against the 13.1 s walk asking blind would cost. The
    producer's guarantee is untouched: each call folds the log once.

    *in_flight* adds :func:`lane_facts`, and only a caller that knows the pass may ask for it:
    Mode A and Mode B fold whatever lock they find without knowing its selector, so a label
    pass drawn from there would name the root's children.

    **The action band never gets a marker `build_document` alone would still emit.**
    :func:`_hide_unanswerable` runs on whichever build is returned - the ask-free first pass
    or the reworded second one - so neither exit publishes a wait nobody can act on.
    """
    phase_map = phases(repo_root)
    session = session_facts(repo_root)
    # A supervisor advances what its own selector picked, and this layer cannot know that
    # selector - so its presence refuses the whole advance population rather than filtering
    # it. The same read `lanes` below turns on (basicly-2no50w).
    supervised = board_serve.live_holder(repo_root) is not None
    facts = board_snapshot.Facts(
        session=session,
        advances=lambda markers: board_advance.asks(
            loop_state.state_map(repo_root),
            lanes=None,
            supervised=supervised,
            last_event=board_advance.newest(markers),
        ),
        repo=repo_facts(repo_root),
        phases=phase_map,
        readiness=readiness(repo_root),
        lanes=(
            lane_facts(repo_root, session.root_issue, phase_map, lane_label=lane_label)
            if in_flight and session is not None
            # `()` is "no pass is running", which a checkout with no live lock can see and
            # which `board_sections.lanes` already separates from absent. Without it no
            # one-shot producer emitted the section at all, so `running now` read `not
            # emitted by this producer` on every unsupervised repo and could say nothing
            # else (basicly-u6eeag). None stays for the case the parameter guards: a lock
            # is live, so a pass exists whose selector this producer cannot know.
            else ()
            if board_serve.live_holder(repo_root) is None
            else None
        ),
    )
    built = board_snapshot.build_document(repo_root, facts=facts)
    asked = questions(repo_root, built)
    if asked:
        built = board_snapshot.build_document(repo_root, facts=replace(facts, questions=asked))
    return _with_live_spend(_hide_unanswerable(built), session)


def emit_tick(repo_root: Path, cadence_s: float, *, lane_label: str | None = None) -> Path:
    """Land the supervisor tick's board snapshot for *repo_root*; the path written.

    **Here rather than in `supervise`, because here is where the facts are.** A live lock hands
    production from the server to the beat - `board_serve.refresh` folds nothing while a holder
    is fresh - and the tick used to fold on the lock alone. Measured either side on this tree:
    0 phases of 234 units against `board --out`'s 234, no ready set, `repo` holding only a name
    and `IN FLIGHT` with no producer at all. The richest producer was displaced by
    the poorest exactly when a human was watching (basicly-bd4epr). `supervise` cannot reach
    this module, so the beat takes the emission as a callback instead.

    *cadence_s* is the beating thread's own interval rather than
    `supervise.HEARTBEAT_INTERVAL_S`, so a caller that pinned its interval publishes the cadence
    it keeps. `stale_after_s` is `supervise.STALE_AFTER_S`, the same question one horizon on: a
    document older than that came from a holder a contender may by now have replaced.

    **The cost is dominated by one read and it is worth knowing which.** One emission measures
    1.50 s on this repository with a lane adopted, and 7.11 s - 47% of the beat - on the same
    tree once run records exist, because :func:`grant_spend` then walks
    `policy.session_issue_ids` at 5.9 s. It runs *after* the heartbeat write, so what it delays
    is the next beat and never a landing, and 7.11 s clears `supervise.STALE_AFTER_S` by 8x.
    """
    built = document(repo_root, lane_label=lane_label, in_flight=True)
    built["freshness"] = {
        "source": board_snapshot.SUPERVISOR_TICK,
        "cadence_s": cadence_s,
        "stale_after_s": supervise.STALE_AFTER_S,
    }
    return board_snapshot.write_document(repo_root, built)
