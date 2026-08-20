- **The board snapshot schema no longer warns readers off an edit that is safe.** Its
  first line claimed `wired-or-deleted` indexes the file's prose as field references;
  `basicly-r343` had already narrowed that scan to object keys plus the string values
  under `required`, `enum`, `const` and `$ref`, so a `description` is never read. The
  line now states what the gate does read, which makes the real hazard — a new key or
  permitted value repeating a declared name — the one a reader is warned about
  (`basicly-desr1v`).
