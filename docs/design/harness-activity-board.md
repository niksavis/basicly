# Operational activity board

The default served board reads task state and meaningful activity from the same tracker
ledger. It updates cards in place; the previous harness wall remains at `/wall`, and
full backlog and task-history links remain available. `/activity` is an alias for the
new view. `?root=<record>` limits the view to an epic/task and its descendants.

The JSON read model is `/activity.json?root=<record>`. It includes at most 100 records
and five recent comments per record, with bounded text. The full history is preserved
in the ledger. Missing owners, phase, worktree, next step and checks are shown as
unrecorded rather than invented.

## Truth and observation

A record being in progress does not prove that an agent process is running. Recent
file writes, a clean worktree and branch commits do not prove running, queued, idle or
waiting states either. The unsupervised producer now leaves those runtime states
unknown while retaining its observed Git facts. Actual supervisor state is unaffected.

This first activity view reports interactive runtime presence as unknown. An agent can
record a meaningful action through the existing tracker comment interface:

```text
[activity] {"action":"Checking the consumer journey","phase":"verification","worktree":"task-name","next":"Read the result","evidence":"Focused checks passed"}
```

These are explicitly reported fields, not heartbeat evidence. Arbitrary `runtime`
values in a comment cannot manufacture running processes. Elapsed display uses the
recorded in-progress timestamp and is labelled as the time it was marked active.
Outstanding dependencies are separate from a reported blocker; they do not imply that
an owner who is working has stopped. Errors retain visible previous data with an
unavailable/disconnected indicator.

## Refresh and consumer checks

The client checks every two seconds while visible, pauses hidden-tab work, and backs
off on failure. ETags avoid unchanged JSON transfers. The derived feed caches up to
eight scopes and reloads ledger data only when append-only source file metadata changes.
This cache stores no independent task truth. Duplicate/oversized query values and
unknown roots are refused by name; text is assigned through DOM text nodes.

The 143-test affected board set passed. A recent-edit regression failed before its
correction. A second regression caught an activity timestamp incorrectly retained from
an older report; the timestamp now follows its displayed action. Handover notes remain
in history without replacing meaningful activity. Direct projection,
JavaScript parsing, Ruff and Linux Pyright checks passed.

Real shared-ledger journeys covered all 16 epic records at desktop 1440 and true mobile
390 pixels, keyboard focus, retained expanded details, and disconnect/reconnect without
a document reload. The original mobile check exposed a 427-pixel layout at a requested
390; the failing artifact is retained and wrapping was corrected. Browser evidence and
API samples are under `.basicly/usage/harness-redesign/board-activity-validation/`.

## Remaining acceptance

Task .14 remains in progress. Bind trustworthy live process observations to running,
queued, approval, idle, stale and interrupted states; complete interactive-session
instrumentation coverage and all start/block/review/finish/interruption journeys.
Measure full cold/warm UI load, navigation and action distributions on growing backlogs,
and total refresh impact against baseline-derived budgets. Warm unchanged API timings
are component measurements and establish no whole-harness speedup.
