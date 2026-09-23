---
name: work-tracker
description: Use the append-only work tracker as this repository's issue tracker — read what is ready, file and close records, record dependencies, and reference an id from a commit. Use when planning work, deciding what to do next, filing or closing an issue, or preparing a commit that must name one.
---

# The work tracker

**An append-only event ledger under `.basicly/ledger/`, committed with the code.** Records
are events, never rows edited in place, which is why two people or two agents working the
same backlog in parallel do not conflict. There is no server, no database and no binary to
install.

## Why it is append-only, and what that buys you

A tracker whose file is rewritten on every change conflicts the moment two branches touch
the backlog, and resolving it by hand risks losing a record. Here every write appends one
event to `events-*.jsonl`, and the install declares `merge=union` on that glob, so two
branches that each append merge clean and keep both events. State is *folded* from the log,
so the fold is the same whichever branch merged first.

That is the reason to prefer it, and it only holds if the git attribute is present. Check
with `basicly-tracker status` if a merge ever conflicts on the log.

## Read before you write

Every subcommand takes the ledger directory, `.basicly/ledger`, as its first argument.
`init` creates it. The repository root, or a directory that holds no ledger, is refused
by name, never answered as an empty backlog.

```sh
python3 .basicly/kit/tracker/cli.py ready .basicly/ledger        # what is workable now, ranked
python3 .basicly/kit/tracker/cli.py blocked .basicly/ledger      # what is waiting, and on what
python3 .basicly/kit/tracker/cli.py stats .basicly/ledger        # counts by status
python3 .basicly/kit/tracker/cli.py show .basicly/ledger <id>    # one record in full
python3 .basicly/kit/tracker/cli.py list .basicly/ledger         # every record
```

`ready` is the one to start from: it excludes anything blocked by an open dependency and
ranks what is left, so the top row is the next thing to do. Read it before proposing work
rather than inventing a task.

## Write

```sh
python3 .basicly/kit/tracker/cli.py create .basicly/ledger --prefix <p> --title "<what>" \
    --description "<the trigger>" --acceptance "<how it is checked>" --requirements "<the standard>"
python3 .basicly/kit/tracker/cli.py update .basicly/ledger <id> --status in_progress
python3 .basicly/kit/tracker/cli.py comment .basicly/ledger <id> "<what you learned>"
python3 .basicly/kit/tracker/cli.py dep .basicly/ledger <id> <the-id-it-waits-on>
python3 .basicly/kit/tracker/cli.py close .basicly/ledger <id> --reason "<what shipped, and the evidence>"
python3 .basicly/kit/tracker/cli.py child .basicly/ledger <parent-id> --title "<a piece of it>"
```

Run `cli.py <verb> --help` for the exact flags; they are checked and a wrong one is refused
by name rather than ignored. `.basicly/kit/tracker/REFERENCE.md` lists every command with
one example.

## Check the log itself

```sh
python3 .basicly/kit/tracker/cli.py fsck .basicly/ledger            # exit 0 clean, 1 stale derivative, 2 broken
python3 .basicly/kit/tracker/cli.py fsck .basicly/ledger --rebuild  # write the derivatives again first
```

The log is the truth and everything else is derived from it, which is only worth saying if
you can check it. Run this after a merge you are unsure about, or when a query answers
something that surprises you. A finding names the record and the reason; a broken log is
repaired by appending a corrective event, never by editing a line.

## After a merge

A write appends to `pending-<branch>.jsonl`, so two branches never edit one file, and
every read folds the shards with the trunk log. So a merge needs nothing from you.

**Folding the shards is maintenance, not correctness.** It only bounds the file count;
`fsck` warns above 1,000 shards. Run it on the default branch as its own pull request:

```sh
python3 .basicly/kit/tracker/cli.py compact .basicly/ledger
```

**Do not fold automatically where the default branch takes pull requests.** A fold edits
the trunk log, and two pull requests that each carry a different fold conflict on the
forge. That was measured: every automatic fold outside one writer made a pull request
conflict. Only a repository where one writer pushes straight to the default branch should
pass `init --fold-on-merge`, which wires a `post-merge` hook that folds there and nowhere
else.

## Show a human where the work stands

```sh
python3 .basicly/kit/tracker/cli.py board .basicly/ledger --out tracker-board.html
```

