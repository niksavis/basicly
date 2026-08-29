- **The catalog lint tests no longer depend on the version in the tree.** Nine tests built a
  catalog with no `token_cost:` and expected a clean lint, which held only while the 0.11.0
  window was open; every clean-lint fixture now declares a cost and the window test reads its
  channel from the rule (basicly-ve1h0l).
