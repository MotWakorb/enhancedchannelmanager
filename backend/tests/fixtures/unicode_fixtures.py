"""
Shared Unicode edge-case fixture bank for normalization tests (bd-eio04.3).

This module is the single source of truth for Unicode channel-name test inputs.
Every normalization test that invents its own Unicode strings should instead
import from here so bug-reproducer inputs and edge cases stay indexed and
reusable.

Taxonomy (`NormalizationFixture.category`)
-|-
letter_sup   | Unicode letter superscripts (e.g. `ᴴᴰ`, `ᶠᴴᴰ`, `ᴿᴬᵂ`). Must
             | be stripped/converted to ASCII letters by normalize().
numeric_sup  | Unicode numeric superscripts (e.g. `²`, `⁶⁰`). Converted to
             | ASCII digits (post-bd-yui1k: letters strip, numerics convert).
mixed        | Strings containing both letter and numeric superscripts, or
             | letter superscripts combined with other edge cases.
nfc_nfd      | Canonical-composition pairs. Fixtures use NFD-decomposed input
             | that should collapse to NFC after normalization. Currently
             | xfailed pending bd-eio04.4 NFC wiring.
zero_width   | Invisible format characters: ZWJ (U+200D), ZWSP (U+200B),
             | ZWNJ (U+200C), BOM (U+FEFF). Must be stripped.
combining    | Standalone combining diacritics without base characters, or
             | combining sequences that should normalize.
rtl          | Right-to-left markers (U+200F, U+202E). Scope intentionally
             | narrow per grooming — we preserve RTL marks (Cf stripping
             | scope is limited to ZW/BOM).

Adding new fixtures:
1. Pick the category that matches the bug or edge case you're capturing.
2. Give it a stable, descriptive `name` — prefer `case_<origin>_<what>`.
3. Set `origin` to the bead ID (`bd-xxxxx`), GitHub issue (`issue104`), or
   `synthetic` for hand-crafted coverage cases.
4. Add to the category list AND to ALL_FIXTURES.
5. If the expected output depends on a not-yet-landed feature, mark the
   downstream test with `pytest.mark.xfail(reason=...)` — don't skip the
   fixture here.

Homoglyph fixtures are intentionally out of scope (PO decision 5b,
2026-04-22 grooming). Track confusables/skeleton work in bd-sc5s4.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NormalizationFixture:
    """A single Unicode normalization test case.

    Attributes:
        name: Stable, unique identifier (e.g. `case_issue104_espn_hd`).
              Used as parametrize `id` so failures are greppable.
        input: Raw channel-name string as it would arrive from an M3U.
        expected_normalized: The string after normalize() +
              convert_superscripts() + (eventually) NFC canonicalization.
              For fixtures that depend on unlanded features, downstream tests
              must mark themselves xfail — the expected value here is the
              target end-state.
        origin: Where this case came from. Use a bead ID (`bd-yui1k`), a
              GitHub issue tag (`issue104`), or `synthetic`.
        category: One of LETTER_SUP / NUMERIC_SUP / MIXED / NFC_NFD /
              ZERO_WIDTH / COMBINING / RTL — see module docstring.
        notes: Free-form context for humans reading the fixture bank.
    """

    name: str
    input: str
    expected_normalized: str
    origin: str
    category: str
    notes: str = ""


# -----------------------------------------------------------------------------
# Letter superscripts — ᴬ-ᶻ uppercase and lowercase, strip/convert to ASCII.
# -----------------------------------------------------------------------------

LETTER_SUPERSCRIPT_FIXTURES: list[NormalizationFixture] = [
    NormalizationFixture(
        name="case_issue104_fox_sports_2",
        input="|US| Fox Sports 2 ᴴᴰ US",  # |US| Fox Sports 2 ᴴᴰ US
        expected_normalized="|US| Fox Sports 2 HD US",
        origin="issue104",
        category="letter_sup",
        notes=(
            "Second reporter on GH #104. Letter superscripts ᴴᴰ must convert "
            "to ASCII 'HD' regardless of position in the string."
        ),
    ),
    NormalizationFixture(
        name="case_bd_yj5yi_reorder",
        input="FR: Canal+ ᶠᴴᴰ",  # FR: Canal+ ᶠᴴᴰ
        expected_normalized="FR: Canal+ FHD",
        origin="bd-yj5yi",
        category="letter_sup",
        notes=(
            "Lower-case 'f' superscript (ᶠ, U+1DA0) combined with uppercase "
            "HD superscripts. Regression source for reorder bug — keep here "
            "so any future Unicode work doesn't re-break it."
        ),
    ),
    NormalizationFixture(
        name="case_mid_string_superscript",
        input="BBC ᴴᴰ News",  # BBC ᴴᴰ News
        expected_normalized="BBC HD News",
        origin="synthetic",
        category="letter_sup",
        notes=(
            "Superscripts in the middle of the name, not just at the edges. "
            "Proves conversion is position-independent."
        ),
    ),
]


# -----------------------------------------------------------------------------
# Numeric superscripts — ⁰¹²³⁴⁵⁶⁷⁸⁹, convert to ASCII digits.
# Post-bd-yui1k: numerics were given a distinct preserve path but the
# default normalization path still converts them to ASCII digits.
# -----------------------------------------------------------------------------

NUMERIC_SUPERSCRIPT_FIXTURES: list[NormalizationFixture] = [
    NormalizationFixture(
        name="case_bd_yui1k_numeric_strip",
        input="ESPN ²",  # ESPN ²
        expected_normalized="ESPN 2",
        origin="bd-yui1k",
        category="numeric_sup",
        notes=(
            "Post-bd-yui1k semantics: numeric superscripts convert to ASCII "
            "digits on the default normalization path (letters strip, "
            "numerics convert). The preserve_superscripts=True flag is the "
            "only way to keep the ² glyph."
        ),
    ),
    NormalizationFixture(
        name="case_numeric_superscript_framerate",
        input="Sports ⁶⁰fps UHD",  # Sports ⁶⁰fps UHD
        expected_normalized="Sports 60fps UHD",
        origin="synthetic",
        category="numeric_sup",
        notes=(
            "Framerate patterns are a common real-world shape — ⁶⁰fps, ¹²⁰fps. "
            "Digits should convert so downstream tag matching sees 'fps'."
        ),
    ),
]


# -----------------------------------------------------------------------------
# Mixed — letter + numeric superscripts, or superscripts + other edge cases.
# -----------------------------------------------------------------------------

MIXED_FIXTURES: list[NormalizationFixture] = [
    NormalizationFixture(
        name="case_issue104_espn_hd",
        input="US: ESPN ᴴᴰ ⁶⁰fps",  # US: ESPN ᴴᴰ ⁶⁰fps
        expected_normalized="US: ESPN HD 60fps",
        origin="issue104",
        category="mixed",
        notes=(
            "Real bug from GH #104: both letter and numeric superscripts in "
            "one string. The bug report showed Settings→Normalization Test "
            "handling this but auto-creation not — fixture captures the "
            "exact reporter input."
        ),
    ),
    NormalizationFixture(
        name="case_only_superscripts",
        input="ᴴᴰ⁶⁰",  # ᴴᴰ⁶⁰
        expected_normalized="HD60",
        origin="synthetic",
        category="mixed",
        notes=(
            "Edge case: the entire string is superscripts with no ASCII at "
            "all. Confirms conversion doesn't require ASCII context."
        ),
    ),
]


# -----------------------------------------------------------------------------
# NFC vs NFD — canonical composition.
# Currently xfailed downstream pending bd-eio04.4 NFC wiring. Do not skip
# here — the fixture stays in the bank so .4 can flip the xfail off.
# -----------------------------------------------------------------------------

# NFD form: 'e' + combining acute accent (U+0301). Visually "é" but two codepoints.
_NFD_CAFE = "Café Sports"
# NFC form: pre-composed "é" (U+00E9). Visually identical, one codepoint.
_NFC_CAFE = "Café Sports"

NFC_NFD_FIXTURES: list[NormalizationFixture] = [
    NormalizationFixture(
        name="case_nfd_decomposition_cafe",
        input=_NFD_CAFE,
        expected_normalized=_NFC_CAFE,
        origin="synthetic",
        category="nfc_nfd",
        notes=(
            "NFD-decomposed 'Café Sports' (e + U+0301) should canonicalize "
            "to NFC pre-composed form. Blocks on bd-eio04.4 — downstream "
            "tests must mark pytest.mark.xfail(reason='pending bd-eio04.4 "
            "NFC') until NFC canonicalization is wired into normalize()."
        ),
    ),
]


# -----------------------------------------------------------------------------
# Zero-width / format characters — must be stripped.
# -----------------------------------------------------------------------------

ZERO_WIDTH_FIXTURES: list[NormalizationFixture] = [
    NormalizationFixture(
        name="case_zero_width_joiner_injection",
        input="ESPN‍ HD",  # ZWJ between ESPN and space
        expected_normalized="ESPN HD",
        origin="synthetic",
        category="zero_width",
        notes=(
            "U+200D (ZWJ) injected between tokens. Common copy-paste "
            "artifact that breaks exact-match tag lookups."
        ),
    ),
    NormalizationFixture(
        name="case_zero_width_space_suffix",
        input="Fox News​",  # trailing ZWSP
        expected_normalized="Fox News",
        origin="synthetic",
        category="zero_width",
        notes=(
            "U+200B (ZWSP) as a trailing character. Invisible but makes "
            "'Fox News' != 'Fox News' for string equality."
        ),
    ),
    NormalizationFixture(
        name="case_bom_prefix",
        input="﻿Sky Sports",  # BOM prefix
        expected_normalized="Sky Sports",
        origin="synthetic",
        category="zero_width",
        notes=(
            "U+FEFF (BOM) at string start — classic leaked-from-UTF-8-file "
            "artifact. Must strip."
        ),
    ),
]


# -----------------------------------------------------------------------------
# Combining diacritics — currently empty set; NFD cases cover the main case
# via NFC_NFD_FIXTURES. Reserved for future fixtures that isolate combining
# behavior separately from full NFC canonicalization.
# -----------------------------------------------------------------------------

COMBINING_FIXTURES: list[NormalizationFixture] = []


# -----------------------------------------------------------------------------
# RTL markers — keep category for future expansion but only one fixture here.
# Per grooming: Cf stripping scope is narrow. RTL marks like U+200F PRESERVE
# as-is (unlike ZWJ/ZWSP/BOM). Single fixture documents that boundary.
# -----------------------------------------------------------------------------

RTL_FIXTURES: list[NormalizationFixture] = [
    NormalizationFixture(
        name="case_rtl_mark_preserved",
        input="Al Jazeera ‏Arabic",  # U+200F right-to-left mark
        expected_normalized="Al Jazeera ‏Arabic",
        origin="synthetic",
        category="rtl",
        notes=(
            "U+200F (RLM) is preserved — Cf stripping is intentionally "
            "narrow. Only ZWJ/ZWSP/ZWNJ/BOM strip; bidi marks stay. "
            "If this behavior changes, update the fixture together with "
            "the implementation."
        ),
    ),
]


# -----------------------------------------------------------------------------
# Boundary cases — empty string, etc. Not category-specific.
# -----------------------------------------------------------------------------

_BOUNDARY_FIXTURES: list[NormalizationFixture] = [
    NormalizationFixture(
        name="case_empty_string",
        input="",
        expected_normalized="",
        origin="synthetic",
        category="letter_sup",  # arbitrary; boundary case applies everywhere
        notes=(
            "Empty-string boundary. Categorized as letter_sup for routing — "
            "every normalization path must handle '' without raising."
        ),
    ),
]


# -----------------------------------------------------------------------------
# Flat list for bulk tests (parity sweeps, round-trip checks, etc.).
# Ordering is stable; new additions go at the end of their category list.
# -----------------------------------------------------------------------------

ALL_FIXTURES: list[NormalizationFixture] = [
    *LETTER_SUPERSCRIPT_FIXTURES,
    *NUMERIC_SUPERSCRIPT_FIXTURES,
    *MIXED_FIXTURES,
    *NFC_NFD_FIXTURES,
    *ZERO_WIDTH_FIXTURES,
    *COMBINING_FIXTURES,
    *RTL_FIXTURES,
    *_BOUNDARY_FIXTURES,
]


__all__ = [
    "NormalizationFixture",
    "LETTER_SUPERSCRIPT_FIXTURES",
    "NUMERIC_SUPERSCRIPT_FIXTURES",
    "MIXED_FIXTURES",
    "NFC_NFD_FIXTURES",
    "ZERO_WIDTH_FIXTURES",
    "COMBINING_FIXTURES",
    "RTL_FIXTURES",
    "ALL_FIXTURES",
]
