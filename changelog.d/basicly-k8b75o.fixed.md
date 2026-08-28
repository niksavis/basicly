- **The release commit no longer refuses itself over the notes it publishes.** Assembly deleted
  the fragment filename that accounted for a record, so 43 of 149 records lost their note in the
  commit publishing it. The assembler now writes `(<record-id>)` onto a fragment whose body lacks
  it (basicly-k8b75o).
