# basicly-comments

**The code is the source of truth, so a code file carries no prose.** This command reports
every prose comment in a code file and removes them when asked. It needs no `basicly`: the
kit it ships is standard library only, with no third-party package, no network and no
subprocess.

```console
$ uvx --from git+https://github.com/niksavis/basicly#subdirectory=packages/basicly-comments basicly-comments check src/
src/thing.py:12: # what this function does
comments: 1 prose comments in 40 files, 0 unreadable
```

`check` never writes and exits 1 when it finds prose. `fix` writes, and writes nothing it
could not prove. `init` copies the kit into `.basicly/kit/comments` so it runs from plain
`python3` afterwards, with no `uvx` and no network.

A comment a tool reads is not prose and is never touched: `noqa`, `nosec`,
`type: ignore`, a shebang, `SPDX-License-Identifier`, a `/*!` banner.

Covers Python, JavaScript and TypeScript, the C-like family including C#, CSS and Sass,
HTML, shell, PowerShell, SQL and Visual Basic. Config, data and Markdown are declined:
their comments are the only place a why can live.

Full reference, including the three proofs every strip must pass:
[`kit/README.md`](../../.basicly/core/kit/comments/README.md).
