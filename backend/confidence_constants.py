"""
Shared confidence-floor constant for the dedup/fuzzy-match subsystem.

Split out from ``services.dedup_matcher`` so the settings validator in
``config`` can import ``CONFIDENCE_FLOOR`` without forming an import cycle
(``config`` → ``dedup_matcher`` → ``normalization_engine`` → ``database`` →
``config``). This module is a pure leaf — it imports nothing intra-app — so
no module that reads ``CONFIDENCE_FLOOR`` participates in that cycle. See
bead enhancedchannelmanager-0nabr for the topology rationale; mirrors the
``db_base.Base`` split (bead wlvxh).

``services.dedup_matcher`` re-exports ``CONFIDENCE_FLOOR`` from here so the
existing ``from services.dedup_matcher import CONFIDENCE_FLOOR`` call sites
keep working without a sweeping rewrite, and so there remains exactly one
source of truth for the value (the matcher and the validator cannot drift).

Hard confidence floor — defense-in-depth integrity constraint per ADR-008
§D2. Changing this is an ADR addendum, not a runtime config change — see
ADR-008 §D2 final paragraph.
"""

CONFIDENCE_FLOOR: float = 0.60

__all__ = ["CONFIDENCE_FLOOR"]
