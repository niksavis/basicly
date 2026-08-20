- **A `change-summary` artifact records the commit the landing actually took.** The head
  was read before the merge, which rebases the branch and can add a regeneration commit on
  top of it, so the recorded `commit` named a sha that no longer existed: `basicly-gvlpxm`'s
  own summary carried `d3422f81` while its branch stood two commits later at `634c125a`,
  and the changed-path count and digest beside it described that stale tree. The head now
  comes back from the landing itself, so it resolves in the base branch and the paths a
  reader derives from it are the ones that landed. The changed-path set is still read
  before the merge, where it is still the build's own (`basicly-gvlpxm`).
