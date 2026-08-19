- **`interface-facts` now names the machine-readable documentation route for every dependency
  this repo declares, so rung 3 stops costing an open-ended search.** Twelve rows, each fetched
  rather than recalled on 2026-08-19: `llms.txt` plus per-page markdown for uv, ruff, Claude
  Code, the Anthropic API, Codex and GitHub Docs; Sphinx `_sources/*.rst.txt` and `objects.inv`
  for Python, pytest and the library dependencies; and git and pre-commit stated as rung 2,
  answered by the installed binary. Absence is controlled for — every host that 404'd on
  `llms.txt` was re-probed on a page it must serve.

  The table sits **below** the binary-first rule, with the incident that forces the ordering:
  a `uv` installed at 0.11.28 against 0.12.5 released, where the current documentation described
  a `uv init` default the installed binary did not have and `--help` could not reveal. It also
  states what to refuse — never cache or commit fetched documentation, never cite an aggregator,
  never record a fact without its version and date — and tells the reader to probe a row rather
  than trust it, because three of the twelve routes redirected on the day they were written.
  GitHub Docs' Search API is documented with the **undocumented `client_name` parameter** it
  requires: the example GitHub prints in its own `llms.txt` answers 400 without it. The probe
  behind the table, including the two claims it refuted, is
  `docs/research/2026-08-19-documentation-routes.md` (`basicly-e2mz.48.1`).
