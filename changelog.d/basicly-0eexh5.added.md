- **A completed dispatch now records its spend in the committed ledger**, as a typed
  `dispatch` event whose `spend_micros` the fold sums into the record's totals. Spend used
  to live only in the self-ignored `.basicly/usage/run-records.json`, so a clone read every
  grant as `spend unknown`. Measured tokens only, deduplicated by content digest
  (basicly-0eexh5).
