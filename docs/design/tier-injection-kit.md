# Tier Injection Kit — Why It Is Hybrid, And Where The Line Falls

Status: **shipped.** Built over `basicly-wbsz` (`.1` resolver, `.2` hook, `.3` installer,
`.4` this note), with `basicly-dukb` fixing the installer's project-scope rendering. The
operational reference is the kit's own
[`README`](../../.basicly/core/kit/README.md); this document is the _why_, and the record
of what was measured rather than assumed.

The problem it solves: **no provider model id is portable, so an agent source cannot name
one.** A source declares a portable tier (`low`/`medium`/`high`/`maximum`) and the
[committed map](../../.basicly/core/models/README.md) resolves it per vendor and per
surface. That leaves one question, which is this kit: **how does the declared tier reach
the spawn?**

## 1. Two ways for a tier to reach a spawn, and why we prefer the harder one

| | Static emission | Dynamic injection |
| --- | --- | --- |
| Mechanism | write the resolved model into each agent definition | rewrite the spawn at spawn time |
| Bead | `basicly-a3yi` | `basicly-wbsz` — **this kit** |
| Where the fact lives | duplicated in every definition file | once, in the map |
| Goes stale | silently, on any map change | cannot |
| Works for a consumer's own agent | only if the emitter knows about it | yes — keys off the declared tier |
| Needs a host hook | no | **yes** |

**Decision (owner, 2026-07-31): injection is preferred, static emission is the
fallback.** A model value should not be written into a definition file; it should be
controlled at spawn time by whatever spawns the subagent. The reasoning is that a
duplicated fact is a fact that will disagree with itself, and the map is the thing that
changes.

Two further requirements made this its own deliverable rather than part of `a3yi`:

1. **It must work with no basicly harness present.** The unit of delivery is therefore a
   _kit_ — the committed map plus three small files — consumable with zero basicly
   imports and no `basicly` on `PATH`. Checked by driving it under `env -i` with `-S -I`,
   not by asserting it.
2. **It must work for an agent the consumer wrote themselves.** So resolution keys off
   something present on any definition — a declared tier, or a configured default for one
   that declares none — never off basicly's own catalog.

## 2. Why it is hybrid, and why that is not a compromise

Injection needs a host that lets something intercept a spawn. Exactly one of our two
v1 hosts does.

**Claude Code: proven, live.** A `PreToolUse` hook matching the `Agent` tool, returning
`updatedInput`, overrides the definition's frontmatter. First demonstrated on
`basicly-a3yi`: frontmatter declared `opus`, the hook injected `haiku`, and the subagent's
transcript recorded 22 messages on `claude-haiku-4-5-20251001` with no `opus` anywhere in
the run. Then re-proven end to end for the _shipped_ kit on `wbsz.3` — see §4.

**Copilot CLI: cannot, today.** `preToolUse` with `modifiedArgs` is documented, but a
repo-level `.github/hooks/*.json` hook never fired across three probes (agent delegation;
a shell tool with `--allow-all-tools`; the same without it), and on 1.0.77 there is no
hook surface at all: no hooks directory under `~/.copilot`, no hook key in its
`settings.json`, no hook option in `--help`. Possibly an SDK-only capability. Copilot
self-updates, so this finding is pinned to **1.0.77**.

So a first version is **necessarily** hybrid: dynamic on claude, static frontmatter plus
session-level `copilot --model` on copilot. The installer encodes this as data rather than
a branch, and **declines for copilot while saying why** — reporting success for a hook
that will never fire is the worse outcome, because it moves the failure to the point where
nothing can diagnose it.

Gemini, Cursor and other hosts are **deferred past v1.0.0** by owner decision. The kit's
shape must not hard-code a two-host assumption, but their adapters are not designed now.

### A dead probe, recorded so it is not repeated

`strings` on the copilot binary is **not** evidence. It returned zero for `preToolUse`,
`modifiedArgs` **and** `subagent_type` — and `subagent_type` is a positive control that
must be present, so the zero is a property of the probe (the 176M bundle is compressed),
not of the binary. The same technique on the claude binary hits four controls and is
sound. Never cite a `strings` zero on the copilot binary.

## 3. The four traps the rewrite depends on

Each was measured, and the first two are load-bearing: get either wrong and the hook is
silently useless or actively destructive.

1. **`updatedInput` replaces the tool input rather than merging into it**, contradicting
   the general hooks documentation. The hook must echo the whole original input dict and
   add to it. A merge-shaped implementation drops every key it does not restate.
2. **`model` is absent from `tool_input` unless the caller set it.** It must be _added_,
   not rewritten. An implementation that looks for the key to overwrite finds nothing and
   does nothing — and looks like a hook that is not firing.
