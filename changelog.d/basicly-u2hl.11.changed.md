- **The ruff rule families the stack was leaving off are enabled, and security lint now reaches
  `src/`.** `TRY`, `PERF`, `FURB`, `A`, `RET`, `TC`, `TID`, `DTZ` and `BLE` are adopted; `S` is
  adopted over `src/` and per-file-ignored elsewhere so bandit keeps the trees it already scans.
  `TRY003` and `TC003` are deliberately ignored with the reason recorded in `.ruff.toml` — style at
  scale, not a defect class — and `S101` mirrors the existing bandit `B101` skip rather than
  inventing a second answer.

  **Consumers inherit a stricter gate.** The change is called out here rather than filed as a chore
  because a repo that installs basicly gets these families on its next upgrade. What made `S` over
  `src/` worth the churn is measurable: `src/` carried 21 `# nosec` comments that no scanner read —
  bandit was configured over `.scripts`, `.basicly/core/hooks` and `.basicly/core/kit` and never
  `src/` — including an `autoescape=False,  # nosec B701`. An inert suppression reads as "reviewed"
  and is not; 21 of the 25 findings landed on exactly those sites (`basicly-u2hl.11`,
  `basicly-u2hl.16`).
