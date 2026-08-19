- **`basicly tracker show <id>` answers what a record blocks, what blocks it, and what its
  children are.** Neither the engine's command nor the kit's own `show` rendered a single edge:
  both returned the folded record's seven keys with no `dependencies` and no `dependents`, while
  the engine's internal reader answered both directions off the same edge events in the same
  store. So no command-line surface answered the dependency question one record at a time —
  which is the question an agent orienting through the CLI asks first, and the reason a snapshot
  producer built over this surface would have emitted a graph with no edges.

  Both surfaces now carry both keys. Each edge names its type and the other record's status; a
  dependent also carries its title, because a caller listing children has no second read to
  reach for. **Both keys are always present and empty when the record has no edges**, so absence
  is distinguishable from a surface that never rendered them — the failure that prevents is a
  reader taking a missing key for "no blockers". A test holds the two producers to one shape over
  three records including a dangling edge, because they are two producers and not one: the kit
  cannot import the engine, so no single implementation is available from that side. The
  `work-tracker` skill, which listed six keys of a folded record, names both (basicly-ztik9a).
