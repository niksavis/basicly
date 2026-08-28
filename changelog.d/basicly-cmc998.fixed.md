- **A release commit no longer arrives at its own hook stale.** The version bump adds a
  character to every projected header, and the `always-on-sizes` block states those sizes, so
  the pre-commit fixer rewrote `architecture.md` mid-commit and the framework refused the
  release. `basicly release` now applies the fast fixers after it rebuilds (basicly-cmc998).
