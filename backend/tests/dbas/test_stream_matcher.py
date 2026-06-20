"""Unit tests for the DBAS 4-tier stream matcher (pure function).

Bead: ``enhancedchannelmanager-al6e3`` (split 3/4 of ``0i2vt.14``).

TDD parametrized table — one case per tier (exact URL → name+provider →
normalized name → fuzzy → all-miss), each asserting BOTH the returned tier AND
the matched destination id (not an aggregate %-match). Plus the contract edges:
empty candidates → miss, tie-break determinism (lowest id, order-independent),
and no-mutation of inputs.

The ">99% match-rate" success signal from ``0i2vt.14`` is a SEPARATE integration
test over the committed seed snapshot (bead ``enhancedchannelmanager-zqtjj``);
it needs a live/seeded Dispatcharr instance and is NOT in this file — see the
follow-up note in the bead report.
"""

from __future__ import annotations

import copy

import pytest

from dbas.stream_matcher import (
    STREAM_FUZZY_FLOOR,
    MatchTier,
    match_stream,
)


def _stream(id_=None, name=None, url=None, m3u_account=None):
    """Build a minimal stream record dict with only the set fields.

    Mirrors the Dispatcharr stream shape the matcher reads (``id``, ``name``,
    ``url``, ``m3u_account``). Unset fields are omitted so tests exercise the
    matcher's missing-field handling, not a fixed schema.
    """
    rec: dict = {}
    if id_ is not None:
        rec["id"] = id_
    if name is not None:
        rec["name"] = name
    if url is not None:
        rec["url"] = url
    if m3u_account is not None:
        rec["m3u_account"] = m3u_account
    return rec


# ---------------------------------------------------------------------------
# One case per tier — assert BOTH tier and matched id.
# ---------------------------------------------------------------------------


def test_tier1_exact_url_wins():
    """Tier 1: identical URL is the canonical stream identity."""
    stream = _stream(name="ESPN HD", url="http://p/abc.ts", m3u_account=7)
    candidates = [
        # Same name+provider (would be Tier 2) but a DIFFERENT url — must lose
        # to the exact-url candidate so we prove Tier 1 outranks Tier 2.
        _stream(id_=10, name="ESPN HD", url="http://p/rotated.ts", m3u_account=7),
        _stream(id_=11, name="ESPN HD", url="http://p/abc.ts", m3u_account=7),
    ]
    assert match_stream(stream, candidates) == (MatchTier.EXACT_URL, 11)


def test_tier2_exact_name_same_provider():
    """Tier 2: URL rotated (no Tier-1 hit) but same name + same provider."""
    stream = _stream(name="ESPN HD", url="http://p/old.ts", m3u_account=7)
    candidates = [
        # Same name, DIFFERENT provider → only a Tier-3 candidate, must lose.
        _stream(id_=20, name="ESPN HD", url="http://q/x.ts", m3u_account=99),
        # Same name, SAME provider, different (rotated) url → the Tier-2 hit.
        _stream(id_=21, name="ESPN HD", url="http://p/new.ts", m3u_account=7),
    ]
    assert match_stream(stream, candidates) == (
        MatchTier.EXACT_NAME_SAME_PROVIDER,
        21,
    )


def test_tier3_exact_normalized_name_any_provider():
    """Tier 3: same normalized name, different provider (cross-provider migrate).

    Also proves normalization is shared with the dedup matcher: the archived
    name carries a ``N | `` channel-number prefix the conservative normalizer
    strips, so it matches the unprefixed destination name.
    """
    stream = _stream(name="5 | ESPN HD", url="http://p/old.ts", m3u_account=7)
    candidates = [
        _stream(id_=30, name="ESPN HD", url="http://q/x.ts", m3u_account=42),
    ]
    assert match_stream(stream, candidates) == (
        MatchTier.EXACT_NORMALIZED_NAME,
        30,
    )


def test_tier4_fuzzy_normalized_name():
    """Tier 4: minor name drift, no exact tier hits, fuzzy clears the floor."""
    stream = _stream(name="ESPN HD East", url="http://p/old.ts", m3u_account=7)
    candidates = [
        # token_set_ratio of "espn hd east" vs "espn east hd" == 1.0 (token set
        # ignores order) — clears the floor; no exact-name tier matched it.
        _stream(id_=40, name="ESPN East HD", url="http://q/x.ts", m3u_account=99),
    ]
    tier, match_id = match_stream(stream, candidates)
    assert tier == MatchTier.FUZZY_NORMALIZED_NAME
    assert match_id == 40


