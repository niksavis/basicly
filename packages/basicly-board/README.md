# basicly-board

**Serve a tracker ledger as a web page and an HTTP API on localhost.** A person reads,
writes and edits stories in the browser, and an agent refinement pass shapes each story
before it is ready. The API returns the same versioned JSON as the tracker commands, so a
team can build its own page, server or chart on it. Standard library only. It needs the
tracker kit.

```console
$ uvx --from git+https://github.com/niksavis/basicly#subdirectory=packages/basicly-tracker basicly-tracker init
$ uvx --from git+https://github.com/niksavis/basicly#subdirectory=packages/basicly-board basicly-board init
$ python3 .basicly/kit/board/server.py serve .basicly/ledger
board: http://127.0.0.1:8765/ serves .basicly/ledger; API at /api/v1
```

The endpoints, the refinement flow and how to build your own client:
[`kit/board/README.md`](../../.basicly/core/kit/board/README.md).
