# basicly-tracker

**An append-only work tracker whose ledger merges cleanly when two people work in
parallel.** Records are events rather than rows edited in place, so two branches that each
append conflict never — which is the reason to replace a tracker whose file is rewritten on
every change. It needs no `basicly`: standard library only, no third-party package, no
network.

```console
$ uvx --from git+https://github.com/niksavis/basicly#subdirectory=packages/basicly-tracker basicly-tracker init
tracker: added to .gitattributes: events-*.jsonl -text merge=union
tracker: 31 file(s) written, 0 unchanged, in .basicly/kit/tracker
$ python3 .basicly/kit/tracker/cli.py ready .basicly/ledger
{"count": 0, "records": [], "schema": "basicly.scheduler.v1", ...}
```

That `.gitattributes` line is the point of the tracker: it is what makes two branches that
each append an event merge clean instead of conflicting. `init` writes it before it writes
a single kit file, and refuses to install at all if it cannot.

`init` copies the kit into `.basicly/kit/tracker` so it runs from plain `python3`
afterwards.

**One file instead of `uvx`.** `bundle` writes `tracker.pyz`, a single archive that
runs on any platform with Python 3.9 or later and needs no network after it is written:

```console
$ uvx --from git+https://github.com/niksavis/basicly#subdirectory=packages/basicly-tracker basicly-tracker bundle
tracker: wrote tracker.pyz; run it as python tracker.pyz <verb>
$ python tracker.pyz init
$ python tracker.pyz ready .basicly/ledger
```

On its first run the archive unpacks itself into a cache directory named after its own
content (`%LOCALAPPDATA%`, else `$XDG_CACHE_HOME`, else `~/.cache`, under `basicly-kits`),
and runs from there. It still needs Python: it is not a native executable.

**A repository that already has a backlog brings it across with `import`**, rather than
retyping it. It reads a JSONL export one record per line, keeps the ids, and carries the
comments and the dependency edges with them. Preview it first — `--dry-run` reports the
same plan by the same code path and writes nothing:

```console
$ python3 .basicly/kit/tracker/cli.py import .basicly/ledger issues.jsonl --dry-run
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