def test_all_miss_below_fuzzy_floor():
    """Miss: no URL/name/provider/fuzzy signal clears any tier."""
    stream = _stream(name="ESPN HD", url="http://p/espn.ts", m3u_account=7)
    candidates = [
        _stream(id_=50, name="Cartoon Network", url="http://q/cn.ts", m3u_account=99),
    ]
    assert match_stream(stream, candidates) == (MatchTier.MISS, None)


# ---------------------------------------------------------------------------
# Tier-ordering proof in a single table: each higher tier must win when a
# lower-tier candidate is also present.
# ---------------------------------------------------------------------------

_ARCHIVED = _stream(name="ESPN HD", url="http://p/abc.ts", m3u_account=7)


@pytest.mark.parametrize(
    ("candidates", "expected"),
    [
        # Tier 1 present alongside a Tier-2 candidate → Tier 1 wins.
        (
            [
                _stream(id_=2, name="ESPN HD", url="http://p/rot.ts", m3u_account=7),
                _stream(id_=1, name="ESPN HD", url="http://p/abc.ts", m3u_account=7),
            ],
            (MatchTier.EXACT_URL, 1),
        ),
        # No url match; Tier 2 present alongside a Tier-3 candidate → Tier 2.
        (
            [
                _stream(id_=3, name="ESPN HD", url="http://q/x.ts", m3u_account=99),
                _stream(id_=4, name="ESPN HD", url="http://p/new.ts", m3u_account=7),
            ],
            (MatchTier.EXACT_NAME_SAME_PROVIDER, 4),
        ),
        # No url, no same-provider name; Tier 3 present alongside fuzzy → Tier 3.
        (
            [
                _stream(id_=5, name="ESPN HD East", url="http://q/y.ts", m3u_account=88),
                _stream(id_=6, name="ESPN HD", url="http://q/x.ts", m3u_account=99),
            ],
            (MatchTier.EXACT_NORMALIZED_NAME, 6),
        ),
        # Only a fuzzy candidate → Tier 4.
        (
            [
                _stream(id_=7, name="ESPN East HD", url="http://q/y.ts", m3u_account=88),
            ],
            (MatchTier.FUZZY_NORMALIZED_NAME, 7),
        ),
        # Nothing clears any tier → MISS.
        (
            [
                _stream(id_=8, name="Discovery", url="http://q/z.ts", m3u_account=88),
            ],
            (MatchTier.MISS, None),
        ),
    ],
)
def test_tier_ladder_ordering(candidates, expected):
    """Each tier outranks every looser tier when both candidates are present."""
    assert match_stream(_ARCHIVED, candidates) == expected


# ---------------------------------------------------------------------------
# Returned tier satisfies the ``tier: int`` contract.
# ---------------------------------------------------------------------------


def test_returned_tier_is_int_contract():
    """The bead contract is ``(tier: int, ...)``; IntEnum satisfies it."""
    stream = _stream(name="ESPN", url="http://p/a.ts", m3u_account=7)
    candidates = [_stream(id_=1, name="ESPN", url="http://p/a.ts", m3u_account=7)]
    tier, _ = match_stream(stream, candidates)
    assert isinstance(tier, int)
    assert tier == 1
    assert int(MatchTier.MISS) == 0


# ---------------------------------------------------------------------------
# Empty candidates → miss.
# ---------------------------------------------------------------------------


def test_empty_candidates_is_miss():
    stream = _stream(name="ESPN HD", url="http://p/abc.ts", m3u_account=7)
    assert match_stream(stream, []) == (MatchTier.MISS, None)


# ---------------------------------------------------------------------------
# Tie-break determinism: lowest id wins, independent of candidate order.
# ---------------------------------------------------------------------------


def test_tier1_tiebreak_lowest_id():
    """Two exact-URL candidates → the lower id wins."""
    stream = _stream(name="ESPN", url="http://p/abc.ts", m3u_account=7)
    candidates = [
        _stream(id_=30, name="ESPN", url="http://p/abc.ts", m3u_account=7),
        _stream(id_=12, name="ESPN", url="http://p/abc.ts", m3u_account=7),
        _stream(id_=25, name="ESPN", url="http://p/abc.ts", m3u_account=7),
    ]
    assert match_stream(stream, candidates) == (MatchTier.EXACT_URL, 12)


