- **A source module with no test file named after it now fails a gate, by name.** `§9.4` of
  `docs/design/factory-loop-requirements.md` states the convention — `test_<module>.py`, or
  `test_<module>_<aspect>.py` when one module's tests justify a split — and records that it was
  *emergent* when it was measured: 48 modules, 84 test files, every module covered. Nothing made
  it binding, so the first module splits broke it. Measured on this tree before the fix: 73 source
  units and **11 with no test file named after them**, ten of them created on 2026-08-08. Their
  tests were never missing; they stayed in the file named after the module they were extracted
  from, which is exactly the drift the convention existed to stop. `.scripts/check_test_naming.py`
  is now a `[[verify.checks]]` entry (`test-naming`, in `fast`, `full` and `staged`) and the
  eleven are placed: `artifact_record`, `capability_proof`, `catalog_source`, `dispatch_phase`,
  `mirror`, `owned_store`, `repair_brief`, `skill_source`, `spend_calibration`, `surface_report`
  and `ui` each have their own file (basicly-u2hl.14).
- **The gate runs forward only, and says so.** A source unit must have a test file; a test file
  need not have a source unit — `tests/` legitimately covers `.scripts/`, the git hooks, the
  shipped kit and whole-loop integration paths, none of which are modules, and failing on those
  would make the gate unrunnable rather than stricter. The unit is what the package exposes: a
  top-level module is one unit and a subpackage is one unit, so `renderers/claude.py` is covered
  by `tests/test_renderers.py`. A derived name that is already another unit's own test file does
  not count, which closes the hole where splitting a module and deleting its test file would read
  as covered under the very form the split created.
- **`[sizing] working_set_max` raised from 132,000 to 200,000.** The ceiling is derived from the
  dispatch record, not chosen, and `basicly-u2hl.14` itself completed at a re-derived estimate of
  197,646 — a 27-path scope costing 65,882 to read at the feature seed. A second instance of the
  `basicly-tcmy.5` shape by a new route: this lane did not widen its scope, it *wrote into* the
  scope it was admitted on, being a gate whose deliverable is new test files under paths its own
  scope already named. `src/basicly/config.py` carries the derivation and what the number is not
  (basicly-u2hl.14).
