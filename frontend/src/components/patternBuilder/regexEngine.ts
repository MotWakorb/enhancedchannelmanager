/**
 * Pure functions for regex generation and validation in the Pattern Builder.
 *
 * Core algorithm:
 * 1. Sort annotations by position
 * 2. For literal gaps: escape regex metacharacters, collapse whitespace to \s+
 * 3. For annotations: emit (?<variableName>typeRegex)
 * 4. Split into title/time/date patterns based on variable name routing
 * 5. Validate generated regex against all examples
 *
 * Supports nested annotations (wrapper groups that contain inner groups),
 * generating nested capture groups like (?<event>(?:(?<team1>.+?)\s+vs\s+(?<team2>.+?))|(?:.+?)).
 * Wrapper groups use alternation so inner annotations are optional — the engine tries
 * the detailed branch first and falls back to plain .+? if it doesn't match.
 */

import type { Annotation, Example, ValidationResult, PatternTarget, VariableType } from './types';
import { VARIABLE_TYPE_REGEX, TIME_VARIABLES, DATE_VARIABLES, NAME_TYPE_HINTS } from './types';

/** Escape regex metacharacters in a literal string. */
function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** Convert a literal text gap to a regex fragment (escape + collapse whitespace). */
function literalToRegex(s: string): string {
  if (!s) return '';
  // Escape metacharacters, then replace runs of whitespace with \s+
  return escapeRegex(s).replace(/\s+/g, '\\s+');
}

/** Determine which pattern a variable routes to. */
export function getPatternTarget(variableName: string): PatternTarget {
  if (TIME_VARIABLES.has(variableName)) return 'time';
  if (DATE_VARIABLES.has(variableName)) return 'date';
  return 'title';
}

/** Get the regex fragment for a leaf annotation. */
function annotationRegex(ann: Annotation): string {
  if (ann.variableType === 'custom' && ann.customRegex) {
    return `(?<${ann.variableName}>${ann.customRegex})`;
  }
  // 'time' and 'date' types already contain their own named groups —
  // return the raw fragment without wrapping in another named group.
  if (ann.variableType === 'time') {
    return VARIABLE_TYPE_REGEX.time;
  }
  if (ann.variableType === 'date') {
    return VARIABLE_TYPE_REGEX.date;
  }
  const fragment = VARIABLE_TYPE_REGEX[ann.variableType];
  return `(?<${ann.variableName}>${fragment})`;
}

/** Check if annotation A strictly contains annotation B. */
function strictlyContains(a: Annotation, b: Annotation): boolean {
  return a.start <= b.start && a.end >= b.end && (a.start < b.start || a.end > b.end);
}

/** Check if an annotation is a wrapper (contains at least one other annotation). */
export function isWrapperAnnotation(ann: Annotation, all: Annotation[]): boolean {
  return all.some(b => b !== ann && strictlyContains(ann, b));
}

/**
 * Recursively build regex content for a range, handling nested annotations.
 * Produces the inner content of a wrapper group (without the outer named group).
 */
function buildNestedContent(
  text: string,
  allAnnotations: Annotation[],
  rangeStart: number,
  rangeEnd: number,
  excludeAnn: Annotation,
): string {
  // Get annotations inside this range, excluding the wrapper itself
  const candidates = allAnnotations.filter(a =>
    a !== excludeAnn &&
    a.start >= rangeStart && a.end <= rangeEnd
  );

  // Find top-level among candidates (not strictly contained by another candidate)
  const topLevel = candidates.filter(a =>
    !candidates.some(b => b !== a && strictlyContains(b, a))
  );
  const sorted = [...topLevel].sort((a, b) => a.start - b.start);

  const parts: string[] = [];
  let cursor = rangeStart;

  for (const ann of sorted) {
    if (ann.start > cursor) {
      parts.push(literalToRegex(text.slice(cursor, ann.start)));
    }

    const children = candidates.filter(a =>
      a !== ann && strictlyContains(ann, a)
    );

    if (children.length > 0) {
      // Nested wrapper: recurse
      const innerRegex = buildNestedContent(text, allAnnotations, ann.start, ann.end, ann);
      parts.push(`(?<${ann.variableName}>(?:${innerRegex})|(?:.+?))`);
    } else {
      parts.push(annotationRegex(ann));
    }

    cursor = ann.end;
  }

  if (cursor < rangeEnd) {
    parts.push(literalToRegex(text.slice(cursor, rangeEnd)));
  }

  return parts.join('');
}

