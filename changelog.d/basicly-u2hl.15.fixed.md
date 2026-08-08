- **The type checker now analyses the scripts and hooks it had been silently skipping.**
  pyright's default `exclude` is `["**/node_modules", "**/__pycache__", "**/.*"]`, and that last
  pattern dropped `.scripts/` and `.basicly/core/` — including the git hooks that ship to consumers
  via `basicly install` and the kit modules that run in the dispatch path, which is the code with
  the widest blast radius — from every mode this repo runs. Nothing failed, because a checker
  cannot report on a file it never opened. `[tool.pyright]` now spells out both `include` and
  `exclude`: an `include` alone would not have been a fix, since `exclude` is applied on top of it
  and wins, and it filters files named explicitly on the command line too (`pyright
  .scripts/check_module_size.py` analysed 0 files). Coverage goes from 204 files to 242 — the 38
  tracked modules under those two trees — and the four errors that were hiding there are fixed: an
  `ast.AST` walked without narrowing to `ast.expr` before reading `.lineno` (`kit-boundary.py`), a
  `__doc__.splitlines()` on the `str | None` module
  docstring in two argparse setups, and a `modalities.get()` narrowed on a second call rather than
  on the value. `tests/test_type_checking.py` sweeps the whole tracked tree against both lists, so
  the next directory of first-party Python fails there instead of inheriting the silence, and runs
  pyright over a bad module under `.scripts/` to prove the coverage is real — with the config minus
  its `exclude` override as the discriminator, which analyses nothing and exits 0 (basicly-u2hl.15).
