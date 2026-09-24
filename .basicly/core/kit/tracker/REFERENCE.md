# Tracker kit command reference

Every command takes the ledger directory, `.basicly/ledger`, as its first argument and
prints one JSON object with a `schema` field. `cli.py <command> --help` lists every flag.
The examples use `acme` as the id prefix and `acme-a1b2` as a record id.

## Write

### create

Mint a record id and append its first events. `owed` in the output names what the record
still needs before `dor` passes.

```sh
python3 .basicly/kit/tracker/cli.py create .basicly/ledger --prefix acme --title "Keep comments on export" --description "When an export holds a comment, I want it kept, so I can import it back." --acceptance "- The importer shall keep every comment line" --requirements "- Standard library only"
```

### child

Mint the next child id under a parent and record the parent-child edge.

```sh
python3 .basicly/kit/tracker/cli.py child .basicly/ledger acme-a1b2 --title "Parse multi-line comments"
```

### update

Set fields, the status or labels. `--add-label` and `--remove-label` repeat.

```sh
python3 .basicly/kit/tracker/cli.py update .basicly/ledger acme-a1b2 --status in_progress --add-label export
```

### comment

Append one comment.

```sh
python3 .basicly/kit/tracker/cli.py comment .basicly/ledger acme-a1b2 "The export drops a trailing newline."
```

### dep

Record that the first record waits on the second. `--type` sets the edge type, `blocks`
when omitted. An edge that would make a cycle is refused.

```sh
python3 .basicly/kit/tracker/cli.py dep .basicly/ledger acme-a1b2 acme-c3d4
```

### close

Move one or more records to `closed`. The reason is the permanent record of what shipped.

```sh
python3 .basicly/kit/tracker/cli.py close .basicly/ledger acme-a1b2 --reason "Shipped the importer fix; the round-trip test passes"
```

### delete

Tombstone a record. Its id is never reused.

```sh
python3 .basicly/kit/tracker/cli.py delete .basicly/ledger acme-a1b2
```

### import

Bring a JSONL export from another tracker across, one record per line. `--dry-run`
reports the same plan and writes nothing.

```sh
python3 .basicly/kit/tracker/cli.py import .basicly/ledger issues.jsonl --dry-run
```

## Read

### ready

The ranked records that can be worked on now. It leaves out a record labelled `refine`.

```sh
python3 .basicly/kit/tracker/cli.py ready .basicly/ledger --limit 10
```

### blocked

Each record that is not ready, and what holds it.

```sh
python3 .basicly/kit/tracker/cli.py blocked .basicly/ledger
```

### stats

Counts by status, with the ready and blocked counts.

```sh
python3 .basicly/kit/tracker/cli.py stats .basicly/ledger
```

### show

One record's folded state and its edges in both directions.

```sh
python3 .basicly/kit/tracker/cli.py show .basicly/ledger acme-a1b2
```

### list

Every record, filtered by `--status` and cut by `--limit`.

```sh
python3 .basicly/kit/tracker/cli.py list .basicly/ledger --status open --limit 20
```

## Shape

### dor

The definition of ready. Exit 0 when the record carries everything the rule requires,
exit 1 with what is missing and how to add it.

```sh
python3 .basicly/kit/tracker/cli.py dor .basicly/ledger acme-a1b2
```

### scaffold

The headings, description skeleton and flags a record of one type must carry, read from
the ledger's `template.json` when there is one.

```sh
python3 .basicly/kit/tracker/cli.py scaffold .basicly/ledger --type bug
```

### fields

Each record field, its role and the code or person that reads it. A write of a field
that is not in the table, or of an import-history field, is refused. `show` prints the
derived `dates` (created, updated, closed) computed from the event times.

```sh
python3 .basicly/kit/tracker/cli.py fields .basicly/ledger
```

### refine

The open records a refinement pass owes: each one carries the `refine` label or fails
`dor`. A person writes or edits a story, and the board page adds the label. An agent then
rewrites the record with the full fields (trigger, acceptance criteria, requirements,
type, priority, edges) and removes the label with `update --remove-label refine`.

```sh
python3 .basicly/kit/tracker/cli.py refine .basicly/ledger
```

## Keep the log healthy

### fsck

Fold the whole log and report anything broken. Exit 0 clean, 1 stale derivative, 2 broken.
`--rebuild` writes the derived files again first.

```sh
python3 .basicly/kit/tracker/cli.py fsck .basicly/ledger
```

### shards

The pending writer shards the ledger holds.

```sh
python3 .basicly/kit/tracker/cli.py shards .basicly/ledger
```

### compact

Fold every pending shard into the trunk log. Run it on the default branch as its own
pull request; `init --fold-on-merge` runs it after a merge where one writer pushes there.

```sh
python3 .basicly/kit/tracker/cli.py compact .basicly/ledger
```

## Show

### board

Write one self-contained HTML page: the counts, the ready set, what is blocked, a
dependency drawing and every record with what it still owes.

```sh
python3 .basicly/kit/tracker/cli.py board .basicly/ledger --out tracker-board.html
```
