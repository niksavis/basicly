# basicly-tracker

**An append-only work tracker whose ledger merges cleanly when two people work in
parallel.** Records are events rather than rows edited in place, so two branches that each
append conflict never — which is the reason to replace a tracker whose file is rewritten on
every change. It needs no `basicly`: standard library only, no third-party package, no
network.

**Install the code once per machine, and keep only the ledger in each repository.** This
is the default mode:

```console
$ uv tool install 'git+https://github.com/niksavis/basicly@v0.20.2#subdirectory=packages/basicly-tracker'
$ basicly-tracker init
tracker: added to .gitattributes: events-*.jsonl -text merge=union
tracker: created the ledger .basicly/ledger
tracker: pinned the ledger to tracker 0.18.15
tracker: commit-msg now refuses a code commit on a record you do not hold
basicly-tracker: wrote the user skill to ~/.claude/skills/basicly-tracker/SKILL.md
$ basicly-tracker ready .basicly/ledger
{"count": 0, "records": [], "schema": "basicly.scheduler.v1", ...}
```

That `.gitattributes` line is the point of the tracker: it is what makes two branches that
each append an event merge clean instead of conflicting. `init` writes it first, and
refuses to install at all if it cannot.

`init` writes no kit code into the repository. It refuses, with the install command, when
no `basicly-tracker` is installed for the user. `.basicly/ledger/.kit-version` pins the
version, and every command refuses a ledger that pins another one, because an older reader
drops the event kinds it does not know.

**One file per repository instead: the sandbox mode.** `init --sandbox` writes
`.basicly/tracker.pyz`, a single archive that runs on any platform with Python 3.9 or later,
and a repository skill that names it. It needs no user install:

```sh
uvx --from git+https://github.com/niksavis/basicly#subdirectory=packages/basicly-tracker basicly-tracker init --sandbox
python3 .basicly/tracker.pyz ready .basicly/ledger
```

On its first run the archive unpacks itself into a cache directory named after its own
content (`%LOCALAPPDATA%`, else `$XDG_CACHE_HOME`, else `~/.cache`, under `basicly-kits`),
and runs from there. It still needs Python: it is not a native executable.

`init` and `init --sandbox` switch a repository between the modes and leave the files of
one mode only. A repository that vendors the older `.basicly/kit/tracker/` folder moves with
`update --sandbox`, or with `update`, which removes the folder only when the user install
accepts the pinned version. The commit hook runs the tracker from `.basicly/tracker.pyz`,
then `.basicly/kit/tracker/`, then `basicly-tracker` on PATH, and refuses the commit when it
finds none.

**A repository that already has a backlog brings it across with `import`**, rather than
retyping it. It reads a JSONL export one record per line, keeps the ids, and carries the
comments and the dependency edges with them. Preview it first — `--dry-run` reports the
same plan by the same code path and writes nothing:

```console
$ basicly-tracker import .basicly/ledger issues.jsonl --dry-run
{
  "absent": [],
  "diverged": [],
  "dry_run": true,
  "imported": ["demo-aa11", "demo-bb22"],
  "rejected": [{"reason": "not a record id", "subject": "'not-an-id'"}],
  "schema": "basicly.tracker.import.v1",
  "source": "issues.jsonl",
  "tombstoned": []
}
```

`init` finds a beads or beans backlog by its files and offers the import. It never runs
`bd`, `br` or `beans`. At a terminal it shows the dry-run plan and asks once. With no
terminal it imports nothing and prints the command, for example
`basicly-tracker init --import beads`. A bd Dolt store with no `.beads/issues.jsonl`
needs `bd export -o .beads/issues.jsonl` first. The imported records land in `refine`,
because they carry no acceptance criteria yet, so `ready` shows 0 until you shape them.

**Trial it beside br with a mirror.** `basicly-tracker init --mirror beads` imports
`.beads/issues.jsonl` and installs no claim gate, because br stays the source of truth.
`basicly-tracker sync .basicly/ledger` re-imports it, and br wins for status.
`basicly-tracker init --end-mirror` ends the trial and installs the claim gate.

A record the importer cannot name is **refused and reported, never dropped quietly**.
Re-running the same export appends nothing, so an import can be repeated while the other
tracker is still authoritative. Every imported record records where it came from, which
`--source` names if the file name is not the name you want.

**A browser board and an HTTP API are an optional add-on.** The
[`basicly-board`](../basicly-board/README.md) package serves a page on `127.0.0.1:8765`
where a person reads, writes and edits stories, and an agent refinement pass shapes them
before they are ready. Its API returns the same versioned JSON as these commands.

Every command, with one example each:
[`kit/tracker/REFERENCE.md`](../../.basicly/core/kit/tracker/REFERENCE.md).

Full specification, including the collision budget the ids are sized from:
[`kit/SPEC.md`](../../.basicly/core/kit/tracker/SPEC.md).

## Install with a coding agent

Give a coding agent the link to this section, or copy the prompt into it. Run the agent
in the root of your repository. The agent installs the tracker and the board, and it asks
you before it imports a backlog.

```text
Install the basicly work tracker and its board in this repository, as
https://github.com/niksavis/basicly/blob/main/packages/basicly-tracker/README.md#install-with-a-coding-agent
describes. Do the four steps in order.

1. Install. Run these commands in the repository root:
   uv tool install 'git+https://github.com/niksavis/basicly@v0.20.2#subdirectory=packages/basicly-tracker'
   uv tool install 'git+https://github.com/niksavis/basicly@v0.20.2#subdirectory=packages/basicly-board'
   basicly-tracker init
   basicly-board init
2. Configure. When init reports a beads or beans backlog, tell me how many records it holds.
   Then stop and ask me whether to import it. Never import without my yes.
   On yes, run the import command that init printed, for example `basicly-tracker init --import beans`.
   Set the id prefix with `basicly-tracker config .basicly/ledger set prefix <prefix>`.
   Use the prefix of the imported ids, or else a short lowercase name for this repository.
3. Use. `basicly-tracker ready .basicly/ledger` lists the work that can start now.
   `basicly-tracker refine .basicly/ledger` lists the records that need acceptance criteria first.
   An imported record starts in refine. Tell me both counts.
4. See. Start the board in the background with `basicly-board serve .basicly/ledger`.
   When port 8765 is taken, add `--port` with a free port.
   Check that the URL it prints answers, then give me the URL.
   The board can stop when this session ends. Give me the serve command that starts it again.
```

The same commands at a terminal end in the same state. At a terminal, `init` asks the
import question itself.
