"""Tests for the catalog content-verification checks."""

from __future__ import annotations

from basicly.catalog_verify import verify_catalog
from basicly.schema import Fragment


def _frag(frag_id: str, body: str, **kwargs: object) -> Fragment:
    """Build a Fragment with sane defaults for content-check tests."""
    return Fragment(
        id=frag_id,
        description=f"{frag_id} description",
        category=kwargs.pop("category", "project"),  # type: ignore[arg-type]
        applies_to=kwargs.pop("applies_to", ["all"]),  # type: ignore[arg-type]
        body=body,
        **kwargs,  # type: ignore[arg-type]
    )


def test_clean_set_passes() -> None:
    """A set of distinct, unambiguous fragments reports no violations."""
    fragments = [
        _frag("style", "Format code with the configured formatter."),
        _frag("tests", "Add a regression test for every bug fix."),
    ]
    assert verify_catalog(fragments) == []


def test_identical_bodies_flagged() -> None:
    """Two fragments with identical bodies are reported."""
    fragments = [
        _frag("a", "Keep diffs minimal and focused."),
        _frag("b", "Keep diffs minimal and focused."),
    ]
    violations = verify_catalog(fragments)
    assert any("'a' and 'b' have identical bodies" in v for v in violations)


def test_near_duplicate_bodies_flagged() -> None:
    """Two fragments whose bodies are near-identical are reported."""
    base = "Always validate external input at the trust boundary before use in logic."
    fragments = [
        _frag("a", base),
        _frag("b", base + "!"),
    ]
    violations = verify_catalog(fragments)
    assert any("near-duplicate bodies" in v for v in violations)


def test_contradiction_flagged() -> None:
    """Opposing single-sided preferences across fragments are reported."""
    fragments = [
        _frag("a", "Indent using tabs."),
        _frag("b", "Indent using spaces."),
    ]
    violations = verify_catalog(fragments)
    assert any("possible contradiction" in v and "tabs" in v for v in violations)


def test_contradiction_not_flagged_when_one_fragment_states_a_preference() -> None:
    """A single fragment naming both sides is a resolved preference, not a contradiction."""
    fragments = [
        _frag("style", "Prefer pathlib over os.path for filesystem paths."),
    ]
    assert not any("contradiction" in v for v in verify_catalog(fragments))


def test_ambiguous_phrase_flagged() -> None:
    """A vague filler phrase in a body is reported."""
    fragments = [_frag("a", "Handle errors as appropriate for the situation.")]
    violations = verify_catalog(fragments)
    assert any("vague phrase 'as appropriate'" in v for v in violations)


def test_scope_overlap_flagged() -> None:
    """Two scoped fragments with the same targets and paths are reported."""
    fragments = [
        _frag("py-a", "Use type hints.", scope_paths=["**/*.py"]),
        _frag("py-b", "Prefer comprehensions.", scope_paths=["**/*.py"]),
    ]
    violations = verify_catalog(fragments)
    assert any("share the same scope" in v for v in violations)


def test_scope_overlap_ignores_default_scope() -> None:
    """Default-scope (**) fragments are the norm and never count as an overlap."""
    fragments = [
        _frag("a", "Prioritize correctness over speed."),
        _frag("b", "Keep code clean and free of dead code."),
    ]
    assert not any("share the same scope" in v for v in verify_catalog(fragments))