/**
 * Result of generating patterns from annotations.
 */
export interface GeneratedPatterns {
  titlePattern: string;
  timePattern: string;
  datePattern: string;
  /** All segments in original text order — used for validation. */
  combinedPattern: string;
}

/**
 * Generate regex patterns from annotations on an example text.
 *
 * The algorithm builds a single annotated regex from the example text,
 * then splits it into title/time/date patterns by detecting which variables
 * belong to which pattern target.
 *
 * Supports nested annotations: wrapper annotations that fully contain inner
 * annotations generate nested capture groups.
 */
export function annotationsToRegex(text: string, annotations: Annotation[]): GeneratedPatterns {
  if (!annotations.length) {
    return { titlePattern: '', timePattern: '', datePattern: '', combinedPattern: '' };
  }

  // Find top-level annotations (not strictly contained by any other)
  const topLevel = annotations.filter(a =>
    !annotations.some(b => b !== a && strictlyContains(b, a))
  );
  const sorted = [...topLevel].sort((a, b) => a.start - b.start);

  // Build segments from top-level annotations
  interface Segment {
    type: 'literal' | 'annotation';
    text: string;
    annotation?: Annotation;
    target: PatternTarget;
    /** Pre-built regex for this segment (handles nesting). */
    regex: string;
  }

  const segments: Segment[] = [];
  let cursor = 0;

  for (const ann of sorted) {
    // Add literal gap before this annotation
    if (ann.start > cursor) {
      const gapText = text.slice(cursor, ann.start);
      segments.push({ type: 'literal', text: gapText, target: 'title', regex: literalToRegex(gapText) });
    }

    // Generate regex for this annotation (potentially with nested groups)
    const children = annotations.filter(a =>
      a !== ann && strictlyContains(ann, a)
    );

    let regex: string;
    if (children.length > 0) {
      const innerRegex = buildNestedContent(text, annotations, ann.start, ann.end, ann);
      regex = `(?<${ann.variableName}>(?:${innerRegex})|(?:.+?))`;
    } else {
      regex = annotationRegex(ann);
    }

    segments.push({
      type: 'annotation',
      text: text.slice(ann.start, ann.end),
      annotation: ann,
      target: getPatternTarget(ann.variableName),
      regex,
    });
    cursor = ann.end;
  }

  // Trailing literal
  if (cursor < text.length) {
    segments.push({ type: 'literal', text: text.slice(cursor), target: 'title', regex: literalToRegex(text.slice(cursor)) });
  }

  // Assign literal segments to the appropriate pattern based on adjacent annotations.
  // A literal between two annotations of the same target inherits that target.
  // A literal between different targets gets assigned to the "earlier" target
  // (the one on its left side).
  for (let i = 0; i < segments.length; i++) {
    if (segments[i].type !== 'literal') continue;

    const prevAnn = findPrevAnnotation(segments, i);
    const nextAnn = findNextAnnotation(segments, i);

    if (prevAnn && nextAnn && prevAnn.target === nextAnn.target) {
      segments[i].target = prevAnn.target;
    } else if (prevAnn && nextAnn && prevAnn.target !== nextAnn.target) {
      // Literal bridges two different pattern targets — it goes with the prev
      segments[i].target = prevAnn.target;
    } else if (prevAnn) {
      segments[i].target = prevAnn.target;
    } else if (nextAnn) {
      segments[i].target = nextAnn.target;
    }
  }

  // Build split patterns (for backend re.search()) and combined pattern (for validation).
  //
  // Bridge literals (between annotations of different targets) need special handling:
  // - Title→other bridges: keep in title split pattern (anchors lazy quantifiers like team2)
  //   and in combined pattern as exact literal.
  // - Non-title→other bridges: these contain example-specific text (like "09:30" between
  //   date and time) that varies across examples. Drop from split patterns entirely;
  //   replace with .*? in combined pattern for flexible matching.
  // - Trailing literals (after last annotation): drop from both (re.search doesn't need them).
  const titleParts: string[] = [];
  const timeParts: string[] = [];
  const dateParts: string[] = [];
  const combinedParts: string[] = [];

  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];

    if (seg.type === 'annotation') {
      combinedParts.push(seg.regex);
      if (seg.target === 'time') timeParts.push(seg.regex);
      else if (seg.target === 'date') dateParts.push(seg.regex);
      else titleParts.push(seg.regex);
      continue;
    }

    // Literal segment — determine how to handle based on context
    const prevAnn = findPrevAnnotation(segments, i);
    const nextAnn = findNextAnnotation(segments, i);
    const isBridge = prevAnn && nextAnn && prevAnn.target !== nextAnn.target;
    const isTrailing = prevAnn && !nextAnn;

    if (isTrailing) {
      // After last annotation — skip (re.search doesn't need trailing anchors)
      continue;
    }

    if (isBridge && prevAnn!.target !== 'title') {
      // Non-title bridge (e.g., date→time): example-specific text like "09:30".
      // Use .*? in combined for flexibility; omit from split patterns.
      combinedParts.push('.*?');
      continue;
    }

    // Same-target literal, title→other bridge, or leading literal: include normally
    combinedParts.push(seg.regex);
    if (seg.target === 'time') timeParts.push(seg.regex);
    else if (seg.target === 'date') dateParts.push(seg.regex);
    else titleParts.push(seg.regex);
  }

  return {
    titlePattern: titleParts.join(''),
    timePattern: timeParts.join(''),
    datePattern: dateParts.join(''),
    combinedPattern: combinedParts.join(''),
  };
}

