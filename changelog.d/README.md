# Changelog fragments

One file per lane. A lane that makes a user-facing change writes its entry here
instead of editing `CHANGELOG.md`:

```text
changelog.d/<bead-id>.<category>.md
```

`<category>` is one of `added`, `changed`, `deprecated`, `removed`, `fixed`,
`security` — the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) sections.
The file holds the entry body only, no `###` heading: assembly writes the heading.

```markdown
- **One-line claim in bold.** Then the detail a consumer needs, and the bead id
  the change came from.
```

## Why the name

`d` is **directory**, following the Unix drop-in convention — `init.d`, `cron.d`,
`conf.d`, `sources.list.d`. `<name>.d` means "a directory of fragments that compose
into `<name>`", so the name itself says two things: `CHANGELOG.md` is the assembled
artifact, and you add a file here rather than editing it. (It is also the directory
name `scriv` uses for the same job; this repo implements its own scanner and takes
only the convention.)

## Why a file per lane

The filename carries the bead id, so it is unique by construction and two lanes
cannot write the same file. Editing one `CHANGELOG.md` anchor is what blocked three
of four unattended multi-lane runs: `loop preflight` reported all lanes `in band`,
two landed, and the third rebased onto an anchor that had moved twice and spent both
its rework retries there (`basicly-4746`).

## What the release does with them

`basicly release` folds every fragment into the `## [Unreleased]` body — grouped by
category, ordered by category then filename, so the section is byte-identical on any
machine — and then deletes the files in the same commit. A hand-curated
`[Unreleased]` body still publishes alongside them, and a fragment that lands in a
category the operator already opened is appended to that section rather than opening
a second heading.

A file here that is empty, or whose name does not parse, **refuses the release** and
names itself. Nothing about a lane's release note is allowed to be silent.

This `README.md` is the one name assembly skips.