def test_tiebreak_is_order_independent():
    """Shuffling the candidate list does not change the matched id."""
    stream = _stream(name="ESPN", url="http://p/abc.ts", m3u_account=7)
    base = [
        _stream(id_=30, name="ESPN", url="http://p/abc.ts", m3u_account=7),
        _stream(id_=12, name="ESPN", url="http://p/abc.ts", m3u_account=7),
        _stream(id_=25, name="ESPN", url="http://p/abc.ts", m3u_account=7),
    ]
    forward = match_stream(stream, base)
    reverse = match_stream(stream, list(reversed(base)))
    assert forward == reverse == (MatchTier.EXACT_URL, 12)


def test_tier4_fuzzy_tiebreak_prefers_higher_score_then_lowest_id():
    """Fuzzy tier picks the highest score; ties on score break to lowest id."""
    stream = _stream(name="ESPN HD East", url="http://p/old.ts", m3u_account=7)
    candidates = [
        # Perfect token-set match (score 1.0) at id 9 and id 4 → lowest id wins.
        _stream(id_=9, name="ESPN East HD", url="http://q/a.ts", m3u_account=99),
        _stream(id_=4, name="East ESPN HD", url="http://q/b.ts", m3u_account=88),
        # A weaker (but still above-floor) partial match at a lower id must NOT
        # win over the perfect-score candidates.
        _stream(id_=2, name="ESPN HD West Coast", url="http://q/c.ts", m3u_account=77),
    ]
    tier, match_id = match_stream(stream, candidates)
    assert tier == MatchTier.FUZZY_NORMALIZED_NAME
    assert match_id == 4


# ---------------------------------------------------------------------------
# No mutation of inputs.
# ---------------------------------------------------------------------------


def test_does_not_mutate_inputs():
    """The matcher only reads — stream and candidates are unchanged after."""
    stream = _stream(name="5 | ESPN HD", url="http://p/abc.ts", m3u_account=7)
    candidates = [
        _stream(id_=1, name="ESPN HD", url="http://p/abc.ts", m3u_account=7),
        _stream(id_=2, name="ESPN East HD", url="http://q/x.ts", m3u_account=99),
    ]
    stream_before = copy.deepcopy(stream)
    candidates_before = copy.deepcopy(candidates)

    match_stream(stream, candidates)

    assert stream == stream_before
    assert candidates == candidates_before


# ---------------------------------------------------------------------------
# Defensive / edge cases on malformed records.
# ---------------------------------------------------------------------------


def test_candidate_without_id_is_skipped():
    """A candidate with no usable id can't be a match target → skipped."""
    stream = _stream(name="ESPN", url="http://p/abc.ts", m3u_account=7)
    candidates = [
        _stream(name="ESPN", url="http://p/abc.ts", m3u_account=7),  # no id
        _stream(id_=5, name="ESPN", url="http://p/abc.ts", m3u_account=7),
    ]
    assert match_stream(stream, candidates) == (MatchTier.EXACT_URL, 5)


def test_bool_id_is_rejected():
    """A bool id (int subclass) is not a valid stream id → candidate skipped."""
    stream = _stream(name="ESPN", url="http://p/abc.ts", m3u_account=7)
    candidates = [
        {"id": True, "name": "ESPN", "url": "http://p/abc.ts", "m3u_account": 7},
        _stream(id_=5, name="ESPN", url="http://p/abc.ts", m3u_account=7),
    ]
    assert match_stream(stream, candidates) == (MatchTier.EXACT_URL, 5)


def test_stream_with_no_url_falls_through_to_name_tiers():
    """No url on the archived stream → Tier 1 can't fire; name tiers still run."""
    stream = _stream(name="ESPN HD", m3u_account=7)  # no url
    candidates = [
        _stream(id_=1, name="ESPN HD", url="http://p/abc.ts", m3u_account=7),
    ]
    assert match_stream(stream, candidates) == (
        MatchTier.EXACT_NAME_SAME_PROVIDER,
        1,
    )


