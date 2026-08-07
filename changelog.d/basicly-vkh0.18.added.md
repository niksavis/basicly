- **A shadow differential that refuses to compare the owned tracker against a copy of
  itself.** The owned event log answers the three queries the loop advances on — phase
  derivation, the ready set, gate status — and those answers are compared record by record
  against the tracker still authoritative for them.

  The load-bearing half is the **refusal**. The comparison must run against the live
  tracker and never against a re-import of its own export, because two derivatives of one
  lossy snapshot agree with each other and prove nothing — and the failure mode is a
  *clean report*, so it has to be something the harness declines to run without. The
  reference side is therefore audited rather than trusted, on three routes: the sha256 of
  the bytes it read against the digest the import recorded; any export at all, on the
  measured ground that a `br gate report` row is visible to `br gate list` and **absent
  from the JSONL export entirely**; and a perturbation probe for a source that declares no
  snapshot, which a genuinely live source ignores and so cannot false-fire.

  `clean` and `conclusive` are separate properties, and a caller cannot get the second by
  asking for the first. A comparison over a population where every record gives the same
  answer has discriminated nothing, which is not hypothetical: before the dual write, every
  bead reported zero gate rows, so that query was constant and a report saying only *clean*
  would have been reporting the absence of evidence as agreement.

  Both sides supply the same view type and the verdicts are derived **once** for both, so a
  disagreement is about a fact rather than about two copies of a rule (`basicly-vkh0.18`).
