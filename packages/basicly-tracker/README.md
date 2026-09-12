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
$ python3 .basicly/kit/tracker/cli.py ready .
{"count": 0, "records": [], "schema": "basicly.scheduler.v1", ...}
```

That `.gitattributes` line is the point of the tracker: it is what makes two branches that
each append an event merge clean instead of conflicting. `init` writes it before it writes
a single kit file, and refuses to install at all if it cannot.

`init` copies the kit into `.basicly/kit/tracker` so it runs from plain `python3`
afterwards. It also imports a beads JSONL export, so a repository can move its backlog
across without retyping it.

Full specification, including the collision budget the ids are sized from:
[`kit/SPEC.md`](../../.basicly/core/kit/tracker/SPEC.md).