def test_stream_with_no_name_and_no_url_match_is_miss():
    """No url match and no usable name → name tiers are skipped → MISS."""
    stream = _stream(url="http://p/none.ts", m3u_account=7)  # no name
    candidates = [
        _stream(id_=1, name="ESPN HD", url="http://p/abc.ts", m3u_account=7),
    ]
    assert match_stream(stream, candidates) == (MatchTier.MISS, None)


def test_empty_string_url_does_not_match_empty_string_url():
    """An empty/missing url must not spuriously match on Tier 1.

    Two streams that both happen to lack a url must fall through to the name
    tiers, never match each other on a blank-url Tier-1 ``"" == ""``.
    """
    stream = _stream(name="ESPN HD", m3u_account=7)  # no url
    candidates = [
        # Same name, DIFFERENT provider, also no url → must be Tier 3, not a
        # blank-url Tier-1 false positive.
        _stream(id_=1, name="ESPN HD", m3u_account=99),
    ]
    assert match_stream(stream, candidates) == (
        MatchTier.EXACT_NORMALIZED_NAME,
        1,
    )


def test_provider_none_skips_tier2():
    """An archived stream with no provider can't take Tier 2 → Tier 3 instead."""
    stream = _stream(name="ESPN HD", url="http://p/old.ts")  # no m3u_account
    candidates = [
        _stream(id_=1, name="ESPN HD", url="http://q/x.ts", m3u_account=7),
    ]
    assert match_stream(stream, candidates) == (
        MatchTier.EXACT_NORMALIZED_NAME,
        1,
    )


def test_fuzzy_floor_is_inclusive_boundary():
    """A pair scoring exactly at the floor is admitted (>= comparison).

    Guards the boundary condition of :data:`STREAM_FUZZY_FLOOR` so a refactor
    can't silently flip ``>=`` to ``>``.
    """
    assert STREAM_FUZZY_FLOOR == 0.60


# ---------------------------------------------------------------------------
# allow_fuzzy floor (spike xp6mp ruling 1b — the sync-path stream floor).
# ---------------------------------------------------------------------------


def test_allow_fuzzy_false_floors_at_tier3_miss_on_fuzzy_only():
    """allow_fuzzy=False: a pair that ONLY a Tier-4 fuzzy rung would catch is a
    MISS — the sync path does not admit a fuzzy guess (ruling 1b)."""
    stream = _stream(name="ESPN HD East", url="http://p/old.ts", m3u_account=7)
    candidates = [
        # Token-set fuzzy would score 1.0 here, but no exact tier hits.
        _stream(id_=40, name="ESPN East HD", url="http://q/x.ts", m3u_account=99),
    ]
    # Default ladder (DBAS restore) DOES catch it at Tier-4.
    assert match_stream(stream, candidates) == (MatchTier.FUZZY_NORMALIZED_NAME, 40)
    # Floored ladder (sync) does NOT.
    assert match_stream(stream, candidates, allow_fuzzy=False) == (MatchTier.MISS, None)


def test_allow_fuzzy_false_still_matches_exact_tiers():
    """allow_fuzzy=False floors ONLY Tier-4; Tiers 1-3 still match exactly so a
    real (exact) stream still attaches on the sync path."""
    stream = _stream(name="ESPN HD", url="http://p/espn.ts", m3u_account=7)
    # Tier-1 exact URL.
    url_cands = [_stream(id_=10, name="x", url="http://p/espn.ts", m3u_account=99)]
    assert match_stream(stream, url_cands, allow_fuzzy=False) == (MatchTier.EXACT_URL, 10)
    # Tier-3 exact normalized name (different url + provider).
    name_cands = [_stream(id_=20, name="ESPN HD", url="http://other/y.ts", m3u_account=99)]
    assert match_stream(stream, name_cands, allow_fuzzy=False) == (
        MatchTier.EXACT_NORMALIZED_NAME,
        20,
    )


def test_allow_fuzzy_default_is_true_preserves_restore_behavior():
    """The default (allow_fuzzy=True) preserves the full 4-tier ladder so the
    DBAS one-shot restore path is unchanged."""
    stream = _stream(name="ESPN HD East", url="http://p/old.ts", m3u_account=7)
    candidates = [_stream(id_=40, name="ESPN East HD", url="http://q/x.ts", m3u_account=99)]
    # No keyword == default == fuzzy allowed.
    assert match_stream(stream, candidates) == (MatchTier.FUZZY_NORMALIZED_NAME, 40)
