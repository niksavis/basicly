<div align="center">

<img src="site/assets/logo.svg" alt="basicly logo" width="112">

# basicly

**basicly writes the instruction files for Claude Code, Codex and Copilot from one catalog of rules. It adds git hooks that check every commit.**

Use it when one or more coding agents work in your git repository. Every agent then follows the same rules and passes the same gates.

[![latest release](https://img.shields.io/github/v/release/niksavis/basicly?label=release)](https://github.com/niksavis/basicly/releases/latest)
[![quality gates](https://github.com/niksavis/basicly/actions/workflows/quality-gates.yml/badge.svg)](https://github.com/niksavis/basicly/actions/workflows/quality-gates.yml)
[![python 3.14+](https://img.shields.io/badge/python-3.14%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

</div>

## Install

Run this inside your git repository. On Linux, macOS or the Windows Subsystem for Linux
(WSL):

```sh
curl -fsSL https://niksavis.github.io/basicly/install.sh | sh
```

On Windows (PowerShell):

```powershell
irm https://niksavis.github.io/basicly/install.ps1 | iex
```

You need git and [uv](https://docs.astral.sh/uv/). The script installs uv when it is
missing, and uv gets Python 3.14 or later. You also need Node.js and the
`markdownlint-cli2` package in the repository, for the markdown gate. Without them,
install stops before it writes a file.
[Markdown gate prerequisites](docs/how-to/upgrade-and-check-drift.md#markdown-gate-prerequisites)
shows how to add the package, choose the files it checks, or turn the gate off.

The script installs the latest release. To read the script before you run it, or to pick
a version, see [install options](docs/how-to/upgrade-and-check-drift.md#install-options).

### What install changes

Install writes the instruction files (`.claude/CLAUDE.md`, `AGENTS.md` and
`.github/copilot-instructions.md`), the skills, the subagents and the `.basicly/` folder.
It writes the git and agent hooks, `basicly.toml`, `.basicly-local/`, a continuous
integration (CI) workflow and
`.vscode/tasks.json`. It also adds lines to `.gitignore` and `.gitattributes`.

Install does not commit. Run `git status` to review the files. Every person who commits
needs the same tools, as
[keep every committer working](docs/how-to/upgrade-and-check-drift.md#keep-every-committer-working)
explains.

## What you get

- **Rules.** The catalog is a set of YAML files that hold the rules for your agents.
  basicly writes them into the files each agent reads. A skill is a set of steps that
  an agent loads when a task needs it. A subagent is a helper agent with one job. Your
  own rules live in `.basicly-local/`, and an upgrade never changes them.
- **Gates.** A gate is a check that refuses a commit when it fails. basicly runs the
  gates in git hooks and in agent hooks. An agent hook is a command that a coding agent
  runs before or after it acts. CI runs the gates again.
- **A tracker.** The tracker stores records of work, their dependencies and the result
  of each gate. It keeps them in a log in `.basicly/ledger/`. basicly adds to the log and
  never changes old lines. You commit the log with your code.
- **A loop.** The `loop` command takes one record from creation to a merged change. It
  builds each record in its own git worktree, which is a second working copy of the
  repository. The loop reads the state of each record from the tracker, so another
  agent can continue the work.

## First use

1. Open your coding agent in the repository. It now reads the rules in its instruction
   file.
2. Follow the [tutorial](docs/tutorial/first-loop.md) to make the first commit. Each
   commit needs a [Conventional Commits](https://www.conventionalcommits.org) subject
   and the id of a tracker record that you claimed. To claim a record marks it as your
   work. The tutorial shows both on a test
   repository that you can delete. It sends no request to a paid AI model.

[Unblock a commit](docs/how-to/unblock-a-commit.md) explains each refusal and its fix.

## Run basicly commands

Install adds no `basicly` command to your `PATH`. Add this function to your shell
startup file, such as `~/.bashrc` or `~/.zshrc`. Put the version you installed in the URL;
the [releases page](https://github.com/niksavis/basicly/releases) lists each version:

```sh
basicly() { uvx --python '>=3.14' --from https://github.com/niksavis/basicly/releases/download/v0.44.1/basicly-0.44.1-py3-none-any.whl basicly "$@"; }
```

In PowerShell, add this function to your profile:

```powershell
function basicly { uvx --python '>=3.14' --from https://github.com/niksavis/basicly/releases/download/v0.44.1/basicly-0.44.1-py3-none-any.whl basicly @args }
```

After you edit a rule in `.basicly-local/`, write the instruction files again, then check
that they match the catalog:

```sh
basicly build
basicly check
```

## Upgrade and uninstall

To upgrade, run the install script again. It is safe to run more than once. basicly is
before version 1.0, so a minor release can change a command. The [changelog](CHANGELOG.md)
names each change.

To remove the files that basicly generated, run:

```sh
basicly uninstall
```

Uninstall keeps your rules in `.basicly-local/`, `basicly.toml` and your tracker records.
[Uninstall](docs/how-to/upgrade-and-check-drift.md#uninstall) explains `--purge`, which
also deletes your rules, and the files that you remove by hand.

## Install one kit alone

A kit is a part of basicly that installs without the rest. There are four kits. Each has
no dependencies and needs Python 3.9 or later.

| Kit | What it adds to a repository |
| --- | --- |
| [`basicly-tracker`](packages/basicly-tracker) | the tracker alone. Two people or agents can add records at the same time, and git still merges the log |
| [`basicly-board`](packages/basicly-board) | a web page and an HTTP API for the tracker, on your own computer. It needs the tracker kit |
| [`basicly-comments`](packages/basicly-comments) | a gate that refuses a prose comment in a code file, and a tool that removes such comments safely |
| [`basicly-tier`](packages/basicly-tier) | a model tier (`low`, `medium`, `high`, `maximum`) that a subagent names instead of a model. The kit maps each tier to a model |

For example, to add the tracker:

```sh
uv tool install 'git+https://github.com/niksavis/basicly@v0.44.1#subdirectory=packages/basicly-tracker'
basicly-tracker init
```

The [board README](packages/basicly-board/README.md) gives the board's commands. The kits
reference below gives the commands of the other three kits.

## Documentation

| Page | What it covers |
| --- | --- |
| [Tutorial](docs/tutorial/first-loop.md) | install to your first finished record, on a test repository |
| [Customize the rules](docs/how-to/customize-the-catalog.md) | your own rules, rules for some paths only, files you must not edit |
| [Set up the verify gate](docs/how-to/wire-up-the-verify-gate.md) | declare your checks. A gate with no checks always passes |
| [Unblock a commit](docs/how-to/unblock-a-commit.md) | the refusals you will meet, and the fix for each |
| [Upgrade, check for changed files, uninstall](docs/how-to/upgrade-and-check-drift.md) | install options, `--purge` |
| [Build several records in parallel](docs/how-to/run-parallel-lanes.md) | one git worktree per record |
| [Resume work or give it to another agent](docs/how-to/resume-a-track.md) | continue after a crash, or with a different agent |
| [Command reference](docs/reference/cli.md) | every `basicly` command and its flags |
| [Kits reference](docs/reference/kits.md) | the commands of the tracker, comments and tier kits, and what each kit writes |
| [Trial the tracker beside br](docs/how-to/trial-the-tracker-beside-br.md) | run the tracker beside br, an earlier tracker, before you retire br |
| [Architecture](docs/architecture/architecture.md) | how basicly works, and why |
| [Status](docs/architecture/status.md) | what is shipped, what is being built, and what is planned |
| [Codex permissions](docs/how-to/configure-codex-permissions.md) | remove the Codex permission policy that an earlier version wrote |

## Contributing

Open a GitHub issue to report a bug or suggest an idea.
[`CONTRIBUTING.md`](CONTRIBUTING.md) explains the development setup, the commit rules
and the checks a change must pass.

## License

basicly uses the [MIT License](LICENSE).
