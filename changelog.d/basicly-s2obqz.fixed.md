- The base-checkout lock survives two Windows races: a release retried past a waiter's
  concurrent read (WinError 32), and a create refused by a peer's in-flight delete now
  reads as busy instead of crashing the dispatch.