3. **The `Agent` tool's `model` is an alias, not an id** — `enum(["sonnet","opus","haiku",
   "fable"])` in the 2.1.220 binary. The resolver's full `claude-opus-5` is rejected
   there, though the same host's definition _frontmatter_ documents a full id as legal:
   two surfaces of one host with different vocabularies. Corroborated from a second
   direction by the `a3yi` experiment, which injected the bare alias `haiku` and worked.
   The tier-to-alias table therefore lives **on the kit**, beside `HOST_SURFACES`, with a
   test holding it to the map through `models.same_model` — chosen over deriving it from
   the map's `family` at runtime, where an upstream rename would silently change an
   injected alias.
4. **`CLAUDE_CODE_SUBAGENT_MODEL` outranks** the per-invocation parameter the hook writes.
   Where it is set, every injection is inert; the hook detects this and stays silent
   rather than pretending. It is the first thing to check when an injection "fails".

And one that is not a trap in the mechanism but in the _operation_ of it:

**Installing the hook has no effect until the host CLI process is restarted.** Clearing
the conversation reloads neither hooks nor agent definitions. Measured with a control on
2026-08-01: a definition written by an earlier conversation _and_ a brand-new one written
seconds earlier were both rejected as "Agent type not found" in a conversation begun by
`/clear`, while agents predating the process start loaded normally — which rules out the
alternative explanation that the unrecognized `tier:` frontmatter key was getting the
definitions rejected. The README says so in the install steps rather than in a
troubleshooting note, because that is where the reader is — and the installer itself ends
a run that wrote with the same instruction, because a reader who follows the entry point
never opens the README at all (`basicly-e3z6`). A dry run and an already-installed
converge run do not say it: nothing changed for a restart to pick up.

## 4. The live proof

The mechanism was proven on `a3yi`; what `wbsz.3` owed was that _this shipped hook_ does
it end to end, once installed. Both halves are now closed.

**Half 1 — the installed command resolves and emits.** Driving it with real `PreToolUse`
payloads gave seven outcomes, each discriminating, in both directions: `tier: low` →
`model=haiku`; and silence for a definition with no tier, an unknown name, a caller-pinned
model, a non-`Agent` call, `CLAUDE_CODE_SUBAGENT_MODEL` set, and a working directory
outside any map. All exit 0, stderr empty.

**Half 2 — the host honours it.** With the hook installed and the process relaunched:

| Probe | Declares | Ran on |
| --- | --- | --- |
| `tier-probe` | `tier: low` | `claude-haiku-4-5-20251001` |
| `probe-control` | _no tier key_ | `claude-opus-5` (the host default) |

The host default was opus, so the tier-declaring probe came back **two tiers below it**
and the byte-identical probe with only the tier key deleted came back untouched. The
control is the point: a one-sided proof passes by pinning everything. Both models were
read off the subagent transcripts, never off the agents' own claims about which model they
were.

## 5. Scope is what makes the installer's output committable

`basicly-dukb`, a P1 filed against `wbsz.3` the day it shipped. At project scope the
installer wrote a machine-specific absolute command — an interpreter path and a repository
path — into `.claude/settings.json`, which is **tracked**. That leaks a username into a
commit and is broken for every teammate.

The interesting part is why the tests could not see it: every one installed into a bare
`tmp_path` while running the installer from basicly's own checkout, so the hook was never
inside the repository being written to. The fix moved the suite to a fixture that is a
repository _containing_ the kit.

The second interesting part is that the test which pinned the defect carried an unverified
rationale in its docstring — "a relative path breaks the moment a spawn happens in a
subdirectory" — and the counter-argument filed on the bead, that basicly's own hooks ship
relative commands and are fine, was **also** unverified and was the one that turned out
false. The host documents that handlers run in the _current_ directory, not the project
root, and this repo has watched its own relative-path hook fail exactly that way. Both
options were wrong; the answer was a third the vendor documents, `${CLAUDE_PROJECT_DIR}`,
which the host substitutes itself before any shell sees it.

The resulting rule, which the two scopes now encode:

- **committed and shared** (`<repo>/.claude/settings.json`) → nothing machine-specific:
  the placeholder plus `uv run --no-project --no-python-downloads python`. `uv` because
  every committer to a basicly-managed repo already needs it for the projected git hooks,
  and because Windows ships no `python3.exe` from the python.org installer — the name
  instead hits an App Execution Alias that opens the Microsoft Store, a worse failure than
  a clean one. `--no-python-downloads` keeps the spawn path network free.
- **machine-local** (`~/.claude/settings.json`) → absolute paths, nothing needed on
  `PATH`.
- **neither possible** → refuse. Falling back to the absolute rendering would reinstate
  the bug.

## 6. What this deliberately is not

- **Not a security boundary.** It is a convenience in the spawn path, and a bug in it must
  never stop an agent from spawning: any unexpected error exits 0 with no output. It sets
  a model and nothing else — it is not a way to smuggle authority into a spawn.
- **Not a source of authority over the map.** It reads committed data. It never calls the
  network and never calls an LLM.
- **Not willing to guess.** An unresolvable tier leaves the call untouched so the host's
  own default applies. It never substitutes a neighbouring tier's model — the silent
  demotion `basicly-izda` exists to prevent.
- **Not machine-wide by default.** `--user` installs for every repository on the machine
  and is a deliberate opt-in. It is safe only because the hook answers solely for a
  directory tree with its own committed map, which is why the resolver's kit-adjacent
  fallback is switched **off** in the hook: the kit is always beside itself, so leaving it
  on would inject a model into every unrelated repository on the machine.

## 7. Open

What is still open about this kit is tracker state, not prose, so query it rather than
reading a list here:

```sh
br list --label kit
```

The list this section used to carry is gone deliberately. It duplicated what the tracker
already owns, so it went stale on every close — and because two lanes closing two kit
items both edited this one anchor, it collided in a file neither bead declared. That is
the failure this section caused (`basicly-3f76`).
