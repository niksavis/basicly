- **The harness board design no longer states a snapshot build time its own table refutes.**
  Constraint C5 claimed 19.1 ms for a whole build and a 26x margin against the 500 ms
  acceptance cap, while the per-source table directly above it listed 16.5 ms for the fold
  alone - the figure had excluded the log read, which is the largest single cost in the
  producer. Re-measured on the tree that ships `units` and `graph`: **103.8 ms**, median of
  21, decomposed step by step so the whole can be checked against its parts. The reduction
  against `observe()` is 59x rather than 320x, and the real headroom against the cap is
  4.8x, which is recorded as a band rather than a loose bound (`basicly-ef953m`).
