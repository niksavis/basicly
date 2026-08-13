A claude dispatch record now carries its cache split. `claude_json_usage` and
`claude_turn_usage` summed the four reported token counts into the total and discarded the
breakdown, so every claude run record read `cache_read_tokens: null` and no cache-hit ratio
could be derived from the ledger at all. Claude reports its counts disjoint from each other
where codex reports `input_tokens` inclusive of the cached portion, so the claude extractor
folds them to the same provider-neutral convention rather than storing the raw field — which
keeps `input_tokens - cache_read_tokens` a valid uncached figure whoever produced the numbers.
A usage block that omits a cache key records null rather than 0, because a turn that really
read no cache reports a genuine 0.
