"""
Normalization Rule Engine

Processes stream names through configurable rules to normalize them for
channel creation and matching. Supports regex patterns, multiple condition
types, and various transformation actions.

The engine loads rules from the database, organized into groups with
priority ordering. Rules execute in order until a match with stop_processing
is found, or all rules have been evaluated.

Unified NormalizationPolicy (bd-eio04.1, closes GH #104)
--------------------------------------------------------
The Test Rules preview path (`engine.test_rule`,
`engine.test_rules_batch`) and the auto-creation execution path
(`engine.normalize`) now share a single NormalizationPolicy. Both paths
apply identical Unicode preprocessing before condition matching:
  * NFC canonicalization (unicodedata.normalize('NFC', ...)) — collapses
    NFD-decomposed input like 'Café' (e + U+0301) to pre-composed form.
    NFC, not NFKC, to avoid over-normalizing ligatures / fullwidth /
    Roman-numeral compatibility forms.
  * Strip a narrow whitelist of invisible Cf code points: U+200B ZWSP,
    U+200C ZWNJ, U+200D ZWJ, U+FEFF BOM. RTL marks (U+200F, U+202E) are
    preserved.
  * Convert ALL Unicode superscripts to ASCII (both letter-superscripts
    ᴴᴰ -> HD and numeric superscripts ² -> 2). The prior
    `preserve_superscripts` kwarg / `preserve_numeric` carve-out
    introduced by PR #61 / bd-yui1k is deleted outright — divergence
    between the two code paths was the root cause of GH #104.

Operator knobs
--------------
ECM_NORMALIZATION_UNIFIED_POLICY (default: "true")
    Feature flag for the unified policy. Set to "false" (or "0") to
    roll back to the pre-bd-eio04.1 behavior (separate superscript
    carve-outs, no NFC, no Cf-stripping) without re-deploying. Intended
    as a one-pull-request rollback switch; remove once the new policy
    has soaked in production.

ECM_NORMALIZATION_CONFUSABLES_FOLD (default: "false", bd-sc5s4)
    Opt-in. When "true" (or "1"/"yes"/"on"), the policy applies a
    Unicode-TR39-style confusables fold AFTER NFC + Cf-stripping +
    superscript conversion. Cyrillic/Greek/math/fullwidth look-alikes
    fold to their Latin/ASCII visual equivalents, which makes
    homoglyph-disguised inputs (e.g. Cyrillic 'а' U+0430 vs Latin 'a'
    U+0061) compare equal. Off by default because the fold is
    aggressive and can collapse characters that some operators want
    distinct (Greek 'Ω' Ohm sign vs Latin 'O', math italics, etc.).
    Filed under the homoglyph attack threat — see docs/normalization.md.
"""
import os
import re
import logging
import time
import unicodedata
from typing import Optional
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

import safe_regex  # bd-eio04.14: ReDoS-guarded wrapper for user-supplied regex
from models import NormalizationRule, NormalizationRuleGroup, TagGroup, Tag

logger = logging.getLogger(__name__)


# Policy version string emitted into the structured decision log (bd-eio04.9).
# Bumped when the unified-policy flag flips so on-call can correlate
# divergence reports with the policy the run was using.
POLICY_VERSION_UNIFIED = "unified-v1"
POLICY_VERSION_LEGACY = "legacy"


def _current_policy_version(policy_obj: "NormalizationPolicy") -> str:
    """Return the canonical policy-version tag for the decision log."""
    return POLICY_VERSION_UNIFIED if policy_obj.unified_enabled else POLICY_VERSION_LEGACY


# -----------------------------------------------------------------------------
# Feature flag — operators can toggle to fall back to pre-bd-eio04.1 behavior.
# Evaluated once at import time. To change at runtime, restart the container.
# -----------------------------------------------------------------------------

def _unified_policy_enabled() -> bool:
    """Parse ECM_NORMALIZATION_UNIFIED_POLICY. Default-on."""
    raw = os.environ.get("ECM_NORMALIZATION_UNIFIED_POLICY", "true")
    return raw.strip().lower() not in {"false", "0", "no", "off"}


def _confusables_fold_enabled() -> bool:
    """Parse ECM_NORMALIZATION_CONFUSABLES_FOLD. Default-OFF (opt-in, bd-sc5s4)."""
    raw = os.environ.get("ECM_NORMALIZATION_CONFUSABLES_FOLD", "false")
    return raw.strip().lower() in {"true", "1", "yes", "on"}


# Cf code points stripped by the unified policy. Intentionally narrow: we
# only strip invisible markers that commonly appear as copy-paste artifacts
# (ZWJ/ZWSP/ZWNJ/BOM). RTL/LTR bidi marks (U+200F, U+202E) are preserved
# because they can carry legitimate directional meaning in channel names.
_STRIPPED_CF_CODEPOINTS = frozenset({
    "​",  # ZERO WIDTH SPACE
    "‌",  # ZERO WIDTH NON-JOINER
    "‍",  # ZERO WIDTH JOINER
    "﻿",  # BYTE ORDER MARK / ZERO WIDTH NO-BREAK SPACE
})


# Cache for tag groups to avoid repeated database queries
_tag_group_cache: dict[int, list[tuple[str, bool]]] = {}  # group_id -> [(value, case_sensitive), ...]


def invalidate_tag_cache():
    """Clear the global tag caches so the next access reloads from DB."""
    _tag_group_cache.clear()
    NormalizationEngine._tag_group_id_cache.clear()
    clear_abbreviation_cache()


# Unicode superscript to ASCII mapping. The historical split into
# LETTER_SUPERSCRIPTS + NUMERIC_SUPERSCRIPTS (bd-yui1k) was retained only
# to support the `preserve_superscripts` kwarg, which bd-eio04.1 removed
# outright — both letter AND numeric superscripts now convert on ALL
# paths. The two maps are preserved as internal detail so historical
# imports of LETTER_SUPERSCRIPTS / NUMERIC_SUPERSCRIPTS / SUPERSCRIPT_MAP
# continue to resolve.
#
# Common letter patterns used as quality tags:
#   ᴴᴰ (HD), ᶠᴴᴰ (FHD), ᵁᴴᴰ (UHD), ᴿᴬᵂ (RAW), ˢᴰ (SD), etc.
# Common numeric patterns: ESPN² (ESPN2), ⁶⁰fps (60fps), ¹²⁰Hz (120Hz).
LETTER_SUPERSCRIPTS = {
    # Uppercase superscripts
    '\u1d2c': 'A',  # ᴬ
    '\u1d2e': 'B',  # ᴮ
    '\u1d30': 'D',  # ᴰ
    '\u1d31': 'E',  # ᴱ
    '\u1d33': 'G',  # ᴳ
    '\u1d34': 'H',  # ᴴ
    '\u1d35': 'I',  # ᴵ
    '\u1d36': 'J',  # ᴶ
    '\u1d37': 'K',  # ᴷ
    '\u1d38': 'L',  # ᴸ
    '\u1d39': 'M',  # ᴹ
    '\u1d3a': 'N',  # ᴺ
    '\u1d3c': 'O',  # ᴼ
    '\u1d3e': 'P',  # ᴾ
    '\u1d3f': 'R',  # ᴿ
    '\u1d40': 'T',  # ᵀ
    '\u1d41': 'U',  # ᵁ
    '\u1d42': 'W',  # ᵂ
    '\u2c7d': 'V',  # ⱽ
    # Lowercase superscripts
    '\u1d43': 'a',  # ᵃ
    '\u1d47': 'b',  # ᵇ
    '\u1d48': 'd',  # ᵈ
    '\u1d49': 'e',  # ᵉ
    '\u1da0': 'f',  # ᶠ
    '\u1d4d': 'g',  # ᵍ
    '\u02b0': 'h',  # ʰ
    '\u2071': 'i',  # ⁱ
    '\u02b2': 'j',  # ʲ
    '\u1d4f': 'k',  # ᵏ
    '\u02e1': 'l',  # ˡ
    '\u1d50': 'm',  # ᵐ
    '\u207f': 'n',  # ⁿ
    '\u1d52': 'o',  # ᵒ
    '\u1d56': 'p',  # ᵖ
    '\u02b3': 'r',  # ʳ
    '\u02e2': 's',  # ˢ
    '\u1d57': 't',  # ᵗ
    '\u1d58': 'u',  # ᵘ
    '\u1d5b': 'v',  # ᵛ
    '\u02b7': 'w',  # ʷ
    '\u02e3': 'x',  # ˣ
    '\u02b8': 'y',  # ʸ
    '\u1dbb': 'z',  # ᶻ
}

