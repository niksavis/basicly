- **The Python guidance no longer tells an agent to write a waiver the gate refuses.**
  `python-guidelines` is path-scoped on `**/*.py`, so every agent touching Python loads it,
  and its waiver recipe was three weeks stale in three ways at once. It omitted the kind, so
  its literal example parsed as unclassified and `waivers.py` rejected it - *states no kind,
  so nothing says whether this is permanent or owed back*. It named `waiver_count` under
  `[tool.module_size]` in `pyproject.toml`, the shared anchor that bounced three of five
  lanes on one day and that a per-record `count_delta` in `basicly.d` replaced. And it never
  mentioned rebaselining, which is the ordinary permitted route - already used 50 times
  across 26 entries, requiring a reason and a base commit, counted and printed on the pass
  line rather than silent.

  The cost was measured, not supposed. Three of four agents in one parallel pass
  independently reported the two size ratchets as a systemic blocker and spent budget on
  them rather than on their task. One earlier unit was a total loss to it: it enumerated all
  8.4 million subsets of a file's 26 natural blocks against the gates' own measurement
  functions, found 225,756 that satisfy both ratchets and not one that is a nameable
  responsibility, and reverted - on a margin of 28 prose tokens.

  The guidance now gives the economics before the remedy. **Extracting is not free and two
  in three natural cuts make it worse**: removing a unit raises the parent's prose share
  whenever the unit is prose-lighter than the parent, so a cut that satisfies `module-size`
  breaks `comment-density`. Measured over 3,588 real top-level definitions in the 68 frozen
  oversized modules, only 34.4% are prose-heavier than their parent and so satisfy both at
  once. Then rebaselining, with its two required inputs. Then the waiver, in both accepted
  spellings - `cohesion:` for permanent and `cost(<record-id>):` for debt that expires when
  that record closes. And the trap that makes the obvious move wrong: **a waiver on a frozen
  module replaces its frozen entry outright**, so waiving a module far above the cap deletes
  its ceiling to buy a few hundred tokens.

  Checked by feeding every waiver example in every surface to the gate's own parser rather
  than by reading them - three examples, zero unclassified, on the catalog source and both
  projections. Sweeping the whole file instead of only the edited passage is what found a
  third stale kindless example that the original finding had not named.
