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
- **The spend-accuracy gate now measures a bead, not one attempt at a bead.**
  `forecast_spend_tokens` is derived from a bead's `## Scope`, so every dispatch of that bead
  records the identical number — what getting the whole bead done should cost — while
  `decompose.spend_accuracy` compared it against each dispatch separately. A bead dispatched more
  than once therefore had every attempt after the first scored against a forecast that covers work
  an earlier attempt already did, which is a structural under-spend rather than a forecast error:
  `basicly-u2hl.14` ran 30,139,416 then 2,785,270 then 1,512,403 tokens against a 26,320,290
  forecast, and the third attempt alone read as 0.057x and turned `main` red while the lane itself
  came in at 1.31x. The same unit error as basicly-tcmy.34, one level up — a number held against a
  quantity it does not denominate. A bead's comparable dispatches are now summed into one
  `SpendPair`, the forecast taken from the latest of them (a re-dispatch re-reads the bead, so four
  of the eight multiply-dispatched beads in this ledger carry forecasts differing by 2.5-9.7% across
  their attempts; each lands in band under either end, so no verdict turns on the choice), and
  `attempts` is carried on the pair and named in the violation — "spent
  17,000,000 tokens over 4 dispatches" — so an aggregate can never read as one runaway lane. The
  live gate goes from one violation to none across 60 lanes, and an overrun spread across four
  dispatches still fires (basicly-u2hl.15).