# Numeric and math-symbol superscripts (ESPN², ⁺⁻⁼⁽⁾). Prior to
# bd-yui1k these were not in the conversion map at all — PR #61's
# "preserve" flag worked for ESPN² only by accident. bd-eio04.1 drops
# the preservation carve-out entirely; numerics convert on every path.
NUMERIC_SUPERSCRIPTS = {
    '\u2070': '0',  # ⁰
    '\u00b9': '1',  # ¹
    '\u00b2': '2',  # ²
    '\u00b3': '3',  # ³
    '\u2074': '4',  # ⁴
    '\u2075': '5',  # ⁵
    '\u2076': '6',  # ⁶
    '\u2077': '7',  # ⁷
    '\u2078': '8',  # ⁸
    '\u2079': '9',  # ⁹
    '\u207a': '+',  # ⁺
    '\u207b': '-',  # ⁻
    '\u207c': '=',  # ⁼
    '\u207d': '(',  # ⁽
    '\u207e': ')',  # ⁾
}

# Combined superscript map used by convert_superscripts(). Exported for
# historical callers / tests that import it directly.
SUPERSCRIPT_MAP = {**LETTER_SUPERSCRIPTS, **NUMERIC_SUPERSCRIPTS}


# -----------------------------------------------------------------------------
# Confusables fold (bd-sc5s4) — Unicode TR39 skeleton-style mapping for the
# most common homoglyph attack pairs. NOT the full TR39 confusables.txt
# table (~6000 entries); a curated subset covering the realistic threat:
#
#   * Cyrillic letters that share a glyph with Latin (А/а/В/Е/е/К/М/...).
#   * Greek capitals that share a glyph with Latin (Α/Β/Ε/Ζ/Η/Ι/Κ/Μ/Ν/Ο/...).
#   * Mathematical alphanumeric symbols (𝐀-𝐳, 𝟎-𝟗, etc.) — bulk-folded
#     by NFKC, but fullwidth/halfwidth Latin (Ａ-ｚ, ０-９) is kept here
#     explicitly so we don't have to invoke NFKC and lose the
#     ligature/Roman-numeral preservation that the unified policy
#     intentionally guards via NFC-not-NFKC.
#   * Common digit/letter look-alikes are NOT folded by default (O/0,
#     I/1/l) — those collisions cause more false positives than they
#     prevent attacks, and the bead description flags them as
#     speculative. Operators who want them can layer a normalization
#     rule of their own.
#
# Sources cross-referenced:
#   * Unicode TR39 confusables.txt (Latin-target rows).
#   * unicode-security.org/confusables.html.
# Curated for IPTV channel name surfaces — covers the alphabets most
# likely to appear in M3U feeds (Cyrillic, Greek, fullwidth CJK forms).
# -----------------------------------------------------------------------------

CONFUSABLES_MAP: dict[str, str] = {
    # Cyrillic uppercase that look like Latin uppercase
    'А': 'A',  # А CYRILLIC CAPITAL A
    'В': 'B',  # В CYRILLIC CAPITAL VE
    'С': 'C',  # С CYRILLIC CAPITAL ES
    'Е': 'E',  # Е CYRILLIC CAPITAL IE
    'Н': 'H',  # Н CYRILLIC CAPITAL EN
    'І': 'I',  # І CYRILLIC CAPITAL BYELORUSSIAN-UKRAINIAN I
    'Ј': 'J',  # Ј CYRILLIC CAPITAL JE
    'К': 'K',  # К CYRILLIC CAPITAL KA
    'М': 'M',  # М CYRILLIC CAPITAL EM
    'О': 'O',  # О CYRILLIC CAPITAL O
    'Р': 'P',  # Р CYRILLIC CAPITAL ER
    'Ѕ': 'S',  # Ѕ CYRILLIC CAPITAL DZE
    'Т': 'T',  # Т CYRILLIC CAPITAL TE
    'Х': 'X',  # Х CYRILLIC CAPITAL HA
    'У': 'Y',  # У CYRILLIC CAPITAL U (looks like Latin Y)
    # Cyrillic lowercase that look like Latin lowercase
    'а': 'a',  # а CYRILLIC SMALL A
    'с': 'c',  # с CYRILLIC SMALL ES
    'е': 'e',  # е CYRILLIC SMALL IE
    'һ': 'h',  # һ CYRILLIC SMALL SHHA
    'і': 'i',  # і CYRILLIC SMALL BYELORUSSIAN-UKRAINIAN I
    'ј': 'j',  # ј CYRILLIC SMALL JE
    'о': 'o',  # о CYRILLIC SMALL O
    'р': 'p',  # р CYRILLIC SMALL ER
    'ѕ': 's',  # ѕ CYRILLIC SMALL DZE
    'х': 'x',  # х CYRILLIC SMALL HA
    'у': 'y',  # у CYRILLIC SMALL U (looks like Latin y)
    # Greek capitals that look like Latin capitals
    'Α': 'A',  # Α GREEK CAPITAL ALPHA
    'Β': 'B',  # Β GREEK CAPITAL BETA
    'Ε': 'E',  # Ε GREEK CAPITAL EPSILON
    'Ζ': 'Z',  # Ζ GREEK CAPITAL ZETA
    'Η': 'H',  # Η GREEK CAPITAL ETA
    'Ι': 'I',  # Ι GREEK CAPITAL IOTA
    'Κ': 'K',  # Κ GREEK CAPITAL KAPPA
    'Μ': 'M',  # Μ GREEK CAPITAL MU
    'Ν': 'N',  # Ν GREEK CAPITAL NU
    'Ο': 'O',  # Ο GREEK CAPITAL OMICRON
    'Ρ': 'P',  # Ρ GREEK CAPITAL RHO
    'Τ': 'T',  # Τ GREEK CAPITAL TAU
    'Υ': 'Y',  # Υ GREEK CAPITAL UPSILON
    'Χ': 'X',  # Χ GREEK CAPITAL CHI
    # Greek lowercase that resemble Latin lowercase (common subset)
    'ο': 'o',  # ο GREEK SMALL OMICRON
    'ρ': 'p',  # ρ GREEK SMALL RHO (descender, but commonly substituted)
    'υ': 'u',  # υ GREEK SMALL UPSILON
    # Math italic / sans-serif Latin (Mathematical Alphanumeric Symbols block).
    # NFKC would do this in bulk but also collapses ligatures/fullwidth which
    # the unified policy intentionally preserves. Listed individually below
    # for the math-italic block (U+1D400 - U+1D7FF) range so we can be
    # surgical. Programmatic build: each block is 26 letters, then the next
    # block starts. We hand-roll the most common attack-relevant ones.
    '\U0001d400': 'A', '\U0001d401': 'B', '\U0001d402': 'C', '\U0001d403': 'D',
    '\U0001d404': 'E', '\U0001d405': 'F', '\U0001d406': 'G', '\U0001d407': 'H',
    '\U0001d408': 'I', '\U0001d409': 'J', '\U0001d40a': 'K', '\U0001d40b': 'L',
    '\U0001d40c': 'M', '\U0001d40d': 'N', '\U0001d40e': 'O', '\U0001d40f': 'P',
    '\U0001d410': 'Q', '\U0001d411': 'R', '\U0001d412': 'S', '\U0001d413': 'T',
    '\U0001d414': 'U', '\U0001d415': 'V', '\U0001d416': 'W', '\U0001d417': 'X',
    '\U0001d418': 'Y', '\U0001d419': 'Z',
    # Math italic lowercase (U+1D44E onward — note U+1D455 is reserved)
    '\U0001d44e': 'a', '\U0001d44f': 'b', '\U0001d450': 'c', '\U0001d451': 'd',
    '\U0001d452': 'e', '\U0001d453': 'f', '\U0001d454': 'g',
    '\U0001d456': 'i', '\U0001d457': 'j', '\U0001d458': 'k', '\U0001d459': 'l',
    '\U0001d45a': 'm', '\U0001d45b': 'n', '\U0001d45c': 'o', '\U0001d45d': 'p',
    '\U0001d45e': 'q', '\U0001d45f': 'r', '\U0001d460': 's', '\U0001d461': 't',
    '\U0001d462': 'u', '\U0001d463': 'v', '\U0001d464': 'w', '\U0001d465': 'x',
    '\U0001d466': 'y', '\U0001d467': 'z',
    # Math digits 0-9 (Mathematical Bold Digits block U+1D7CE-U+1D7D7)
    '\U0001d7ce': '0', '\U0001d7cf': '1', '\U0001d7d0': '2', '\U0001d7d1': '3',
    '\U0001d7d2': '4', '\U0001d7d3': '5', '\U0001d7d4': '6', '\U0001d7d5': '7',
    '\U0001d7d6': '8', '\U0001d7d7': '9',
    # Fullwidth Latin (NFKC would also fold these, but doing it explicitly
    # lets us keep NFC and only fold the fullwidth letters/digits, not
    # ligatures / Roman numerals / squared-character compatibility forms).
    'Ａ': 'A', 'Ｂ': 'B', 'Ｃ': 'C', 'Ｄ': 'D', 'Ｅ': 'E',
    'Ｆ': 'F', 'Ｇ': 'G', 'Ｈ': 'H', 'Ｉ': 'I', 'Ｊ': 'J',
    'Ｋ': 'K', 'Ｌ': 'L', 'Ｍ': 'M', 'Ｎ': 'N', 'Ｏ': 'O',
    'Ｐ': 'P', 'Ｑ': 'Q', 'Ｒ': 'R', 'Ｓ': 'S', 'Ｔ': 'T',
    'Ｕ': 'U', 'Ｖ': 'V', 'Ｗ': 'W', 'Ｘ': 'X', 'Ｙ': 'Y',
    'Ｚ': 'Z',
    'ａ': 'a', 'ｂ': 'b', 'ｃ': 'c', 'ｄ': 'd', 'ｅ': 'e',
    'ｆ': 'f', 'ｇ': 'g', 'ｈ': 'h', 'ｉ': 'i', 'ｊ': 'j',
    'ｋ': 'k', 'ｌ': 'l', 'ｍ': 'm', 'ｎ': 'n', 'ｏ': 'o',
    'ｐ': 'p', 'ｑ': 'q', 'ｒ': 'r', 'ｓ': 's', 'ｔ': 't',
    'ｕ': 'u', 'ｖ': 'v', 'ｗ': 'w', 'ｘ': 'x', 'ｙ': 'y',
    'ｚ': 'z',
    '０': '0', '１': '1', '２': '2', '３': '3', '４': '4',
    '５': '5', '６': '6', '７': '7', '８': '8', '９': '9',
}


