# basicly-tier

**A subagent declares a portable tier; this makes the spawn run on the model that tier
resolves to.** It needs no `basicly`: standard library only, no third-party package, no
network.

```console
$ uvx --from git+https://github.com/niksavis/basicly#subdirectory=packages/basicly-tier basicly-tier --host claude --tier low
{"alias": "haiku", "model": "claude-haiku-4-5", "surface": "anthropic", "tier": "low", ...}
```

`init` copies the kit and its model map into `.basicly/kit/tier` so it runs from plain
`python3` afterwards. It fails closed: an unavailable cell carries no model, and the
resolver never substitutes a neighbouring tier's.

Full reference, including the five traps that have each cost real debugging time:
[`kit/README.md`](../../.basicly/core/kit/tier/README.md).
