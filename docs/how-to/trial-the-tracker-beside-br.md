# Trial the tracker beside br

Use this guide when a repository already uses br (beads_rust), and you want to run the
basicly tracker and its board beside it until you decide that it works. br stays the source
of truth during the trial. The basicly ledger is a mirror that you re-import from
`.beads/issues.jsonl`.

## 1. Install the tracker and the board once per machine

```sh
uv tool install 'git+https://github.com/niksavis/basicly@v0.21.2#subdirectory=packages/basicly-tracker'
uv tool install 'git+https://github.com/niksavis/basicly@v0.21.2#subdirectory=packages/basicly-board'
```

Each `init` also writes a skill to `~/.claude/skills/`, so an agent in any repository with a
ledger knows the commands.

To keep one file per repository and no user install, pass `--sandbox` to each `init` below.
The commands then start with `python3 .basicly/tracker.pyz` and
`python3 .basicly/board.pyz`.

## 2. Start the mirror

Run these in the root of the repository:

```sh
basicly-tracker init --mirror beads
basicly-board init
```

`init --mirror beads` does four things:

- It imports every record, comment and edge from `.beads/issues.jsonl`.
- It sets the ledger id prefix from `issue_prefix` in `.beads/config.yaml`.
- It writes `.basicly/ledger/mirror.json`, which marks the ledger as a mirror.
- It installs no claim gate, so a commit that names a record that you claimed in br lands.

br files are never written. Commit the ledger with the rest of the change.

## 3. Keep the mirror current

After you work in br, re-import it:

```sh
basicly-tracker sync .basicly/ledger
```

The report names:

- `imported`: new records in br;
- `status_changed`: records whose status moved, with the old and the new status. br wins
  for status;
- `diverged`: records whose title or other fields changed in br. The mirror reports these,
  and it does not copy them;
- `absent`: records that br no longer holds;
- `stale`: a warning when `.beads/beads.db` is newer than the export. Run
  `br sync --flush-only`, then sync again.

Add `--dry-run` to see the report and write nothing.

## 4. Compare the two in the browser

```sh
basicly-board serve .basicly/ledger
```

Open the address that it prints. A banner says that the ledger mirrors br. The page opens on
**All open**, because an imported record has no acceptance criteria yet, so it lands in
**Refine** and not in **Ready**. When port 8765 is taken, add `--port` with a free port.

## 5. End the trial

When you decide that the tracker works, stop writing to br and end the mirror:

```sh
basicly-tracker init --end-mirror
```

It removes `mirror.json` and installs the claim gate. From then on, a code commit must name
a record that you hold in the basicly ledger. A plain `init` or `update` keeps the mirror and
says how to end it.

Before you delete `.beads/`, list its files and account for each one. A check of what the
import carried cannot see a file that the import never read.