def fold_confusables(text: str) -> str:
    """
    Map Unicode confusables (homoglyphs) to their canonical Latin/ASCII form.

    Subset of Unicode TR39 confusables.txt focused on the realistic
    homoglyph-attack surface for IPTV channel names: Cyrillic, Greek,
    math italic, fullwidth. Pure ASCII strings are returned unchanged
    (fast-path bail-out), preserving the regression contract for ASCII
    callers.

    bd-sc5s4. Opt-in via ECM_NORMALIZATION_CONFUSABLES_FOLD; not invoked
    by the default policy.
    """
    if not text:
        return text
    if text.isascii():
        return text
    return ''.join(CONFUSABLES_MAP.get(ch, ch) for ch in text)


def convert_superscripts(text: str) -> str:
    """
    Convert Unicode superscript characters to their ASCII equivalents.

    Under the unified NormalizationPolicy (bd-eio04.1), every superscript
    — both letter-superscripts (ᴴᴰ -> HD, ᴿᴬᵂ -> RAW) and numeric
    superscripts (² -> 2, ⁶⁰ -> 60) — converts on every code path. The
    prior `preserve_numeric` kwarg is removed; divergence between paths
    was the bug class behind GH #104.

    When the ECM_NORMALIZATION_UNIFIED_POLICY flag is disabled, this
    function also applies NFC canonicalization and Cf-stripping at the
    top of the pipeline (inside NormalizationPolicy.apply_to_text). When
    the flag is enabled (default), NormalizationPolicy is the canonical
    entry point; call it directly rather than this helper if you want
    the full preprocessing.
    """
    if not text:
        return text
    # NFC canonicalize so NFD-decomposed inputs (e + U+0301) look
    # identical to pre-composed inputs (U+00E9) after conversion.
    text = unicodedata.normalize("NFC", text)
    return ''.join(SUPERSCRIPT_MAP.get(ch, ch) for ch in text)


# -----------------------------------------------------------------------------
# NormalizationPolicy — single canonical Unicode preprocessor shared by the
# Test Rules and Auto-Create code paths. Introduced for bd-eio04.1 to close
# the divergence behind GH #104. The dataclass is intentionally minimal and
# frozen so .4 (observability fields) and .9 (hook fields) can add members
# later without breaking callers.
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class NormalizationPolicy:
    """
    Unified Unicode preprocessing policy.

    The policy is a frozen dataclass applied identically by
    `NormalizationEngine.normalize` and `NormalizationEngine.test_rule` /
    `test_rules_batch`. It exists so subsequent beads (bd-eio04.9 for
    observability, others for further unification) can add fields
    without hunting down preprocessor call sites.

    Attributes:
        unified_enabled: When True (default, gated by
            ECM_NORMALIZATION_UNIFIED_POLICY env var), apply NFC +
            Cf-stripping + full superscript conversion at every policy
            entry point. When False, fall back to the pre-bd-eio04.1
            behavior (superscript conversion only, no NFC, no
            Cf-stripping) so operators have a rollback switch.
        confusables_fold: When True (opt-in, gated by
            ECM_NORMALIZATION_CONFUSABLES_FOLD env var, bd-sc5s4),
            apply a curated Unicode-TR39-style homoglyph fold AFTER
            NFC + Cf-stripping + superscripts. Folds Cyrillic/Greek/
            math-italic/fullwidth look-alikes to Latin/ASCII so
            disguised inputs (Cyrillic 'а' vs Latin 'a') compare equal.
            Off by default — aggressive, can collapse intentionally
            distinct characters. Has no effect when unified_enabled is
            False (the legacy rollback path is byte-for-byte preserved).
    """

    unified_enabled: bool = True
    confusables_fold: bool = False

    def apply_to_text(self, text: str) -> str:
        """
        Apply the policy to an input text. Returns a new string.

        Order (under unified_enabled=True):
            1. NFC canonicalize.
            2. Strip whitelisted Cf code points (ZWSP/ZWNJ/ZWJ/BOM).
            3. Convert superscripts to ASCII (letters AND numerics).
            4. (opt-in, bd-sc5s4) Fold confusables to Latin/ASCII.

        Under unified_enabled=False, only step (3) runs, via the legacy
        preserve_numeric=False path — this matches pre-bd-eio04.1
        default behavior. The confusables fold is intentionally
        suppressed on the legacy path so the rollback switch retains
        its byte-for-byte semantic.
        """
        if not text:
            return text
        if self.unified_enabled:
            text = unicodedata.normalize("NFC", text)
            if any(ch in _STRIPPED_CF_CODEPOINTS for ch in text):
                text = ''.join(
                    ch for ch in text if ch not in _STRIPPED_CF_CODEPOINTS
                )
            text = ''.join(SUPERSCRIPT_MAP.get(ch, ch) for ch in text)
            if self.confusables_fold:
                text = fold_confusables(text)
            return text
        # Legacy fallback path — behavior prior to bd-eio04.1. Keeps
        # superscript conversion (bd-yui1k behavior with
        # preserve_numeric=False) but NO NFC, NO Cf-stripping, and NO
        # confusables fold (rollback switch preserves prior bytes).
        return ''.join(SUPERSCRIPT_MAP.get(ch, ch) for ch in text)


# Process-wide canonical policy instance. Both code paths read this. The
# instance is created at module load so the env-var feature flag is
# latched once; to change, restart the container.
_DEFAULT_POLICY = NormalizationPolicy(
    unified_enabled=_unified_policy_enabled(),
    confusables_fold=_confusables_fold_enabled(),
)


def get_default_policy() -> NormalizationPolicy:
    """Return the process-wide NormalizationPolicy instance."""
    return _DEFAULT_POLICY


def _reset_default_policy_for_tests() -> NormalizationPolicy:
    """Re-read the ECM_NORMALIZATION_UNIFIED_POLICY and
    ECM_NORMALIZATION_CONFUSABLES_FOLD env vars and refresh the singleton.
    ONLY for tests — production toggles the flags via container restart,
    not at runtime.
    """
    global _DEFAULT_POLICY
    _DEFAULT_POLICY = NormalizationPolicy(
        unified_enabled=_unified_policy_enabled(),
        confusables_fold=_confusables_fold_enabled(),
    )
    return _DEFAULT_POLICY


@dataclass
class RuleMatch:
    """Result of a rule match attempt."""
    matched: bool
    match_start: int = -1
    match_end: int = -1
    groups: tuple = ()  # Captured groups for regex
    matched_tag: str = ""  # The tag value that matched (for tag_group conditions)


@dataclass
class NormalizationResult:
    """Result of normalizing a stream name."""
    original: str
    normalized: str
    rules_applied: list  # List of rule IDs that were applied
    transformations: list  # List of (rule_id, before, after) tuples


