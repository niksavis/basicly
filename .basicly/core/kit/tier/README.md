# Tier injection kit

**A subagent declares a portable tier; this kit makes the spawn actually run on the
model that tier resolves to.** Three Python files and one JSON map, with **no
basicly**: no `import basicly`, nothing on `PATH`, no third-party package, no
network. Copy them into a repository that has never heard of this harness and they
work.

| File | What it does |
| --- | --- |
| `tier_resolver.py` | answers _which model_ a tier means, for one host surface |
| `claude_tier_hook.py` | rewrites a Claude Code spawn to use it |
| `install_hook.py` | wires the hook into the host's settings |
| `../models/model-map.json` | the committed data all three read ([contract](../models/README.md)) |

The `tier-injection` skill is the entry point for using it. This file is the
reference for how it behaves and where it stops.

## The hybrid: one host resolves at spawn time, the other cannot

| Host | How a tier is applied | Why |
| --- | --- | --- |
| **Claude Code** | **dynamically**, at spawn time, by the hook here | it exposes a `PreToolUse` hook that can rewrite the `Agent` tool's input |
| **Copilot CLI** | **statically** — frontmatter in the definition, plus `copilot --model` for the session | it exposes no hook that fires for a spawn, so there is nothing to intercept |

Dynamic is preferred because a model pinned into a definition file is a fact
duplicated in every definition, and it goes stale silently. The static path is the
documented **fallback**, not a second-class accident.

The copilot finding is measured, not assumed, and is pinned to **CLI 1.0.77**
(copilot self-updates). Across three probes a repo-level `.github/hooks/*.json`
hook never fired — on agent delegation, on a shell tool with `--allow-all-tools`,
and on the same without it — and 1.0.77 has no hooks directory under `~/.copilot`,
no hook key in its `settings.json` and no hook option in `--help`.

So the installer **declines for copilot and says why**, rather than reporting a
success for a hook that would never fire:

```console
$ python3 .basicly/core/kit/tier/install_hook.py --host copilot
copilot: nothing installed - the Copilot CLI exposes no hook surface that fires
for a spawn (...); use static frontmatter plus `copilot --model` instead
```

It exits **1**, so a script can branch on it without parsing the report.

## Install

```bash
python3 .basicly/core/kit/tier/install_hook.py --dry-run   # print what it would write
python3 .basicly/core/kit/tier/install_hook.py             # this repository
python3 .basicly/core/kit/tier/install_hook.py --user      # every repository on this machine
```

Re-running converges: it never duplicates the hook, and it matches hooks by the
script they run, so one you wrote yourself is never touched. A `settings.json` that
exists but cannot be parsed is **refused, never overwritten**.

**Then quit and relaunch the host — the whole CLI process.** Hooks and agent
definitions are read when the process starts. Clearing the conversation reloads
neither, so the hook will appear to do nothing while every diagnostic says it is
correctly installed. This is the first thing a new consumer hits.

### The two scopes are written differently, on purpose

A repository's `.claude/settings.json` is **committed and shared**, so it gets a
command with nothing machine-specific in it:

```json
"command": "uv run --no-project --no-python-downloads python \"${CLAUDE_PROJECT_DIR}/.basicly/core/kit/tier/claude_tier_hook.py\""
```

`${CLAUDE_PROJECT_DIR}` is substituted by the host itself, before any shell sees
it, so it resolves to the project root whatever the working directory is and it
works under PowerShell too. `--no-python-downloads` keeps the spawn path network
free: it fails closed rather than fetching an interpreter mid-spawn. Pass
`--interpreter "py -3"` if you have no `uv`.

`~/.claude/settings.json` is machine-local, so `--user` keeps absolute paths and
needs nothing on `PATH`. A project-scope install that cannot name the hook relative
to the repository **refuses** rather than falling back to an absolute path.

## Check a resolution without spawning anything

The resolver is a CLI in its own right. It prints one JSON object and exits 0 when
it resolved, 1 when it did not.

```console
$ python3 .basicly/core/kit/tier/tier_resolver.py --host claude --tier low
{"alias": "haiku", "model": "claude-haiku-4-5", "reason": null, "source": "argument",
 "surface": "anthropic", "tier": "low", "vendor": "anthropic"}
```

Surface matters, and not only for spelling — the same model is `claude-haiku-4-5`
to Anthropic and `claude-haiku-4.5` to Copilot:

```console
$ python3 .basicly/core/kit/tier/tier_resolver.py --host copilot --tier low
{"alias": null, "model": "claude-haiku-4.5", ... "surface": "github-copilot", ...}
```

`--name` looks a definition up by subagent name, and `--default-tier` supplies one
for a definition that declares none:

