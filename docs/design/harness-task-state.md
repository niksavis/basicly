# Recover task state from the ledger

`basicly tracker show <id> --view current` reads current task fields, artifacts,
status, ownership, dependencies, every ordinary clarification and the latest
handoff of each kind from each writer. Concurrent handoffs at the same sequence
remain visible together. The default `show` still returns full folded detail.

The new view derives from the existing append-only ledger. It writes no second
state store, summary artifact or report note. Handoff prose appears once rather
than being repeated in both plain and attributed comment collections. Current
process review flags remain visible; their detailed forms are available on demand.

The view returns a `snapshot_identity` and `history_reference`. Use
`basicly tracker show <id> --view history --snapshot <snapshot_identity>` to
retrieve the full folded detail and note history at that identity. A changed
record refuses the old reference; read current state again and reassess the change.
This is a freshness check, not an approval token.

The same flags ship in the standard-library-only tracker kit. A standalone
consumer gets the capability from its installed `tracker/cli.py`, with the ledger
directory before the record argument. It needs neither the engine nor a server.

## Preserve facts rather than repeat reports

Recovery views are explicitly not complete original contracts. Ordinary notes are
never silently trimmed, even when their collection is large. Only older marked
handoffs from the same writer move behind the history reference. Retrieve them
when a current handoff leaves a missing or conflicting fact; the historical source
remains intact. Unknown markers and unattributed notes remain ordinary notes.

The owner records new decisions, changed assumptions, uncertainty, blockers,
evidence references, process ownership and next actions when current fields and
Git cannot supply them. An unchanged routine transition needs no new handover,
agent, usage report or report commit. Closed work stays closed. Check a recorded
process handle before replacing it; a polling timeout does not prove termination.

The ledger writer can have truncated historical note text before it reached a
reader. Current views and dispatch briefs now disclose the loss metadata. That
original text cannot be recovered by requesting more history. Recover required
missing facts from their original source; never call cut text complete.

## Evidence and limits

Engine and isolated Python `-I -S` checks cover both views, ordinary and late
clarifications, concurrent handoffs, source loss, large note collections and stale
retrieval. Fresh Codex and Claude consumers each completed a seeded interrupted
task and recognized an already closed task, preserved completed files and a real
owned process, and passed independent acceptance checks. The four read-only fixture ledgers
kept their original 127 events without duplicate ledger narrative.

The stress fixture's selected response was 4034 bytes for active work and 2989
bytes for closed work, versus 279167 and 278122 bytes of full folded detail. These
are payload measurements, not a controlled model latency or cost comparison.
Worker-mode curator/retro duplication and broader matched recovery/context trials
remain separate acceptance work in the existing redesign tasks.