# Well-known broadcast/network abbreviations that contain vowels.
# These can't be detected by structural heuristics alone because they
# look like regular short words (HBO, AMC, ABC, ESPN, etc.)
# Words that should stay lowercase in title case (unless first word)
# Fallback small words used when DB tag group isn't available
_DEFAULT_SMALL_WORDS = {
    'a', 'an', 'the', 'and', 'but', 'or', 'for', 'nor',
    'of', 'at', 'by', 'to', 'in', 'on', 'vs', 'via', 'my',
    'en', 'de', 'el', 'la', 'le', 'du', 'des', 'les', 'dos', 'das',
}

# Cache for small word tags loaded from the database
_small_words_cache: set[str] | None = None

# Cache for abbreviation tags loaded from the database
_abbreviation_tags_cache: set[str] | None = None


def _load_abbreviation_tags() -> set[str]:
    """Load abbreviation tags from the 'Abbreviation Tags' tag group."""
    global _abbreviation_tags_cache
    if _abbreviation_tags_cache is not None:
        return _abbreviation_tags_cache

    try:
        from database import get_session
        from models import TagGroup, Tag
        session = get_session()
        try:
            group = session.query(TagGroup).filter(TagGroup.name == "Abbreviation Tags").first()
            if group:
                tags = session.query(Tag).filter(
                    Tag.group_id == group.id, Tag.enabled == True
                ).all()
                _abbreviation_tags_cache = {t.value.upper() for t in tags}
            else:
                _abbreviation_tags_cache = set()
        finally:
            session.close()
    except Exception:
        _abbreviation_tags_cache = set()

    return _abbreviation_tags_cache


def _load_small_words() -> set[str]:
    """Load small words from the 'Small Word Tags' tag group."""
    global _small_words_cache
    if _small_words_cache is not None:
        return _small_words_cache

    try:
        from database import get_session
        from models import TagGroup, Tag
        session = get_session()
        try:
            group = session.query(TagGroup).filter(TagGroup.name == "Small Word Tags").first()
            if group:
                tags = session.query(Tag).filter(
                    Tag.group_id == group.id, Tag.enabled == True
                ).all()
                _small_words_cache = {t.value.lower() for t in tags}
            else:
                _small_words_cache = _DEFAULT_SMALL_WORDS
        finally:
            session.close()
    except Exception:
        _small_words_cache = _DEFAULT_SMALL_WORDS

    return _small_words_cache


def clear_abbreviation_cache():
    """Clear all title-case related caches (call when tags are modified)."""
    global _abbreviation_tags_cache, _small_words_cache
    _abbreviation_tags_cache = None
    _small_words_cache = None


def _is_abbreviation(word: str) -> bool:
    """
    Check if an all-uppercase word looks like an abbreviation that should
    stay uppercase during title-casing.

    Only called for all-uppercase words. Mixed-case words (PureFlix, PlayersTV)
    are preserved as-is by the caller.
    """
    alpha = ''.join(c for c in word if c.isalpha())
    if not alpha:
        return False

    # Common short words are NOT abbreviations (BY, OF, THE, WAR, etc.)
    if alpha.lower() in _load_small_words():
        return False

    # Single letter: keep uppercase
    if len(alpha) == 1:
        return True

    # Contains & (A&E, AT&T) — treat as abbreviation
    if '&' in word:
        return True

    # Known abbreviations from the Abbreviation Tags tag group
    # Check both the alpha-only form and the full word (for C-SPAN, etc.)
    abbr_tags = _load_abbreviation_tags()
    if alpha in abbr_tags or word.upper() in abbr_tags:
        return True

    # Contains digits mixed with letters: abbreviation (ESPN2, MSGSN2)
    if any(c.isdigit() for c in word) and alpha:
        return True

    # Broadcast callsigns: W/K + 3 letters = 4 chars (WLUK, WGBA, KABC)
    # or 5 chars (WDCW). 3-letter W/K words (WAR, WIN, KEY) are common English words.
    # Longer W/K words (WASHINGTON, WEST) are also regular words.
    if 4 <= len(alpha) <= 5 and alpha[0] in 'WK':
        return True

    # No vowels = abbreviation (TNT, CNN, FXX, MSNBC, HGTV, NBCSN, NBCLX, NYC)
    # BY/MY are handled by small words check above so Y is not treated as a vowel
    vowels = sum(1 for c in alpha if c in 'AEIOU')
    if vowels == 0:
        return True

    return False


def _smart_title_word(word: str, is_first: bool) -> str:
    """
    Apply smart title-casing to a single word.

    Rules:
    - Mixed-case words (PureFlix, PlayersTV) → preserved as-is
    - All-uppercase abbreviations (ESPN, CBS, TNT) → preserved
    - All-uppercase regular words (WASHINGTON, CITY) → Title-cased
    - All-lowercase words → Title-cased (unless small word and not first)
    - Handles apostrophes correctly ('90s stays '90s, not '90S)
    - Handles ampersands (A&E stays A&E)
    """
    if not word:
        return word

    alpha = ''.join(c for c in word if c.isalpha())
    if not alpha:
        return word  # Numbers, punctuation only — leave as-is

    # Mixed-case: user already has intentional casing (PureFlix, PlayersTV)
    if not alpha.isupper() and not alpha.islower():
        return word

    # All-uppercase: check if abbreviation
    if alpha.isupper():
        if _is_abbreviation(word):
            return word
        # Small words stay lowercase (unless first word)
        if not is_first and alpha.lower() in _load_small_words():
            return word.lower()
        # Regular all-caps word — title case it
        return _title_case_word(word, is_first)

    # All-lowercase
    # If alpha portion is just a suffix on digits/punctuation ('90s, 24th),
    # don't capitalize — the letter is a grammatical suffix, not a word start
    non_alpha_prefix = ''
    for ch in word:
        if ch.isalpha():
            break
        non_alpha_prefix += ch
    if non_alpha_prefix and len(alpha) <= 2:
        return word  # e.g., '90s, 24th — leave as-is

    if not is_first and alpha.lower() in _load_small_words():
        return word  # Keep small words lowercase

    return _title_case_word(word, is_first)


def _title_case_word(word: str, is_first: bool) -> str:
    """
    Title-case a word while handling apostrophes and special chars correctly.
    Python's str.title() treats ' as a word boundary, turning '90s into '90S.
    """
    result = []
    capitalize_next = True
    for i, ch in enumerate(word):
        if ch.isalpha():
            if capitalize_next:
                result.append(ch.upper())
                capitalize_next = False
            else:
                result.append(ch.lower())
        else:
            result.append(ch)
            # Don't capitalize after apostrophes or digits
            # Only capitalize after spaces (which won't appear here since we split on spaces)
    return ''.join(result)


