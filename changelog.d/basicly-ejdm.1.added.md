A dispatched lane's transcript now names the tools each turn called, so a lane's token
spend can be split into context acquisition and implementation. That split is what
`basicly-ejdm` reasons about and had no instrument for: the claim that a lane's
multi-million-token floor is "bought by the instruction" was unfalsifiable without it.

Claude only — codex emits no per-tool event this stack parses, and the report that
consumes this must say so rather than implying coverage. A turn that called nothing
records an empty list; a transcript line written before the field stays absent, so a
reader can tell "called no tools" from "predates the measurement" rather than
classifying the entire historical corpus as pure implementation.
