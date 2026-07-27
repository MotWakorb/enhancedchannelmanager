"""Shared fold-match-key canonicalization (GH #645 / bead 0vao3).

One helper, two call sites — the auto-creation executor's opt-in
``fold_match_key`` merge lookup and the Find Duplicates endpoint's opt-in
``fold_match_key`` grouping — so the two surfaces cannot drift.

The fold produces a COMPARISON KEY only: casefold + strip ALL whitespace.
It is deliberately NOT a normalization rule — visible channel names are
never rewritten by it (docs/normalization.md parity contract). Both
consumers keep their exact/case-insensitive matching first and use the
folded key as an additional, opt-in equivalence.
"""


def fold_match_key(name: str) -> str:
    """Return the canonical comparison key for ``name``.

    Casefolds (stronger than ``lower()`` — handles e.g. German eszett) and
    removes ALL whitespace, interior and edge (``str.split()`` splits on any
    Unicode whitespace run): ``"Euro sport 2"`` -> ``"eurosport2"``.
    """
    return "".join(name.split()).casefold()