class NormalizationEngine:
    """
    Rule-based stream name normalization engine.

    Loads rules from the database and applies them in priority order.
    Groups are processed in priority order (lower first), and within
    each group, rules are processed in priority order.
    """

    def __init__(self, db: Session):
        self.db = db
        self._rules_cache: Optional[list] = None
        self._groups_cache: Optional[list] = None

    def invalidate_cache(self):
        """Clear cached rules to force reload from database."""
        self._rules_cache = None
        self._groups_cache = None
        # Also clear tag group cache
        global _tag_group_cache
        _tag_group_cache.clear()

    def _load_tag_group(self, tag_group_id: int) -> list[tuple[str, bool]]:
        """
        Load tags from a tag group with caching.

        Args:
            tag_group_id: ID of the tag group to load

        Returns:
            List of (tag_value, case_sensitive) tuples for enabled tags
        """
        global _tag_group_cache

        if tag_group_id in _tag_group_cache:
            return _tag_group_cache[tag_group_id]

        # Load from database
        tags = (
            self.db.query(Tag)
            .filter(Tag.group_id == tag_group_id, Tag.enabled == True)
            .all()
        )

        # Convert superscripts in tag values (so tags with ᴿᴬᵂ match RAW)
        tag_list = [(convert_superscripts(tag.value), tag.case_sensitive) for tag in tags]
        _tag_group_cache[tag_group_id] = tag_list

        return tag_list

    def _load_rules(self) -> list[tuple[NormalizationRuleGroup, list[NormalizationRule]]]:
        """
        Load all enabled rules from database, organized by group.
        Returns list of (group, rules) tuples ordered by group priority.
        """
        if self._rules_cache is not None and self._groups_cache is not None:
            return list(zip(self._groups_cache, self._rules_cache))

        # Load enabled groups ordered by priority
        groups = (
            self.db.query(NormalizationRuleGroup)
            .filter(NormalizationRuleGroup.enabled == True)
            .order_by(NormalizationRuleGroup.priority)
            .all()
        )

        result = []
        all_groups = []
        all_rules = []

        for group in groups:
            # Load enabled rules for this group, ordered by priority
            rules = (
                self.db.query(NormalizationRule)
                .filter(
                    NormalizationRule.group_id == group.id,
                    NormalizationRule.enabled == True
                )
                .order_by(NormalizationRule.priority)
                .all()
            )
            result.append((group, rules))
            all_groups.append(group)
            all_rules.append(rules)

        self._groups_cache = all_groups
        self._rules_cache = all_rules

        return result

    def _match_single_condition(
        self,
        text: str,
        condition_type: str,
        pattern: str,
        case_sensitive: bool = False
    ) -> RuleMatch:
        """
        Check if text matches a single condition.
        Returns RuleMatch with match details.

        Applies the unified NormalizationPolicy (bd-eio04.1) to BOTH the
        input text and the pattern so NFC/NFD variants, invisible Cf
        code points, and Unicode superscripts collapse to canonical form
        before matching. This is the shared preprocessing entry point
        for test_rule / test_rules_batch; normalize() preprocesses once
        at the top of the loop and hands already-canonical text in.
        """
        policy = get_default_policy()
        text = policy.apply_to_text(text)
        pattern = policy.apply_to_text(pattern)

        # Prepare text for matching
        match_text = text if case_sensitive else text.lower()
        match_pattern = pattern if case_sensitive else pattern.lower()

        if condition_type == "always":
            return RuleMatch(matched=True, match_start=0, match_end=len(text))

        elif condition_type == "contains":
            idx = match_text.find(match_pattern)
            if idx >= 0:
                return RuleMatch(
                    matched=True,
                    match_start=idx,
                    match_end=idx + len(pattern)
                )
            return RuleMatch(matched=False)

        elif condition_type == "starts_with":
            if match_text.startswith(match_pattern):
                # Check that pattern is followed by separator (NOT end of string)
                # This prevents "ES" from matching "ESPN" - it should only match "ES: ..." or "ES | ..."
                # Also prevents matching if the pattern IS the entire string (nothing would remain)
                remaining = match_text[len(match_pattern):]
                if remaining and re.match(r'^[\s:\-|/]', remaining):
                    return RuleMatch(
                        matched=True,
                        match_start=0,
                        match_end=len(pattern)
                    )
            return RuleMatch(matched=False)

        elif condition_type == "ends_with":
            if match_text.endswith(match_pattern):
                # Check that pattern is preceded by separator (NOT start of string)
                # This prevents "HD" from matching "ADHD" - it should only match "... HD" or "...|HD"
                # Also prevents matching if the pattern IS the entire string (nothing would remain)
                prefix_len = len(text) - len(pattern)
                if prefix_len > 0 and re.search(r'[\s:\-|/]$', text[:prefix_len]):
                    return RuleMatch(
                        matched=True,
                        match_start=prefix_len,
                        match_end=len(text)
                    )
            return RuleMatch(matched=False)

        elif condition_type == "regex":
            # bd-eio04.14: migrated to safe_regex. User-supplied pattern;
            # on timeout / oversize / compile-error safe_regex.search
            # returns None and logs a [SAFE_REGEX] WARN (with pattern
            # sha256 + excerpt). Falling through to RuleMatch(matched=False)
            # preserves the pre-migration no-match arm, which is the
            # contract bd-eio04.1's NormalizationPolicy relies on when a
            # regex condition fails to evaluate.
            flags = 0 if case_sensitive else re.IGNORECASE
            match = safe_regex.search(pattern, text, flags=flags)
            if match:
                return RuleMatch(
                    matched=True,
                    match_start=match.start(),
                    match_end=match.end(),
                    groups=match.groups()
                )
            return RuleMatch(matched=False)

        else:
            logger.warning("[NORMALIZE] Unknown condition type: %s", condition_type)
            return RuleMatch(matched=False)

    def _match_tag_group(
        self,
        text: str,
        tag_group_id: int,
        position: str = "contains"
    ) -> RuleMatch:
        """
        Check if text matches any tag from a tag group.

        Args:
            text: Text to match against
            tag_group_id: ID of the tag group
            position: 'prefix', 'suffix', or 'contains' (default)

        Returns:
            RuleMatch with match details and matched_tag
        """
        tags = self._load_tag_group(tag_group_id)

        for tag_value, case_sensitive in tags:
            match_text = text if case_sensitive else text.lower()
            match_tag = tag_value if case_sensitive else tag_value.lower()

            if position == "prefix":
                # Match at start with separator check
                # Requires something after the tag (don't match if tag IS the entire string)
                if match_text.startswith(match_tag):
                    remaining = match_text[len(match_tag):]
                    if remaining and re.match(r'^[\s:\-|/]', remaining):
                        return RuleMatch(
                            matched=True,
                            match_start=0,
                            match_end=len(tag_value),
                            matched_tag=tag_value
                        )

            elif position == "suffix":
                # Match at end with separator check
                # Requires something before the tag (don't match if tag IS the entire string)
                if match_text.endswith(match_tag):
                    prefix_len = len(text) - len(tag_value)
                    if prefix_len > 0 and re.search(r'[\s:\-|/]$', text[:prefix_len]):
                        return RuleMatch(
                            matched=True,
                            match_start=prefix_len,
                            match_end=len(text),
                            matched_tag=tag_value
                        )

                # Also check for parenthesized version: (TAG)
                # Common pattern in stream names like "Channel Name (HD)" or "Movie (NA)"
                paren_tag = f"({match_tag})"
                if match_text.endswith(paren_tag):
                    prefix_len = len(text) - len(paren_tag)
                    # Parenthesized suffixes typically have a space before them
                    if prefix_len > 0 and text[prefix_len - 1] == ' ':
                        return RuleMatch(
                            matched=True,
                            match_start=prefix_len - 1,  # Include the space before
                            match_end=len(text),
                            matched_tag=tag_value
                        )

            else:  # contains
                # Use word-boundary matching so short tags like "WY" don't match
                # inside longer words like "Laramie" (which contains "LA")
                # Captures a full separator group on each side (e.g., " | WY | " not just " WY ")
                sep = r'[\s:\-|/]+'
                tag_pat = re.escape(match_tag)
                flags = 0 if case_sensitive else re.IGNORECASE
                # bd-eio04.14: tag-group contains patterns are built from
                # user tag values via re.escape (length-bounded in practice
                # but still user-controlled). Route through safe_regex so
                # a pathological tag value cannot trigger ReDoS inside the
                # normalization loop. Fallback contract for both calls: on
                # timeout safe_regex returns None -> fall through to the
                # non-match return at the bottom of the loop, tag group
                # simply "didn't apply" for this entry.
                # Try to match with separators on both sides first
                pattern = r'(' + sep + r')' + tag_pat + r'(?=' + sep + r'|$)'
                m = safe_regex.search(pattern, text, flags=flags)
                if m:
                    # Consume the leading separator group + tag, leave trailing for next segment
                    return RuleMatch(
                        matched=True,
                        match_start=m.start(),
                        match_end=m.end(),
                        matched_tag=tag_value
                    )
                # Try at start of string: tag followed by separator
                pattern = tag_pat + r'(' + sep + r')'
                m = safe_regex.match(pattern, text, flags=flags)
                if m:
                    return RuleMatch(
                        matched=True,
                        match_start=0,
                        match_end=m.end(),
                        matched_tag=tag_value
                    )

        return RuleMatch(matched=False)

    def _match_condition(self, text: str, rule: NormalizationRule) -> RuleMatch:
        """
        Check if text matches the rule's condition(s).
        Supports both legacy single conditions and compound conditions.
        Returns RuleMatch with match details.
        """
        # Check for compound conditions first
        conditions = rule.get_conditions()
        if conditions:
            return self._match_compound_conditions(text, conditions, rule.condition_logic)

        # Handle tag_group condition type
        if rule.condition_type == "tag_group":
            if rule.tag_group_id:
                return self._match_tag_group(
                    text,
                    rule.tag_group_id,
                    rule.tag_match_position or "contains"
                )
            return RuleMatch(matched=False)

        # Fall back to legacy single condition
        return self._match_single_condition(
            text,
            rule.condition_type or "always",
            rule.condition_value or "",
            rule.case_sensitive
        )

    def _match_compound_conditions(
        self,
        text: str,
        conditions: list,
        logic: str = "AND"
    ) -> RuleMatch:
        """
        Match text against multiple conditions with AND/OR logic.
        The first condition's match info is used for the action (primary condition).
        """
        if not conditions:
            return RuleMatch(matched=False)

        results = []
        primary_match = None  # Match info from first condition for action application

        for i, cond in enumerate(conditions):
            cond_type = cond.get("type", "always")
            cond_value = cond.get("value", "")
            cond_case_sensitive = cond.get("case_sensitive", False)
            cond_negate = cond.get("negate", False)

            match = self._match_single_condition(text, cond_type, cond_value, cond_case_sensitive)

            # Apply negation
            if cond_negate:
                matched = not match.matched
            else:
                matched = match.matched

            results.append(matched)

            # Store the first non-negated match as the primary match for action application
            if i == 0 and match.matched and not cond_negate:
                primary_match = match

        # Combine results based on logic
        if logic == "OR":
            final_matched = any(results)
        else:  # AND (default)
            final_matched = all(results)

        if final_matched:
            # Return primary match info if available, otherwise generic match
            if primary_match:
                return primary_match
            return RuleMatch(matched=True, match_start=0, match_end=len(text))

        return RuleMatch(matched=False)

    def _apply_action(self, text: str, rule: NormalizationRule, match: RuleMatch) -> str:
        """
        Apply the rule's action to transform the text.
        """
        action_type = rule.action_type
        action_value = rule.action_value or ""
        pattern = rule.condition_value or ""
        case_sensitive = rule.case_sensitive

        if action_type == "remove":
            # Remove the matched portion
            return text[:match.match_start] + text[match.match_end:]

        elif action_type == "replace":
            # Replace matched portion with action_value
            return text[:match.match_start] + action_value + text[match.match_end:]

        elif action_type == "regex_replace":
            # Use regex substitution
            if rule.condition_type != "regex":
                logger.warning("[NORMALIZE] regex_replace requires regex condition in rule %s", rule.id)
                return text
            # bd-eio04.14: migrated user-regex call to safe_regex.sub.
            # The inner re.sub on action_value (backreference rewrite) stays
            # on stdlib re — that pattern is a hardcoded module literal,
            # not user-supplied. Fallback contract: safe_regex.sub returns
            # text unchanged on timeout / oversize / compile error. The
            # safe_regex module emits its own [SAFE_REGEX] WARN log, so no
            # additional log here.
            flags = 0 if case_sensitive else re.IGNORECASE
            # Convert JS-style backreferences ($1, $2) to Python (\1, \2)
            py_replacement = re.sub(r'\$(\d+)', r'\\\1', action_value)
            return safe_regex.sub(pattern, py_replacement, text, flags=flags)

        elif action_type == "strip_prefix":
            # Remove pattern from start, including any following separator
            # Handles patterns like "US: " or "US | " or "US-"
            if match.match_start == 0:
                result = text[match.match_end:]
                # Also strip common separators that might follow
                result = re.sub(r'^[\s:\-|/]+', '', result)
                return result.strip()
            return text

        elif action_type == "strip_suffix":
            # Remove pattern from end, including any preceding separator
            # Handles patterns like " HD" or " - HD" or " | HD"
            if match.match_end == len(text) or match.match_end == len(text.rstrip()):
                result = text[:match.match_start]
                # Also strip common separators that might precede
                result = result.rstrip(' \t\n\r:-|/')
                return result.strip()
            return text

        elif action_type == "normalize_prefix":
            # Keep the prefix but standardize its format
            # e.g., "US:" -> "US | " or "US-" -> "US | "
            if match.match_start == 0:
                # Extract just the prefix (the matched content)
                prefix = text[match.match_start:match.match_end]
                # Remove any trailing separators from prefix
                prefix = prefix.rstrip(' \t\n\r:-|/')
                # Get the rest of the text
                rest = text[match.match_end:]
                rest = rest.lstrip(' \t\n\r:-|/')
                # Use action_value as the separator format, default to " | "
                separator = action_value if action_value else " | "
                return f"{prefix}{separator}{rest}"
            return text

        elif action_type == "capitalize":
            mode = action_value.lower() if action_value else "title"
            if mode == "upper":
                return text.upper()
            elif mode == "lower":
                return text.lower()
            elif mode == "sentence":
                return text[0].upper() + text[1:].lower() if text else text
            else:
                # Smart title case: preserve abbreviations and mixed-case,
                # title-case all-caps/all-lower words, keep small words lowercase
                words = text.split()
                return ' '.join(
                    _smart_title_word(w, i == 0) for i, w in enumerate(words)
                )

        else:
            logger.warning("[NORMALIZE] Unknown action type: %s", action_type)
            return text

    def _apply_else_action(self, text: str, rule: NormalizationRule) -> str:
        """
        Apply the rule's else action when the condition does NOT match.
        Only applies if else_action_type is set.

        Args:
            text: The current text
            rule: The rule with else action configuration

        Returns:
            Transformed text or original if no else action
        """
        if not rule.else_action_type:
            return text

        action_type = rule.else_action_type
        action_value = rule.else_action_value or ""

        if action_type == "remove":
            # Remove doesn't make sense without a specific match
            # In else context, this would clear the entire text - probably not intended
            logger.warning("[NORMALIZE] Rule %s: 'remove' as else_action has no effect (no match to remove)", rule.id)
            return text

        elif action_type == "replace":
            # Replace entire text with else_action_value
            return action_value

        elif action_type == "regex_replace":
            # For else, apply the regex pattern to the whole text
            if rule.condition_value:
                # bd-eio04.14: migrated user-regex call to safe_regex.sub.
                # Backreference-rewrite sub on action_value stays on stdlib
                # re (hardcoded literal pattern). Fallback contract: on
                # timeout / oversize / compile-error safe_regex.sub
                # returns text unchanged — matches pre-migration behavior
                # where re.error was caught and text was returned.
                flags = 0 if rule.case_sensitive else re.IGNORECASE
                # Convert JS-style backreferences ($1, $2) to Python (\1, \2)
                py_replacement = re.sub(r'\$(\d+)', r'\\\1', action_value)
                return safe_regex.sub(rule.condition_value, py_replacement, text, flags=flags)
            return text

        elif action_type == "strip_prefix":
            # Strip any leading separators and whitespace
            result = text.lstrip(' \t\n\r:-|/')
            return result.strip()

        elif action_type == "strip_suffix":
            # Strip any trailing separators and whitespace
            result = text.rstrip(' \t\n\r:-|/')
            return result.strip()

        elif action_type == "normalize_prefix":
            # No specific prefix matched, so can't normalize
            logger.warning("[NORMALIZE] Rule %s: 'normalize_prefix' as else_action has no effect (no match)", rule.id)
            return text

        elif action_type == "capitalize":
            mode = action_value.lower() if action_value else "title"
            if mode == "upper":
                return text.upper()
            elif mode == "lower":
                return text.lower()
            elif mode == "sentence":
                return text[0].upper() + text[1:].lower() if text else text
            else:
                # Smart title case: preserve abbreviations and mixed-case,
                # title-case all-caps/all-lower words, keep small words lowercase
                words = text.split()
                return ' '.join(
                    _smart_title_word(w, i == 0) for i, w in enumerate(words)
                )

        else:
            logger.warning("[NORMALIZE] Unknown else action type: %s", action_type)
            return text

    def normalize(self, name: str, group_ids: list[int] | None = None) -> NormalizationResult:
        """
        Apply enabled rules to normalize a stream name.

        Rules are applied in multiple passes until no more changes occur.
        This handles cases like "4K/UHD" (both quality tags) or "HD (NA)"
        where stripping one suffix reveals another that should also be stripped.

        Args:
            name: The stream name to normalize
            group_ids: Optional list of NormalizationRuleGroup IDs to apply.
                       None = all enabled groups (default behavior).

        Returns:
            NormalizationResult with original, normalized name, and applied rules.

        Unified NormalizationPolicy (bd-eio04.1)
        ----------------------------------------
        The Unicode preprocessing (NFC canonicalization, narrow Cf-code-point
        stripping, superscript-to-ASCII conversion for both letters AND
        numerics) is applied ONCE at the top of this pipeline via the
        process-wide NormalizationPolicy. The Test Rules preview path
        (`test_rule`, `test_rules_batch`) applies the same policy inside
        `_match_single_condition`. Both paths therefore preprocess input
        identically — that is the contract exercised by
        tests/unit/test_normalization_parity.py.

        The prior `preserve_superscripts` kwarg is removed. PO decision
        2026-04-22: numerics convert on every path; there is no
        carve-out for intentional marks like ESPN².
        """
        # Observability hook (bd-eio04.9) — stamp the start time here so
        # the decision log captures the full normalize() path including
        # policy preprocessing + rule loading. The call to
        # `record_normalization_decision` at the end is deliberately in
        # a try/finally so the hook never alters the return value when
        # an exception inside the engine would otherwise surface.
        _norm_started_at = time.perf_counter()
        result = NormalizationResult(
            original=name,
            normalized=name,
            rules_applied=[],
            transformations=[]
        )

        current = name.strip()

        # Apply the unified NormalizationPolicy once up front. Under the
        # default (ECM_NORMALIZATION_UNIFIED_POLICY=true) this does
        # NFC + Cf-strip + superscript conversion; under the legacy
        # fallback it applies superscript conversion only.
        current = get_default_policy().apply_to_text(current)

        grouped_rules = self._load_rules()

        # Filter to specific groups if requested (per-rule normalization)
        if group_ids is not None:
            all_count = len(grouped_rules)
            allowed = set(group_ids)
            grouped_rules = [(g, r) for g, r in grouped_rules if g.id in allowed]
            logger.debug("[NORMALIZE] Filtered to %d/%d groups (ids=%s) for '%s'",
                        len(grouped_rules), all_count, group_ids, name)

        # Multi-pass normalization: keep applying rules until no changes occur
        max_passes = 10  # Safety limit to prevent infinite loops
        for pass_num in range(max_passes):
            before_pass = current

            # Apply database rules
            current = self._apply_rules_single_pass(current, grouped_rules, result)

            # Apply legacy custom_normalization_tags from settings
            current = self._apply_legacy_custom_tags(current, result)

            # Normalize whitespace between passes
            current = re.sub(r'\s+', ' ', current).strip()

            # If nothing changed this pass, we're done
            if current == before_pass:
                break

            logger.debug("[NORMALIZE] Normalization pass %s: '%s' -> '%s'", pass_num + 1, before_pass, current)

        result.normalized = current

        # Observability hook (bd-eio04.9): record a structured decision
        # for the metrics + sampled INFO log. Import is local so the
        # module stays importable in environments where observability
        # is not installed (tests that stub out logging, etc.). The
        # hook is idempotent and exception-safe — a failure here must
        # never change the normalize() return value.
        try:
            from observability import record_normalization_decision

            elapsed = time.perf_counter() - _norm_started_at
            policy = get_default_policy()
            # Best-effort "coarse rule category" for the metric label:
            # report the action_type of the first transformation when
            # present, else None so the hook skips the rule_matches
            # counter. Per-rule-id detail lives in the INFO log.
            first_action: Optional[str] = None
            if result.transformations:
                first_rule_id = result.transformations[0][0]
                # The rule_id may be the sentinel "legacy_tag" for
                # settings-based tags; pass through.
                if first_rule_id == "legacy_tag":
                    first_action = "legacy_tag"
                else:
                    # Resolve rule_id -> action_type from the cached rules.
                    for _, rules in grouped_rules:
                        for rule in rules:
                            if rule.id == first_rule_id:
                                first_action = rule.action_type
                                break
                        if first_action is not None:
                            break

            record_normalization_decision(
                input_text=name,
                output_text=current,
                matched_rule_ids=list(result.rules_applied),
                applied=bool(result.rules_applied) or (name != current),
                policy_version=_current_policy_version(policy),
                duration_seconds=elapsed,
                rule_category=first_action,
                extra={"source": "normalize"},
            )
        except Exception:  # pragma: no cover — observability must not break the caller
            logger.debug("[NORMALIZE] decision-log hook failed", exc_info=True)

        return result

    def _apply_rules_single_pass(
        self,
        text: str,
        grouped_rules: list,
        result: NormalizationResult
    ) -> str:
        """Apply all database rules once through the text."""
        current = text

        for group, rules in grouped_rules:
            for rule in rules:
                match = self._match_condition(current, rule)

                if match.matched:
                    before = current
                    current = self._apply_action(current, rule, match)

                    # Track what changed
                    if before != current:
                        result.rules_applied.append(rule.id)
                        result.transformations.append((rule.id, before, current))

                        logger.debug(
                            "[NORMALIZE] Rule %s (%s, group '%s'): '%s' -> '%s'",
                            rule.id, rule.name, group.name, before, current
                        )

                    # Stop processing if rule says so
                    if rule.stop_processing:
                        break

                elif rule.else_action_type:
                    # Condition didn't match but rule has an else action
                    before = current
                    current = self._apply_else_action(current, rule)

                    # Track what changed
                    if before != current:
                        result.rules_applied.append(rule.id)
                        result.transformations.append((rule.id, before, current))

                        logger.debug(
                            "[NORMALIZE] Rule %s (%s) [ELSE]: '%s' -> '%s'",
                            rule.id, rule.name, before, current
                        )

                    # Stop processing applies to else branch too
                    if rule.stop_processing:
                        break

        return current

    def _apply_legacy_custom_tags(self, text: str, result: NormalizationResult) -> str:
        """
        Apply custom_normalization_tags from settings.json for backward compatibility.
        These are user-defined tags that predate the database-based tag system.
        """
        try:
            from config import get_settings
            settings = get_settings()
            custom_tags = settings.custom_normalization_tags or []
        except Exception:
            return text

        current = text
        for tag_config in custom_tags:
            tag_value = tag_config.get("value", "")
            mode = tag_config.get("mode", "both")  # prefix, suffix, or both

            if not tag_value:
                continue

            before = current

            # Handle suffix mode
            if mode in ("suffix", "both"):
                # Check for plain suffix with separator
                lower_current = current.lower()
                lower_tag = tag_value.lower()

                # Check if ends with tag (with separator before it)
                if lower_current.endswith(lower_tag):
                    prefix_len = len(current) - len(tag_value)
                    if prefix_len > 0 and current[prefix_len - 1] in ' :-|/':
                        current = current[:prefix_len].rstrip(' :-|/')
                        continue

                # Check for parenthesized suffix: (TAG)
                paren_tag = f"({tag_value})"
                lower_paren = paren_tag.lower()
                if lower_current.endswith(lower_paren):
                    prefix_len = len(current) - len(paren_tag)
                    if prefix_len > 0:
                        current = current[:prefix_len].rstrip()
                        continue

            # Handle prefix mode
            if mode in ("prefix", "both"):
                lower_current = current.lower()
                lower_tag = tag_value.lower()

                if lower_current.startswith(lower_tag):
                    remaining = current[len(tag_value):]
                    if remaining and remaining[0] in ' :-|/':
                        current = remaining.lstrip(' :-|/')

            # Track if changed
            if before != current:
                result.transformations.append(("legacy_tag", before, current))
                logger.debug("[NORMALIZE] Legacy tag '%s': '%s' -> '%s'", str(tag_value).replace('\n', ''), str(before).replace('\n', ''), str(current).replace('\n', ''))

        return current

    # =================================================================
    # Core Name Extraction (for merge_streams fallback matching)
    # =================================================================

    _tag_group_id_cache: dict[str, Optional[int]] = {}

    def _get_tag_group_id_by_name(self, name: str) -> Optional[int]:
        """Get a TagGroup's ID by its display name, with caching."""
        if name in self._tag_group_id_cache:
            return self._tag_group_id_cache[name]

        group = self.db.query(TagGroup).filter(TagGroup.name == name).first()
        gid = group.id if group else None
        self._tag_group_id_cache[name] = gid
        return gid

    def extract_core_name(self, name: str) -> str:
        """
        Strip country prefix and quality suffix from a name using tag groups
        DIRECTLY — does NOT depend on normalization rules being enabled.

        Used by merge_streams core-name fallback when normalize_names=true.

        Returns the core name (never empty; falls back to input).
        """
        current = name.strip()
        if not current:
            return current

        # Convert Unicode superscripts (ᴴᴰ -> HD, etc.)
        current = convert_superscripts(current)

        # Strip leading channel-number prefix: "107 | Name", "107 - Name"
        current = re.sub(r'^\d+\s*[|:\-]\s*', '', current).strip()
        if not current:
            return name.strip()

        country_id = self._get_tag_group_id_by_name("Country Tags")
        quality_id = self._get_tag_group_id_by_name("Quality Tags")

        # Multi-pass: keep stripping until stable (handles stacked tags)
        for _ in range(5):
            before = current

            # Strip country prefix
            if country_id:
                match = self._match_tag_group(current, country_id, "prefix")
                if match.matched and match.match_start == 0:
                    result = current[match.match_end:]
                    result = re.sub(r'^[\s:\-|/]+', '', result).strip()
                    if result:
                        current = result

            # Strip quality suffix
            if quality_id:
                match = self._match_tag_group(current, quality_id, "suffix")
                if match.matched:
                    if match.match_end == len(current) or match.match_end == len(current.rstrip()):
                        result = current[:match.match_start]
                        result = re.sub(r'[\s:|\-/]+$', '',result).strip()
                        if result:
                            current = result

            # Normalize whitespace between passes
            current = re.sub(r'\s+', ' ', current).strip()

            if current == before:
                break

        return current if current else name.strip()

    # =================================================================
    # Call Sign Extraction (for merge_streams local affiliate matching)
    # =================================================================

    # FCC call signs: W/K + 2-3 uppercase letters
    # Parenthesized form: "(WFTS)", "(KABC)"
    # Bare form at end of name: "ABC 28 Tampa WFTS"
    _CALLSIGN_FALSE_POSITIVES = frozenset({"WWE", "WEST", "KIDZ", "KIDS", "WNBA", "WPT"})
    _CALLSIGN_PAREN_RE = re.compile(r'\(([WK][A-Z]{2,3})\)')
    _CALLSIGN_BARE_RE = re.compile(r'\b([WK][A-Z]{2,3})\b')

    # Broadcast networks — bare call sign extraction requires one of these
    # (or a channel number) to be present, preventing false positives on
    # random English words like WAVE, KIDS, WAR
    _BROADCAST_NETWORKS = frozenset({
        "ABC", "CBS", "NBC", "FOX", "PBS", "CW", "MY", "ION",
        "UPN", "WB", "MNT", "UNIVISION", "TELEMUNDO",
    })

    # Prefixes that disqualify a name from call sign extraction —
    # these are content categories, not local station streams/channels
    _CALLSIGN_EXCLUDED_PREFIXES = ("TEAMS:",)

    @staticmethod
    def extract_call_sign(name: str) -> Optional[str]:
        """
        Extract an FCC call sign (W/K + 2-3 uppercase letters) from a name.

        Prefers parenthesized call signs like "(WFTS)" over bare ones.
        For bare form, requires a broadcast network name or channel number
        nearby to prevent false positives on common words.
        Returns None if no call sign found or if it's a known false positive.

        Used by merge_streams call-sign fallback when normalize_names=true.
        """
        if not name:
            return None

        upper = name.upper()

        # Skip names with disqualifying prefixes (e.g., "Teams: CBS Texans (KENS)")
        # Strip leading channel numbers like "2072 | " before checking prefix
        stripped = re.sub(r'^\d+\s*\|\s*', '', upper)
        if any(stripped.startswith(p) for p in NormalizationEngine._CALLSIGN_EXCLUDED_PREFIXES):
            return None

        # Prefer parenthesized: "(WFTS)", "(KABC)"
        m = NormalizationEngine._CALLSIGN_PAREN_RE.search(upper)
        if m:
            cs = m.group(1)
            if cs not in NormalizationEngine._CALLSIGN_FALSE_POSITIVES:
                return cs

        # Bare form: only attempt if name contains a broadcast network or
        # channel number — this prevents matching random words in names
        # like "(MC Radio) New Wave" or "DOCUBOX: MILITARY AND WAR"
        has_network = any(
            safe_regex.search(r'\b' + net + r'\b', upper)
            for net in NormalizationEngine._BROADCAST_NETWORKS
        )
        has_channel_num = bool(re.search(r'\b\d{1,2}\b', upper))

        if not has_network and not has_channel_num:
            return None

        # Take the LAST match — call signs come after city/state names
        # e.g., "CBS: TX WACO KWTX" → want KWTX not WACO
        last_cs = None
        for m in NormalizationEngine._CALLSIGN_BARE_RE.finditer(upper):
            cs = m.group(1)
            if cs not in NormalizationEngine._CALLSIGN_FALSE_POSITIVES:
                last_cs = cs
        return last_cs

    def test_rule(
        self,
        text: str,
        condition_type: str,
        condition_value: str,
        case_sensitive: bool,
        action_type: str,
        action_value: str = "",
        conditions: Optional[list] = None,
        condition_logic: str = "AND",
        tag_group_id: Optional[int] = None,
        tag_match_position: str = "contains",
        else_action_type: Optional[str] = None,
        else_action_value: Optional[str] = None
    ) -> dict:
        """
        Test a rule configuration against sample text without saving.

        Args:
            text: Sample text to test
            condition_type: Rule condition type (legacy single condition)
            condition_value: Pattern to match (legacy single condition)
            case_sensitive: Case sensitivity flag (legacy single condition)
            action_type: Action to apply
            action_value: Replacement value for replace actions
            conditions: Compound conditions list (takes precedence if set)
            condition_logic: "AND" or "OR" for combining conditions
            tag_group_id: Tag group ID for tag_group condition type
            tag_match_position: Position for tag matching ('prefix', 'suffix', 'contains')
            else_action_type: Action to apply when condition doesn't match
            else_action_value: Value for else action

        Returns:
            Dict with matched, before, after, match_details
        """
        import json

        # Create a temporary rule object for testing
        rule = NormalizationRule(
            id=0,
            group_id=0,
            name="Test Rule",
            condition_type=condition_type,
            condition_value=condition_value,
            case_sensitive=case_sensitive,
            tag_group_id=tag_group_id,
            tag_match_position=tag_match_position,
            action_type=action_type,
            action_value=action_value,
            else_action_type=else_action_type,
            else_action_value=else_action_value,
            conditions=json.dumps(conditions) if conditions else None,
            condition_logic=condition_logic
        )

        # bd-eio04.1: apply the unified NormalizationPolicy to the input
        # before matching + action so the Test Rules preview path
        # reaches byte-identical output with normalize(). Prior behavior
        # preprocessed inside _match_condition for matching but applied
        # actions to the raw input — that split was the root cause of
        # GH #104's "Test shows HD but auto-create shows ᴴᴰ" divergence.
        preprocessed_text = get_default_policy().apply_to_text(text)

        match = self._match_condition(preprocessed_text, rule)

        result = {
            "matched": match.matched,
            "before": text,
            "after": preprocessed_text,
            "match_start": match.match_start if match.matched else None,
            "match_end": match.match_end if match.matched else None,
            "matched_tag": match.matched_tag if match.matched_tag else None,
            "else_applied": False,
        }

        if match.matched:
            result["after"] = self._apply_action(preprocessed_text, rule, match)
            # Final cleanup
            result["after"] = re.sub(r'\s+', ' ', result["after"]).strip()
        elif else_action_type:
            # Condition didn't match, apply else action
            result["after"] = self._apply_else_action(preprocessed_text, rule)
            result["after"] = re.sub(r'\s+', ' ', result["after"]).strip()
            result["else_applied"] = True
        else:
            # Also strip/normalize whitespace for consistency with
            # normalize()'s per-pass cleanup.
            result["after"] = re.sub(r'\s+', ' ', preprocessed_text).strip()

        # Observability hook (bd-eio04.9): emit a decision log for the
        # Test Rules preview path too, tagged source=test_rule so
        # SRE can compare the two paths in kibana/loki and catch a
        # divergence without waiting for the nightly canary.
        try:
            from observability import record_normalization_decision

            policy = get_default_policy()
            applied = bool(match.matched) or result.get("else_applied", False)
            matched_ids: list = []
            # test_rule uses a synthetic rule with id=0; surface that
            # so the log shows the preview path ran a concrete rule.
            if applied:
                matched_ids = [0]
            record_normalization_decision(
                input_text=text,
                output_text=result["after"],
                matched_rule_ids=matched_ids,
                applied=applied,
                policy_version=_current_policy_version(policy),
                rule_category=action_type,
                extra={"source": "test_rule"},
            )
        except Exception:  # pragma: no cover — observability must not break the caller
            logger.debug("[NORMALIZE] decision-log hook failed in test_rule", exc_info=True)

        return result

    def test_rules_batch(self, texts: list[str]) -> list[NormalizationResult]:
        """
        Test all enabled rules against multiple sample texts.

        Args:
            texts: List of sample texts to normalize

        Returns:
            List of NormalizationResult objects
        """
        return [self.normalize(text) for text in texts]

    def get_all_rules(self) -> list[dict]:
        """
        Get all rules organized by group for display.

        Returns:
            List of group dicts with their rules
        """
        groups = (
            self.db.query(NormalizationRuleGroup)
            .order_by(NormalizationRuleGroup.priority)
            .all()
        )

        result = []
        for group in groups:
            rules = (
                self.db.query(NormalizationRule)
                .filter(NormalizationRule.group_id == group.id)
                .order_by(NormalizationRule.priority)
                .all()
            )
            result.append({
                **group.to_dict(),
                "rules": [rule.to_dict() for rule in rules]
            })

        return result


def get_normalization_engine(db: Session) -> NormalizationEngine:
    """Factory function to get a NormalizationEngine instance."""
    return NormalizationEngine(db)
