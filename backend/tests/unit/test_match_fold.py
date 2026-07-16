"""Unit tests for the shared fold-match-key canonicalization helper.

GH #645 / bead enhancedchannelmanager-0vao3: the opt-in ``fold_match_key``
rule flag and the Find Duplicates "ignore spacing differences" toggle must
use ONE canonicalization (casefold + strip ALL whitespace) so the two
surfaces cannot drift. The helper produces a COMPARISON KEY only — visible
channel names are never altered (docs/normalization.md parity contract).
"""


class TestFoldMatchKey:
    """Behavior of match_fold.fold_match_key."""

    def test_casefold_and_whitespace_removal(self):
        from match_fold import fold_match_key

        # The exact four spellings from the GH #645 report must collapse
        # to one key.
        keys = {
            fold_match_key("eurosport 2"),
            fold_match_key("Eurosport 2"),
            fold_match_key("Eurosport2"),
            fold_match_key("eurosport2"),
        }
        assert keys == {"eurosport2"}

    def test_interior_and_edge_whitespace_all_removed(self):
        from match_fold import fold_match_key

        assert fold_match_key("  ESPN   News  HD ") == "espnnewshd"
        assert fold_match_key("ESPN\tNews HD") == "espnnewshd"

    def test_casefold_not_just_lower(self):
        from match_fold import fold_match_key

        # casefold handles characters lower() does not (e.g. German eszett).
        assert fold_match_key("STRASSE") == fold_match_key("straße")

    def test_distinct_names_stay_distinct(self):
        from match_fold import fold_match_key

        # The fold must NOT merge anything beyond whitespace/case.
        assert fold_match_key("Eurosport 2") != fold_match_key("Eurosport 3")
        assert fold_match_key("Eurosport 2") != fold_match_key("Eurosport 2 HD")
        assert fold_match_key("ESPN") != fold_match_key("ESPN2")

    def test_empty_and_whitespace_only(self):
        from match_fold import fold_match_key

        assert fold_match_key("") == ""
        assert fold_match_key("   \t ") == ""


class TestFoldMatchKeyParity:
    """Both consumer surfaces must reference the SAME helper object.

    If either the auto-creation executor or the find-duplicates endpoint
    ever grows its own local canonicalization, this test fails — one shared
    helper, two call sites (PO-approved design point 3).
    """

    def test_executor_uses_shared_helper(self):
        import match_fold
        import channel_pipeline_executor

        assert channel_pipeline_executor.fold_match_key is match_fold.fold_match_key

    def test_find_duplicates_router_uses_shared_helper(self):
        import match_fold
        import routers.channels as channels_router

        assert channels_router.fold_match_key is match_fold.fold_match_key
