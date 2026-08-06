- **A `kit-boundary` gate that can actually see the kit tree.** The portable kit under
  `.basicly/core/kit` has one structural rule — the engine imports the kit; the kit imports
  nothing, and never reads basicly's config loader, logging, session state or policy module.
  `docs/design/work-tracker.md` §4 named `lint-imports` as the enforcement for it. That was
  unenforceable rather than merely unimplemented: import-linter analyses the `basicly`
  package, and the kit is flat modules with no `__init__.py`, outside it and not on
  `sys.path`, so the tool never opens a kit file. Measured, not argued —
  `test_import_linter_cannot_see_a_kit_violation` seeds `import basicly.config` into a kit
  and records `lint-imports` reporting `2 kept, 0 broken` while the new gate fails on the
  same line.

  `.basicly/core/hooks/kit-boundary.py` parses every kit module and reports four routes back
  into the engine: a static import, a dynamic one (`importlib.import_module`, `find_spec`,
  `__import__`), a path into the engine's source tree, and a read of `basicly.toml` or of a
  `.basicly/` directory outside the kit's own `.basicly/core`. Path expressions are folded
  first, so `Path(".basicly") / "usage"` is seen. It is wired twice on purpose: as a
  `[[verify.checks]]` entry in `--mode full`, which is what CI runs, and as a `pre-commit`
  hook — the wiring that ships with the kit, so a consumer repo gates the boundary at commit
  time without declaring anything (`basicly-vkh0.16`).
