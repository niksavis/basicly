# basicly-tracker

**An append-only work tracker whose ledger merges cleanly when two people work in
parallel.** Records are events rather than rows edited in place, so two branches that each
append conflict never — which is the reason to replace a tracker whose file is rewritten on
every change. It needs no `basicly`: standard library only, no third-party package, no
network.

```console
$ uvx --from git+https://github.com/niksavis/basicly#subdirectory=packages/basicly-tracker basicly-tracker init
tracker: added to .gitattributes: events-*.jsonl -text merge=union
tracker: 18 file(s) written, 0 unchanged, in .basicly/kit/tracker
$ python3 .basicly/kit/tracker/cli.py ready .basicly/ledger
{"count": 0, "records": [], "schema": "basicly.scheduler.v1", ...}
```

That `.gitattributes` line is the point of the tracker: it is what makes two branches that
each append an event merge clean instead of conflicting. `init` writes it before it writes
a single kit file, and refuses to install at all if it cannot.

`init` copies the kit into `.basicly/kit/tracker` so it runs from plain `python3`
afterwards.

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

A record the importer cannot name is **refused and reported, never dropped quietly**.
Re-running the same export appends nothing, so an import can be repeated while the other
tracker is still authoritative. Every imported record records where it came from, which
`--source` names if the file name is not the name you want.

Full specification, including the collision budget the ids are sized from:
[`kit/SPEC.md`](../../.basicly/core/kit/tracker/SPEC.md).
