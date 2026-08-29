- **The release page is the summary, and a cut without one is refused.** The workflow
  publishes the prose above `[Unreleased]`'s first `###`, the entry counts, a link to the
  section, every `BREAKING` entry and the install line (`.scripts/generate_release_notes.py`);
  `basicly release` refuses a changelog with no summary (basicly-xsdvp6).