function findPrevAnnotation(segments: { type: string; target: PatternTarget }[], idx: number) {
  for (let i = idx - 1; i >= 0; i--) {
    if (segments[i].type === 'annotation') return segments[i];
  }
  return null;
}

function findNextAnnotation(segments: { type: string; target: PatternTarget }[], idx: number) {
  for (let i = idx + 1; i < segments.length; i++) {
    if (segments[i].type === 'annotation') return segments[i];
  }
  return null;
}

/**
 * Validate a regex pattern against multiple examples.
 * Returns per-example results with match status and captured groups.
 */
export function validateAgainstExamples(
  titlePattern: string,
  timePattern: string,
  datePattern: string,
  examples: Example[],
  combinedPattern?: string,
): ValidationResult[] {
  // Use pre-built combined pattern if available (visual mode preserves segment order),
  // otherwise concatenate split patterns (advanced mode).
  const combined = combinedPattern || [titlePattern, timePattern, datePattern].filter(Boolean).join('');
  if (!combined) {
    return examples.map(ex => ({ text: ex.text, matched: false, groups: null }));
  }

  let regex: RegExp;
  try {
    regex = new RegExp(combined);
  } catch {
    return examples.map(ex => ({ text: ex.text, matched: false, groups: null }));
  }

  return examples.map(ex => {
    const match = ex.text.match(regex);
    if (match && match.groups) {
      return { text: ex.text, matched: true, groups: { ...match.groups } };
    }
    return { text: ex.text, matched: match !== null, groups: null };
  });
}

/**
 * Attempt to reverse-parse an existing regex pattern into annotations.
 *
 * Looks for named capture groups (?<name>...) and maps them back to
 * positions in the example text. Returns null if the pattern is too
 * complex to reverse-parse.
 */
