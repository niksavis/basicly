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

Every subcommand takes the repository directory as its first argument.

```sh
python3 .basicly/kit/tracker/cli.py ready .        # what is workable now, ranked
python3 .basicly/kit/tracker/cli.py blocked .      # what is waiting, and on what
python3 .basicly/kit/tracker/cli.py stats .        # counts by status
python3 .basicly/kit/tracker/cli.py show . <id>    # one record in full
python3 .basicly/kit/tracker/cli.py list .         # every record
```

`ready` is the one to start from: it excludes anything blocked by an open dependency and
ranks what is left, so the top row is the next thing to do. Read it before proposing work
rather than inventing a task.

## Write

```sh
python3 .basicly/kit/tracker/cli.py create . --prefix <p> --title "<what>"
python3 .basicly/kit/tracker/cli.py update . <id> --status in_progress
python3 .basicly/kit/tracker/cli.py comment . <id> "<what you learned>"
python3 .basicly/kit/tracker/cli.py dep . <id> --blocked-by <other-id>
python3 .basicly/kit/tracker/cli.py close . <id> --reason "<what shipped, and the evidence>"
python3 .basicly/kit/tracker/cli.py child . <parent-id> --title "<a piece of it>"
```

Run `cli.py <verb> --help` for the exact flags; they are checked and a wrong one is refused
by name rather than ignored.

## How to use it well

- **Claim before you build.** Set the record to in-progress so a second agent reading
  `ready` does not pick up the same thing.
- **A close reason is evidence, not a summary.** Name what shipped, what was run, and the
  number it produced. The reason is the permanent record; the diff is not searchable.
- **Put a finding on the record, not in a comment in the code.** The ledger is where a
  measurement stays true and stays attributable.
- **Reference the id in the commit message.** That is the only link between a change and
  why it was made.
- **File the thing you noticed.** A defect you found and did not file is one nobody else
  can see; `create` costs one command.

## What it will refuse

A record id that does not exist. A dependency edge that would make a cycle. A write to a
record the snapshot says is already closed, unless you say so deliberately. Each refusal
names the reason; read it rather than working around it.
