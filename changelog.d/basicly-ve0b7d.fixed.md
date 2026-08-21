- **A `create` carrying a bare word where a flag belongs is refused, naming the word and the
  flags that would have carried it, instead of dropping it.** `basicly tracker write -- create
  "<title>" bug 1 --description "..."` minted a record with `issue_type` and `priority` both
  unset and printed `created:` — `--type` and `--priority` are flags, so `bug` and `1` landed as
  extra positionals and `write_verbs._create_drafts` ignored them. It caught the operator twice
  in one session, and the second time the untyped record was what the sizing path then
  misreported.

  **The silence is the defect, not the parsing.** A caller who wrote `bug 1` believed they had
  typed the record; nothing said otherwise, and the next reader sees an untyped record with no
  trace that a type was offered. `_create_drafts` twelve lines above already refused a create
  naming no title, on the argument that a titleless record is a `created` event stating nothing
  (basicly-1qi0sz) — an argument that covers an argument the seam cannot place just as well, and
  was not being reused.

  **Arity is not the discriminator, which is why the refusal is scoped to one verb.** br closes
  `[IDS]...` and `update` takes the same, so a refusal keyed on "more than one positional" would
  refuse every plural close in the log; the two `dep` verbs and `gate report` each check an exact
  arity of their own already. `create` was the one verb with a fixed shape — `create <title>` —
  and no check on it. For `close` and `update` the further words are record ids, which
  `owned_write.refuse_a_write_to_an_absent_record` already speaks for, so nothing is silently
  dropped there.

  The flags the message names are read off `tracker_argv.CREATE_FIELD_FLAGS` rather than
  respelled, long spellings only, so a flag added to that table joins the refusal's advice by
  existing — and a caller who wrote a bare word wrote no flag at all, so the short forms would
  only be noise. Both size ratchets were measured before the prose was written: the refusal is
  in `write_verbs.py`, which had 170 tokens of working-set headroom and 636 of prose, and the
  derived constant is in `tracker_argv.py`, which had 2668 of headroom and 41 of prose — the two
  axes pull opposite ways and the first draft tripped both (basicly-ve0b7d).
