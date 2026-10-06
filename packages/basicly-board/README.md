# basicly-board

**Serve a tracker ledger as a web page and an HTTP API on localhost.** A person reads,
writes and edits stories in the browser, and an agent refinement pass shapes each story
before it is ready. The API returns the same versioned JSON as the tracker commands, so a
team can build its own page, server or chart on it. Standard library only. It needs the
tracker kit.

The board installs the same two ways as the tracker. The default mode runs it from a user
install and writes no board code into the repository:

```console
$ uv tool install 'git+https://github.com/niksavis/basicly@v0.20.1#subdirectory=packages/basicly-board'
$ basicly-board init
$ basicly-board serve .basicly/ledger
board: http://127.0.0.1:8765/ serves .basicly/ledger; API at /api/v1
```

`init --sandbox` writes `.basicly/board.pyz` and a repository skill instead, and needs no
user install: `python3 .basicly/board.pyz serve .basicly/ledger`.

The endpoints, the refinement flow and how to build your own client:
[`kit/board/README.md`](../../.basicly/core/kit/board/README.md).

User and repository guidance is installed into both `.claude/skills/` and
`.agents/skills/`. Existing unmanaged skills are preserved.
