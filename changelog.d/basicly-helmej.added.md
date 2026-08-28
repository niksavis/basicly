- **A spend forecast that missed by an order of magnitude can be banked as history.** The
  live test over the whole ledger turned `main` red for every landing whenever one lane's
  recorded forecast missed, three times. `spend-accuracy` is now a verify check with a
  `[tool.spend_accuracy.frozen]` table that tracks, like release-notes (basicly-helmej).
