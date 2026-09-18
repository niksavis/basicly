# The kit commands

Three parts of basicly are packaged to stand alone: `basicly-tracker`, `basicly-comments`
and `basicly-tier`. This page is the reference for their terminal surface — the installer
verbs they share, and the commands each one adds. The `basicly` engine's own surface is
[`cli.md`](cli.md).

**A kit is the same source either way.** `basicly install` materializes all three under
`.basicly/core/kit/<name>` as part of the catalog, and the engine's own hooks call them there.
A kit's own installer vendors that same source to `.basicly/kit/<name>` for a repository that
has no basicly. There is no second copy to drift.

## Getting a kit

**With basicly.** `basicly install` brings all three. Nothing else to run.

**Without basicly.** One line each, and no dependency beyond a Python 3.9 floor:

```sh
uvx --from git+https://github.com/niksavis/basicly#subdirectory=packages/basicly-tracker basicly-tracker init
uvx --from git+https://github.com/niksavis/basicly#subdirectory=packages/basicly-comments basicly-comments init
uvx --from git+https://github.com/niksavis/basicly#subdirectory=packages/basicly-tier basicly-tier init
```

After `init`, plain `python3 .basicly/kit/<name>/cli.py` runs the kit with no `uvx`, no
network and nothing on `PATH`.

## The four verbs every kit shares

| Command | Behaviour |
| --- | --- |
| `basicly-<kit> init [--into PATH] [--with-instructions]` | Vendor the kit into `.basicly/kit/<kit>`, write its skill into every agent skill root, and apply any host configuration the kit needs. Idempotent |
| `basicly-<kit> update [--into PATH] [--with-instructions]` | The same converge, reporting what changed |
| `basicly-<kit> uninstall [--into PATH]` | Remove only what `init` wrote, and nothing else |
| `basicly-<kit> status [--into PATH]` | Say whether the installed copy matches this one |

`--into` names the repository to install into; it defaults to the working directory.
`--with-instructions` also writes the kit's always-on block into whichever agent instruction
files already exist, inside a marked region `uninstall` removes byte for byte. Without the
flag `init` says the block is available and where to read it.

## What each `init` writes, and what `uninstall` takes back

| Kit | Files vendored | Skill roots | Host or repository configuration |
| --- | --- | --- | --- |
| `comments` | 10 | `.claude/skills/comments`, `.agents/skills/comments` | none |
| `tracker` | 20 | `.claude/skills/tracker`, `.agents/skills/tracker` | `.gitattributes` gains `events-*.jsonl` and `pending-*.jsonl`, each `-text merge=union`; `.gitignore` gains the snapshot and checkpoint paths |
| `tier` | 7 | `.claude/skills/tier`, `.agents/skills/tier` | a `PreToolUse`/`Agent` hook in `.claude/settings.json`, pointing at the vendored hook |

[measured 2026-09-16 by installing each kit from its own built wheel into a fresh `git init`
directory]

**The tracker writes its git attribute before the first kit file, and refuses to install at
all if it cannot**, because the parallel-append promise rests on `merge=union`.

**`uninstall` leaves no residue.** The vendored tree, both skill roots, the tier hook and the
tracker's own `.gitignore` and `.gitattributes` lines all go. An `Agent` hook somebody else
wrote is left alone. `tests/test_kit_consumer_install.py` pins each of those, per kit, against
a real wheel.

## `basicly-comments`

Refuses a prose comment in a code file, and removes one when asked. A directive a tool reads
— `noqa`, `nosec`, `type: ignore`, a shebang, a licence banner — is not prose and stays.

| Command | Behaviour |
| --- | --- |
| `basicly-comments check [--skip NAME] [paths ...]` | Report every prose comment and exit 1 when one is found. Paths are files or directories |
| `basicly-comments fix [--skip NAME] [paths ...]` | Remove every prose comment, leaving the directives |
| `basicly-comments languages` | Print every file extension the kit claims |

`--skip NAME` adds one directory name to the built-in skip list, and repeats.

## `basicly-tracker`

An append-only event ledger. Every verb takes the ledger `directory` as its first positional
argument, and `create` makes that directory if it does not exist.

| Command | Behaviour |
| --- | --- |
| `basicly-tracker create DIR --prefix P [--title T] [--field NAME=VALUE] [--status S]` | Mint a record id and append its first events. The prefix is the ledger's id namespace |
| `basicly-tracker child DIR PARENT [--title T] [--field NAME=VALUE] [--status S]` | The same, as a child of an existing record |
| `basicly-tracker show DIR RECORD` | One record's folded state and both edge directions |
| `basicly-tracker list DIR [--status S] [--limit N]` | The records the ledger holds |
| `basicly-tracker ready DIR [--limit N]` | The ranked ready set: what can be worked on now |
| `basicly-tracker blocked DIR` | Each dispatchable record that is not ready, and what holds it |
| `basicly-tracker stats DIR` | Totals by status, ready and blocked |
| `basicly-tracker update DIR RECORD [--field NAME=VALUE] [--status S] [--add-label L] [--remove-label L]` | Change a record. Label flags accumulate against the record's own set; every other flag replaces |
| `basicly-tracker close DIR RECORD [RECORD ...] [--reason R]` | Close one or more records |
| `basicly-tracker comment DIR RECORD TEXT` | Append a comment |
| `basicly-tracker dep DIR RECORD TARGET [--type EDGE_TYPE]` | Add an edge. The types in use are `parent-child`, `blocks`, `related` and `discovered-from` |
| `basicly-tracker delete DIR RECORD` | Tombstone a record |
| `basicly-tracker compact DIR [--writer W]` | Fold every pending writer shard into the trunk log and unlink it. `--writer` narrows it to one and repeats |
| `basicly-tracker shards DIR` | The pending writer shards the ledger holds, with the warn and refuse thresholds |
| `basicly-tracker import DIR EXPORT [--source NAME] [--dry-run]` | Import a foreign tracker's JSONL export, keeping ids, comments and dependency edges. `--dry-run` reports the same plan and writes nothing; a re-run appends nothing |
| `basicly-tracker dor DIR RECORD` | The definition of ready. Exit 0 when the record carries a trigger, acceptance criteria and requirements; exit 1 naming what is missing and how to state it |
| `basicly-tracker board DIR [--out PATH]` | Write one self-contained HTML page: the counts, the ranked ready set, what is blocked and what holds it, a bounded dependency drawing, and every record with what it owes |

