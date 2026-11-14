# basicly-tracker

The standalone tracker includes the CLI, human board, agent skills, plugin exporter and MCP tools.
It runs without the basicly harness or third-party runtime packages.
The package supports Python 3.9 or later, as declared in `pyproject.toml`.

Install the tool for the user by default.
Each repository chooses its tracker and project prefix explicitly.
A private backlog vault can hold work from several public or private code repositories.
Everyone who can read that vault can read all its projects.

## Install

Install the standalone package with uv.

```sh
uv tool install 'git+https://github.com/niksavis/basicly#subdirectory=packages/basicly-tracker'
basicly-tracker init --user
```

The user installation writes tracker skills under `~/.claude/skills/` and `~/.agents/skills/`.
It leaves repository files alone.
The installer refuses to overwrite a skill it does not own.

For an existing local tracker, keep using its current storage until migration passes.
Use the migration reference before switching an existing ledger.
Never attach existing history by writing vault configuration alone.

For a new repository and an empty vault, initialize and attach explicitly.
Run these commands from the repository root.
Replace the vault address and prefix with your choices.

```sh
basicly-tracker vault-init --vault VAULT-ADDRESS --prefix PROJECT-PREFIX
basicly-tracker vault-init --vault VAULT-ADDRESS --prefix PROJECT-PREFIX --yes
```

The first command previews publication.
The confirmed command publishes genesis and attaches only after its safeguards pass.
Existing history requires the migration procedure instead.
A vault attachment uses `.basicly-tracker.toml` and a frozen `.basicly/ledger` guard.
Keep that guard and `.basicly/ledger-archive/` intact.

A repository can still use a local ledger.
This command installs its Git claim gate and uses the user tool.

```sh
basicly-tracker init
```

## Register agent tools

MCP exposes discoverable tools with typed input fields.
The adapter calls the existing tracker CLI and preserves its admission and refusal rules.
It uses the [MCP stdio transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).
Each server process binds to one repository root.
Tool arguments cannot select another repository, vault, prefix or identity.

Print registration for the client you use.
The default scope is the user.

```sh
basicly-tracker mcp-config --client codex --repository "$PWD"
basicly-tracker mcp-config --client claude --repository "$PWD"
```

Merge the returned `configuration` into its named `destination`.
Preserve unrelated settings and existing server entries.
The command prints configuration and writes nothing.
Use `--name` to distinguish registrations for different repositories.
Keep user configuration private because it contains the repository's local path.

Codex requires `env_vars` to forward the tracker session into MCP.
The generated configuration includes that field.
The [Codex MCP guide](https://developers.openai.com/codex/mcp) describes the configuration.
The [Claude MCP guide](https://code.claude.com/docs/en/mcp) describes its client configuration.

Set `BASICLY_HOLDER` to the holder name you choose.
The foreground launcher creates a unique tracker session for each new client process.
The launcher and generated MCP configuration identify the client as a coding agent.
The agent, its MCP children and its Git hooks must receive that same identity.

```sh
basicly-tracker agent -- codex --no-daemon
basicly-tracker agent -- claude
```

Use Codex's `--no-daemon` option to isolate the new client's environment from an existing shared server.
The launcher does not change an already-running desktop client or shared agent server.
Those clients need their supported environment configuration for each separate agent session.
Do not persist one shared `BASICLY_TRACKER_SESSION` value in client registration.
A missing session causes the existing vault claim refusal.

For an intentional resume, pass the previously recorded tracker session explicitly.
Choose the corresponding client conversation when it resumes.
Provider conversation ids and tracker session ids remain separate.

```sh
basicly-tracker agent --session EXISTING-SESSION -- codex --no-daemon resume
```

An agent discovers tools, finds work, creates or refines a record, reviews it and claims it.
Only a settled successful claim authorizes work.
Then the agent sets `status` to `in_progress` through `tracker_update`.
Use `add_labels` and `remove_labels` to change labels without replacing unrelated labels.
Only an agent can remove `refine`, after the record passes its readiness check.
It confirms observed evidence before closing the record.
Every tool result preserves the command's exit code and tracker report.
Inspect `isError`, `exit_code` and pending status before continuing.

The CLI remains available when an agent has no MCP client.

```sh
basicly-tracker ready .basicly/ledger
basicly-tracker show .basicly/ledger RECORD-ID --view current
basicly-tracker claim RECORD-ID
basicly-tracker update .basicly/ledger RECORD-ID --status in_progress
basicly-tracker comment RECORD-ID --text "What changed"
basicly-tracker sync
```

These claim and comment forms use vault configuration from the repository root.
The [command reference](../../.basicly/core/kit/tracker/REFERENCE.md) also covers local ledger forms.

## Install per repository

The optional sandbox mode bundles the same tracker and agent adapter in one Python archive.
It writes repository skills and needs no user installation.

```sh
uvx --from git+https://github.com/niksavis/basicly#subdirectory=packages/basicly-tracker basicly-tracker init --sandbox --with-instructions
python3 .basicly/tracker.pyz mcp --repository "$PWD"
```

`--with-instructions` updates existing agent instruction files with a marked tracker block.
When no instruction file exists, it prints the block for the file your agent reads.
It preserves unrelated instructions.
Print project registration with `--scope project`.
Add `--sandbox` when registering the repository bundle.
Use `--python` when the client needs a different interpreter command.
Project configuration uses relative paths.
Run its client from the repository root.
The tracker never silently replaces existing client configuration.

## Use with the basicly harness

The basicly harness ships the same kit, adapter and its `work-tracker` skill.
Use its installed tracker rather than creating another tracker or vendored copy.
Launch the installed adapter from the repository root.

```sh
python3 .basicly/core/kit/tracker/mcp_server.py --repository "$PWD"
```

Print client registration directly from the installed kit when no standalone package is installed.

```sh
python3 .basicly/core/kit/tracker/mcp_config.py --client codex --repository "$PWD"
```

The existing `basicly tracker` commands remain available.
The harness provides the same foreground launcher.

```sh
basicly tracker agent -- codex --no-daemon
```

Existing headless dispatch preserves its inherited owner context.
Provider conversation ids do not automatically become tracker session ids.
Installing the standalone tracker does not install the harness.

## Human board and portable plugin

The package includes the human board and HTTP API.

```sh
basicly-tracker serve .basicly/ledger
basicly-tracker plugin --out ./tracker-plugin
```

The plugin exports agent guidance, the command reference and a bundled `scripts/tracker.pyz`.
That archive includes the same MCP adapter.
Export refuses an existing destination without changing its contents.
The bundle requires Python but no installed tracker or basicly engine.

The [command reference](../../.basicly/core/kit/tracker/REFERENCE.md) covers imports, process evidence and migration.
The [format specification](../../.basicly/core/kit/tracker/SPEC.md) describes storage and ownership rules.
