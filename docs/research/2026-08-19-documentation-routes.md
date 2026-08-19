# Research — Machine-readable documentation routes for this repo's dependencies

Probed 2026-08-19 from this worktree with `curl` (`-sL`, 20-30s timeouts, a
`basicly-route-probe` user agent). This document is **findings, not a plan**: it records
what each route answered, how absence was controlled for, and where the routes disagreed
with the premise the work record carried. The routes themselves are stated for readers in
the `interface-facts` skill; this is the provenance behind that table.

Nothing fetched here is cached or committed. Only status codes, byte counts and the few
strings quoted below were kept — the upstream licences are not ours to redistribute.

## 1. What answered

| dependency | probe | result |
| --- | --- | --- |
| uv | `docs.astral.sh/uv/llms.txt` | 200, 5145 B; `uv/reference/cli/index.md` 200, 789 kB. `uv/llms-full.txt` 404 |
| ruff | `docs.astral.sh/ruff/llms.txt` | 200, 1773 B; `ruff/configuration/index.md` 200, 29.6 kB |
| Claude Code | `code.claude.com/docs/llms.txt` | 200, 41.9 kB; `docs/en/cli-reference.md` 200, 107 kB |
| Anthropic API | `platform.claude.com/llms.txt` | 200, 58.8 kB; per-page paths it lists are `platform.claude.com/docs/en/<path>.md`; `llms-full.txt` 200 at **32,169,931 B** |
| Codex | `developers.openai.com/codex/llms.txt` | **308** → `learn.chatgpt.com/docs/llms.txt`, 200, 23.9 kB, titled `# Codex`, markdown twins at `/docs/<slug>.md` |
| GitHub Docs | `docs.github.com/llms.txt` | 200, 28.7 kB; `en/rest/repos/repos.md` 200, `content-type: text/markdown` |
| Python | `docs.python.org/3/llms.txt` | 404; `_sources/library/pathlib.rst.txt` 200, `objects.inv` 200 |
| pytest | `docs.pytest.org/en/stable/llms.txt` | 404; `_sources/how-to/fixtures.rst.txt` 200, `objects.inv` 200 |
| git | `git-scm.com/llms.txt` | 404 — rung 2 only |
| pre-commit | `pre-commit.com/llms.txt` | 404 — rung 2 only |
| jinja2 | `jinja.palletsprojects.com/en/stable/llms.txt` | 302 with an empty body; `objects.inv` 200, `_sources/api.rst.txt` 200 |
| rich | `rich.readthedocs.io/en/stable/llms.txt` | 404; `objects.inv` 200 |

**Absence controls.** A 404 on `llms.txt` is ambiguous between "not served" and "wrong
probe", so each host that 404'd was re-probed on a page it must serve:
`docs.python.org/3/` 200, `docs.pytest.org/en/stable/` 200, `git-scm.com/docs` 200,
`pre-commit.com/` 200. The hosts are reachable; the file is genuinely not there.

## 2. GitHub Docs: `client_name` is required, and undocumented

`docs.github.com/llms.txt` names the Versions, Languages, Page List, Article, Article Body
and Search APIs "the preferred way for LLMs and automated tools to access GitHub
documentation", and prints example invocations. The Search example it prints,

    curl "https://docs.github.com/api/search/v1?query=actions&language=en&version=free-pro-team@latest"

answers **HTTP 400** verbatim, with

    {"error":"Missing required parameter 'client_name' for external requests"}

Appending `&client_name=<a name identifying the caller>` answers 200 with a `hits` array.
`/api/article` and `/api/article/body` both answered **200 without** the parameter, so the
requirement is Search-specific today; send it on every endpoint rather than depending on
that asymmetry.

## 3. Two corrections to the premise on record

The work record (`basicly-e2mz.48.1`, filed 2026-08-19) carried two claims that this
probe refutes. Both are recorded here because the skill's table states the corrected form.

1. **"Article and Search APIs ... without `client_name` the response is 400."** Only Search
   400s. The Article and Article Body endpoints answered 200 without it (§2).
2. **Codex at a nested `/codex/llms.txt`.** That path 308-redirects to
   `learn.chatgpt.com/docs/llms.txt`, while `developers.openai.com/llms.txt` still
   advertises the old URL in its documentation-set list. A probe that does not follow
   redirects, or a reader who trusts the index, lands wrong.

`docs.claude.com/llms.txt` 301s to `platform.claude.com/llms.txt`, as the record said.
Three of twelve rows therefore involve a redirect on the day they were written, which is
the argument for probing a row rather than trusting it.

## 4. Why the table stays below rung 2

uv's changelog gives 0.12.0 "Define build systems by default with `uv init`" (fetched
2026-08-19 from `raw.githubusercontent.com/astral-sh/uv/main/CHANGELOG.md`). On
2026-08-19, before this machine's toolchain was updated, the installed `uv` was 0.11.28
against 0.12.5 released, so the current documentation described a `uv init` the installed
binary did not implement — and `uv init --help` on 0.11.28 listed both `--package` and
`--no-package`, so a flag probe could not see the difference either. The toolchain has
since been updated: `uv --version` now reports 0.12.5 and `git --version` 2.55.0.

A cheap fetch route makes rung 3 the path of least resistance. That is exactly the failure
above, so the skill states the binary-first rule above the table rather than beside it.