A `--field` value is read as JSON when it parses as JSON, and as a string otherwise.

`create`, `child` and `update` also take `--description`, `--acceptance` and
`--requirements`, and every one of them reports what the record still **owes**. A record
is shaped when it carries a trigger in either story voice, acceptance criteria and
requirements — as those fields, or as `## Acceptance Criteria` and `## Requirements`
sections of dash-space bullets in the description, so a record written as prose keeps working.
A placeholder counts as absent.

**`board` is a file, not a server.** It writes one HTML page with its style and its
dependency drawing inline — no script, no linked stylesheet, no network — so it opens
from disk and nothing about it updates on its own. The drawing is bounded: it takes whole
dependency clusters up to a cap and states how many records and edges it drew against how
many the ledger holds. A cluster larger than the cap is dropped whole rather than cut,
because a half-drawn cluster shows a blocked record with no arrow into it. The table
beneath carries every blocking pair either way.

**`dor` is the gate, not `ready`.** `ready` still offers every unblocked record; `dor`
exits non-zero on one that cannot be verified against, which is what a hook or an agent
skill calls before work starts.

## `basicly-tier`

A subagent declares a portable tier — `low`, `medium`, `high`, `maximum` — instead of a
provider model id, and the kit resolves it to the model that tier means for the host in play.
Two programs: a resolver anything can call, and an installer that wires the host.

### The resolver

| Command | Behaviour |
| --- | --- |
| `basicly-tier --host HOST [--name NAME]` | Resolve a tier to a concrete model and print JSON. Exit 0 with a `model` and an `alias`, or exit 1 with the `reason` it resolved nothing |

| Flag | Meaning |
| --- | --- |
| `--host {claude,codex,copilot}` | Required. The host surface to resolve for |
| `--name NAME` | Subagent name to look a definition up by |
| `--definition PATH` | Read the tier from this agent definition instead of looking one up |
| `--tier TIER` | Resolve this tier, outranking whatever the definition declares |
| `--default-tier TIER` | Tier to use for a definition that declares none |
| `--vendor VENDOR` | Override the host's default vendor |
| `--map PATH` | Path to `model-map.json`, overriding discovery |
| `--root PATH` | Directory to search from. Defaults to the working directory |

**It never substitutes a neighbouring tier.** An unavailable cell carries no model and the
resolver says which tier and which surface had none.

### The host installer

`init` runs this for you. Run it directly to change scope, to preview, or to remove the hook
without removing the kit.

| Flag | Meaning |
| --- | --- |
| `--host {claude,copilot}` | Host to install for; repeats. Defaults to every known host |
| `--user` | Install for every repository on this machine instead of just this one |
| `--root PATH` | Repository to install into. Defaults to the working directory |
| `--uninstall` | Remove the hook this script installs, leaving every other hook alone |
| `--interpreter CMD` | Command that runs the hook script, for a consumer without uv |
| `--dry-run` | Report what would be written and write nothing |

**A user-scope hook answers only for a repository that opted in.** The resolver reads the map
beside the vendored kit only when that kit sits inside the project being resolved, so a kit
installed in one repository never rewrites a spawn in another.

**A project-scope install refuses a `--root` the kit does not sit inside**, because the hook
command it would write names the script through `${CLAUDE_PROJECT_DIR}` and no
project-relative path can reach outside the project. The refusal names both remedies: install
with `--user`, or vendor the kit into that repository first.

### Which hosts it reaches

| Host | State |
| --- | --- |
| Claude | wired and measured. A declared tier reaches a `claude -p` dispatch, an in-session background subagent, and a spawn in a repository with no basicly at all |
| Copilot | **not wired.** That host selects a subagent's model in configuration rather than through a hook, so a declared tier is projected into `.github/agents` and nothing there reads it |
| Codex | resolvable by the resolver; no spawn surface is projected to |

## Where a tier is declared

In a basicly repository, a subagent source declares `tier:` and the projector writes it into
both `.claude/agents/<slug>.md` and `.github/agents/<slug>.agent.md`. In a repository without
basicly, write `tier:` into the agent file's own frontmatter; the host ignores a key it does
not know.

**A provider model id never appears in an agent file**
([architecture D-09](../architecture/architecture.md#d-09--a-provider-model-id-never-appears-in-an-agent-file)),
because a pinned id is not portable across agent families and a projected `model:` line would
disable injection rather than implement it.