One self-contained page: the counts, the ranked ready set, what is blocked and what holds
it, a dependency drawing, and every record with what it still owes. No server and no
network — it is a file, and nothing on it updates until you run the command again. Write
it when someone asks what the state of the work is, rather than pasting JSON at them.

## Shape a record before you build against it

A record is **shaped** when it carries three things: a trigger in either story voice, the
acceptance criteria a check is derived from, and the requirements validation judges the
built thing against. Every write prints what the record still `owed`, and the gate refuses
one that is not shaped:

```sh
python3 .basicly/kit/tracker/cli.py dor .basicly/ledger <id>     # exit 0 ready, exit 1 with what is missing
```

Run it before you start work, not after. The three sections are what you verify and
validate against; without them you are checking the code against your own reading of a
title, which is the failure this tracker exists to stop.

State the trigger as a situation — *"When <situation>, I want to <motivation>, so I can
<outcome>."* — or as a persona — *"As a <persona>, I want <goal>, so that <benefit>."* A
persona is never required: where a situation triggers the work and nobody in particular
wants it, inventing one is the defect. A placeholder counts as absent, so pasting either
template unfilled does not satisfy the gate.

`--acceptance` and `--requirements` are the only route on an open record: a
`## Acceptance Criteria` or `## Requirements` section in the description is read only once
the record is closed, as evidence of what it held. `scaffold` prints the body and flags a
record of one type must carry, so you fill it in rather than guess:

```sh
python3 .basicly/kit/tracker/cli.py scaffold .basicly/ledger --type bug
```

### Your own record template

A `template.json` beside the log changes what a record must carry. `extend` adds sections
to the three above, and `override` replaces them. `types` adds sections for one record
type, read from the `issue_type` field:

```json
{
  "mode": "extend",
  "sections": ["## Risks"],
  "types": {"bug": ["## Steps to Reproduce"]}
}
```

A section is satisfied by that heading with content in the description, or by a field
named after it: `--field risks="<text>"`, `--field steps_to_reproduce="<text>"`. `dor`,
`create`, `board` and the basicly engine all read the same file. A malformed template is
refused by name, never ignored.

### Write a record someone else can build

- **One record, one change a person can see.** If the acceptance criteria describe two
  outcomes that can ship apart, file two records and add a `dep` edge between them.
- **Small enough to finish.** A record that needs more than one working session is two
  records. Use `child` to split a large one, and keep the parent as the anchor.
- **Criteria a check can derive.** Write each criterion as a trigger and a response:
  *"When <event>, the <system> shall <response>."* One bullet is one check.
- **Name the standard, not the method.** Requirements say what the result must obey, such
  as a platform, a limit or a rule. They do not say how to build it.

A record the gate refuses, then the same record shaped:

```sh
python3 .basicly/kit/tracker/cli.py create .basicly/ledger --prefix acme --title "fix export"
python3 .basicly/kit/tracker/cli.py create .basicly/ledger --prefix acme --title "fix export" \
    --description "When an export holds a comment with a newline, I want it kept, so I can import it back unchanged." \
    --acceptance "- When the export holds a multi-line comment, the importer shall keep every line" \
    --requirements "- Standard library only"
```

The first prints three sections in `owed`. The second prints none.

**`ready` is not the gate.** It offers every unblocked record, shaped or not; `dor` is what
refuses. Read `ready` to choose, then run `dor` before you build.

## How to use it well

- **Claim before you build.** Set the record to in-progress so a second agent reading
  `ready` does not pick up the same thing.
- **A close reason is evidence, not a summary.** Name what shipped, what was run, and the
  number it produced. The reason is the permanent record; the diff is not searchable.
- **Put a finding on the record, not in a comment in the code.** The ledger is where a
  measurement stays true and stays attributable.
- **Reference the id in the commit message.** That is the only link between a change and
  why it was made.
- **Shape it as you file it.** `create` takes the three sections; adding them later costs
  a second write and the record is unusable in between.
- **File the thing you noticed.** A defect you found and did not file is one nobody else
  can see; `create` costs one command.

## What it will refuse

A record id that does not exist. A dependency edge that would make a cycle. A write to a
record the snapshot says is already closed, unless you say so deliberately. Each refusal
names the reason; read it rather than working around it.