```console
$ python3 .basicly/core/kit/tier/tier_resolver.py --host claude --name code-reviewer --default-tier medium
{"alias": "sonnet", "model": "claude-sonnet-5", ... "source": "default", "tier": "medium", ...}
```

**It fails closed.** An unavailable cell carries no model, and the resolver never
substitutes a neighbouring tier's:

```console
$ python3 .basicly/core/kit/tier/tier_resolver.py --host copilot --tier low --vendor google
{"alias": null, "model": null, "reason": "google low is unavailable on github-copilot:
 provider 'github-copilot' serves no model named 'Gemini 3.1 Flash Lite'", ...}
$ echo $?
1
```

## Drive the map from another harness, with no basicly

The kit is four files. Copy them anywhere, keep the two directories beside each
other or point `--map` wherever you put the map, and call it:

```console
$ find . -type f
./kit/tier/claude_tier_hook.py
./kit/tier/install_hook.py
./kit/tier/tier_resolver.py
./models/model-map.json

$ env -i python3 -S -I kit/tier/tier_resolver.py --host claude --tier high --map models/model-map.json
{"alias": "opus", "model": "claude-opus-5", ... "tier": "high", "vendor": "anthropic"}
```

That is an **empty environment** — no `PATH`, no `HOME`, `-S` for no `site`, `-I`
for isolated mode — which is how the no-basicly constraint is checked rather than
merely claimed. Your harness reads `model`, or `alias` where the surface wants the
short form, and pins it however it pins models. The JSON is the contract.

## Traps

Four, each of which has cost real debugging time.

1. **`updatedInput` replaces the tool input, it does not merge into it** — contrary
   to the general hooks documentation. The hook therefore copies every original key
   through and adds only `model`. Drop a key and it is gone from the spawn.
2. **`model` is absent from `tool_input` unless the caller set it**, so it has to be
   **added**, not rewritten. Code that looks for an existing key to overwrite finds
   nothing and does nothing.
3. **The `Agent` tool's `model` is an alias, not an id.** Measured against the
   2.1.220 binary: it is `enum(["sonnet","opus","haiku","fable"])`, so
   `claude-opus-5` is rejected there — a different vocabulary from the same host's
   definition _frontmatter_, which takes a full id. The hook injects the alias.
4. **`CLAUDE_CODE_SUBAGENT_MODEL` outranks** the per-invocation parameter the hook
   writes. Where it is set, every injection is inert and the hook stays deliberately
   silent. **Check that variable first** when an injection appears not to work.

## Debugging an injection

Drive the hook the way the host does — a `PreToolUse` payload on stdin:

```console
$ printf '%s' '{"tool_name":"Agent","cwd":"'"$PWD"'","tool_input":{"subagent_type":"my-agent","prompt":"x"}}' \
    | python3 .basicly/core/kit/tier/claude_tier_hook.py
{"hookSpecificOutput": {"hookEventName": "PreToolUse",
 "updatedInput": {"model": "haiku", "prompt": "x", "subagent_type": "my-agent"}}}
```

**Silence is a valid answer**, and the common one. The hook declines — exit 0, empty
stdout, host default stands — in six cases: the call is not an `Agent` spawn;
`CLAUDE_CODE_SUBAGENT_MODEL` is set; the input or the frontmatter already names a
model; the spawn's directory tree has no map; or nothing resolves (no tier, an
unknown tier, or a cell the map marks unavailable). A definition declaring no tier
is the usual reason:

```console
$ printf '%s' '{"tool_name":"Agent","cwd":"'"$PWD"'","tool_input":{"subagent_type":"code-reviewer","prompt":"x"}}' \
    | python3 .basicly/core/kit/tier/claude_tier_hook.py
$ echo $?
0
```

To tell "declined" from "broken", ask the resolver the same question directly — it
reports a `reason` where the hook is silent.

## Constraints this kit must keep

The kit-wide constraints are in [`../README.md`](../README.md). These are this kit's own.

- **Fail closed.** An unavailable cell carries no `model` key, so a lookup raises
  rather than defaulting, and `alias` is never set without `model`.
- **A bug here must never stop an agent from spawning.** Malformed input, an
  unreadable file, or any unexpected error exits 0 with no output. This is a
  convenience in the spawn path, not a security boundary.
- **The map is looked up from the spawn's own directory tree**, with the
  kit-adjacent fallback off. The kit is always beside itself, so with that fallback
  on, a hook installed once per machine would inject a model into every unrelated
  repository on it.

## Where the evidence lives

The design note is [`docs/design/tier-kit.md`](../../../../docs/design/tier-kit.md).
The beads carry the measurements: `basicly-wbsz.1` the resolver, `wbsz.2` the hook
and the alias finding, `wbsz.3` the installer and the live end-to-end proof,
`basicly-dukb` the portable project-scope command.
