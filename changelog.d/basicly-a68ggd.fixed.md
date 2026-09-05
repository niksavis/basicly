- **The board region headed `the loop` now shows the running pass, not the whole backlog.**
  It binned every active record by phase, so its total equalled `backlog.active` by
  construction - 291 against 291 live. It now bins `lanes[]`, says `no pass is running`
  when there is none, marks a lane that moved this beat, and keeps the backlog census
  under its own label (basicly-a68ggd).