export function regexToAnnotations(
  pattern: string,
  exampleText: string,
): Annotation[] | null {
  if (!pattern || !exampleText) return null;

  try {
    const regex = new RegExp(pattern);
    const match = exampleText.match(regex);
    if (!match || !match.groups) return null;

    const annotations: Annotation[] = [];

    // For each named group, find its position in the match
    for (const [name, value] of Object.entries(match.groups)) {
      if (value === undefined) continue;

      // Find the position of this group's value in the example text
      // We need to find the correct occurrence, considering it should be
      // within the overall match range
      const matchStart = match.index ?? 0;
      const matchEnd = matchStart + match[0].length;
      const valueStart = exampleText.indexOf(value, matchStart);

      if (valueStart === -1 || valueStart >= matchEnd) continue;

      // Infer variable type: prefer name-based hint, then fall back to value-based detection
      let variableType: VariableType = 'text';
      const nameHint = NAME_TYPE_HINTS[name.toLowerCase()];
      if (nameHint) {
        variableType = nameHint;
      } else {
        if (/^\d+$/.test(value)) variableType = 'number';
        else if (/^(AM|PM)$/i.test(value)) variableType = 'word';
        // Default to 'text' (.+?) — don't infer 'word' since the same variable
        // in other examples may contain spaces, periods, or parentheses.
      }

      annotations.push({
        start: valueStart,
        end: valueStart + value.length,
        variableName: name,
        variableType,
      });
    }

    // Sort by position — overlaps are now allowed (nested groups)
    annotations.sort((a, b) => a.start - b.start || (b.end - b.start) - (a.end - a.start));

    return annotations.length > 0 ? annotations : null;
  } catch {
    return null;
  }
}

/**
 * Get all unique variable names from annotations.
 */
export function getVariableNames(annotations: Annotation[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const ann of annotations) {
    if (!seen.has(ann.variableName)) {
      seen.add(ann.variableName);
      result.push(ann.variableName);
    }
  }
  return result;
}

/**
 * Diagnose why a regex pattern fails to match a text string.
 * Extracts literal separator fragments between top-level groups and checks
 * which are missing from the text. Returns a human-readable reason or null.
 */
export function diagnoseRegexFailure(text: string, pattern: string): string | null {
  if (!pattern) return null;
  try {
    if (new RegExp(pattern).test(text)) return null;
  } catch {
    return 'Invalid regex';
  }

  // Extract top-level literal fragments (text at paren depth 0).
  // These are the fixed separators between named capture groups.
  const literals: string[] = [];
  let current = '';
  let depth = 0;
  for (let i = 0; i < pattern.length; i++) {
    if (pattern[i] === '\\' && i + 1 < pattern.length) {
      if (depth === 0) current += pattern[i] + pattern[i + 1];
      i++;
      continue;
    }
    if (pattern[i] === '(') {
      if (depth === 0 && current) {
        literals.push(current);
        current = '';
      }
      depth++;
      continue;
    }
    if (pattern[i] === ')') {
      depth = Math.max(0, depth - 1);
      continue;
    }
    if (depth === 0) current += pattern[i];
  }
  if (current) literals.push(current);

  // Test each literal fragment against the text
  for (const lit of literals) {
    if (!lit.trim()) continue;
    try {
      if (!new RegExp(lit, 'i').test(text)) {
        const readable = lit
          .replace(/\\s\+/g, ' ')
          .replace(/\\\./g, '.')
          .replace(/\\(.)/g, '$1');
        return `expected "${readable.trim()}"`;
      }
    } catch { continue; }
  }

  return null;
}

/**
 * Check if a new annotation has a partial overlap with existing annotations.
 * Full containment (wrapper/inner) is allowed. Partial overlaps and exact
 * duplicate ranges are blocked.
 */
export function hasOverlap(existing: Annotation[], start: number, end: number): boolean {
  return existing.some(ann => {
    const overlaps = start < ann.end && end > ann.start;
    if (!overlaps) return false;
    // Block exact same range (duplicate)
    if (start === ann.start && end === ann.end) return true;
    // Allow full containment in either direction
    const fullyContains = start <= ann.start && end >= ann.end;
    const fullyContained = ann.start <= start && ann.end >= end;
    return !fullyContains && !fullyContained;
  });
}
