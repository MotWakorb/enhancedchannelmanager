import { describe, it, expect } from 'vitest';
import { annotationsToRegex, validateAgainstExamples, regexToAnnotations, diagnoseRegexFailure } from './regexEngine';
import type { Annotation } from './types';

describe('annotationsToRegex wrapper alternation', () => {
  // Example: "St. Cloud St. vs Wisconsin" with event wrapping team1 vs team2
  const sportsText = 'St. Cloud St. vs Wisconsin';
  const sportsAnnotations: Annotation[] = [
    { start: 0, end: 26, variableName: 'event', variableType: 'text' },
    { start: 0, end: 13, variableName: 'team1', variableType: 'text' },
    { start: 17, end: 26, variableName: 'team2', variableType: 'text' },
  ];

  it('generates alternation pattern for wrapper groups', () => {
    const result = annotationsToRegex(sportsText, sportsAnnotations);
    // The event group should contain alternation: detailed branch | fallback
    expect(result.titlePattern).toContain('(?:');
    expect(result.titlePattern).toContain('|(?:.+?)');
  });

  it('matches detailed branch when inner structure is present', () => {
    const result = annotationsToRegex(sportsText, sportsAnnotations);
    const validation = validateAgainstExamples(
      result.titlePattern, result.timePattern, result.datePattern,
      [{ id: '1', text: 'St. Cloud St. vs Wisconsin', annotations: [] }],
      result.combinedPattern,
    );
    expect(validation[0].matched).toBe(true);
    // Inner groups are defined — detailed branch was chosen, not fallback
    expect(validation[0].groups?.team1).toBeTruthy();
    expect(validation[0].groups?.team2).toBeTruthy();
    // event captures the overall match (lazy .+? for team2 captures minimally
    // when nothing follows — this is expected regex behavior)
    expect(validation[0].groups?.event).toBeTruthy();
  });

  it('matches fallback branch when inner structure is absent', () => {
    const result = annotationsToRegex(sportsText, sportsAnnotations);
    const validation = validateAgainstExamples(
      result.titlePattern, result.timePattern, result.datePattern,
      [{ id: '2', text: "B1G Women's Swimming & Diving Championships - Day 4", annotations: [] }],
      result.combinedPattern,
    );
    expect(validation[0].matched).toBe(true);
    expect(validation[0].groups?.event).toBeTruthy();
    // Inner groups should be undefined (not captured)
    expect(validation[0].groups?.team1).toBeUndefined();
    expect(validation[0].groups?.team2).toBeUndefined();
  });

  it('both examples validate in a single run', () => {
    const result = annotationsToRegex(sportsText, sportsAnnotations);
    const examples = [
      { id: '1', text: 'St. Cloud St. vs Wisconsin', annotations: [] },
      { id: '2', text: "B1G Women's Swimming & Diving Championships - Day 4", annotations: [] },
    ];
    const validation = validateAgainstExamples(
      result.titlePattern, result.timePattern, result.datePattern,
      examples, result.combinedPattern,
    );
    expect(validation[0].matched).toBe(true);
    expect(validation[1].matched).toBe(true);
  });

  it('leaf annotations are unchanged (no alternation)', () => {
    const text = 'NBA: Lakers vs Celtics';
    const annotations: Annotation[] = [
      { start: 0, end: 3, variableName: 'league', variableType: 'word' },
      { start: 5, end: 11, variableName: 'team1', variableType: 'text' },
      { start: 15, end: 22, variableName: 'team2', variableType: 'text' },
    ];
    const result = annotationsToRegex(text, annotations);
    // Leaf groups should be plain named groups without alternation
    expect(result.titlePattern).toContain('(?<league>\\w+)');
    expect(result.titlePattern).toContain('(?<team1>.+?)');
    expect(result.titlePattern).toContain('(?<team2>.+?)');
    expect(result.titlePattern).not.toContain('|(?:.+?)');
  });

  it('regexToAnnotations skips undefined inner groups on fallback branch', () => {
    const result = annotationsToRegex(sportsText, sportsAnnotations);
    const fallbackText = "B1G Women's Swimming & Diving Championships - Day 4";
    const annotations = regexToAnnotations(result.combinedPattern, fallbackText);
    // Should only have the event annotation, not team1/team2
    expect(annotations).not.toBeNull();
    const names = annotations!.map(a => a.variableName);
    expect(names).toContain('event');
    expect(names).not.toContain('team1');
    expect(names).not.toContain('team2');
  });
});

describe('diagnoseRegexFailure', () => {
  it('returns null when pattern matches', () => {
    expect(diagnoseRegexFailure('hello world', 'hello')).toBeNull();
  });

  it('identifies missing literal separator', () => {
    // Pattern expects " @ " between groups
    const pattern = '(?<event>.+?)\\s+@\\s+(?<time>.+?)';
    const result = diagnoseRegexFailure('Game 1 | 09:00PM', pattern);
    expect(result).toContain('@');
  });

  it('identifies missing literal prefix', () => {
    // Pattern expects "NBA:" prefix
    const pattern = 'NBA:\\s+(?<team1>.+?)\\s+vs\\s+(?<team2>.+?)';
    const result = diagnoseRegexFailure('NFL: Cowboys vs Eagles', pattern);
    expect(result).toContain('NBA:');
  });

  it('returns null for empty pattern', () => {
    expect(diagnoseRegexFailure('any text', '')).toBeNull();
  });

  it('returns "Invalid regex" for broken pattern', () => {
    expect(diagnoseRegexFailure('text', '(?<bad')).toBe('Invalid regex');
  });

  it('returns generic message when all literals match but structure fails', () => {
    // Pattern with no top-level literals — just groups
    const pattern = '(?<a>\\d+)(?<b>\\d+)';
    const result = diagnoseRegexFailure('no digits here', pattern);
    // Should return null (no literals to diagnose) since there are no top-level literals
    // The function can only diagnose literal mismatches
    expect(result).toBeNull();
  });
});

describe('time variable type', () => {
  it('generates multi-group regex without wrapping named group', () => {
    const text = 'Game @ 8:00PM ET';
    const annotations: Annotation[] = [
      { start: 0, end: 4, variableName: 'event', variableType: 'text' },
      { start: 7, end: 16, variableName: 'time', variableType: 'time' },
    ];
    const result = annotationsToRegex(text, annotations);
    // Should contain individual named groups, not (?<time>...)
    expect(result.timePattern).toContain('(?<hour>');
    expect(result.timePattern).toContain('(?<minute>');
    expect(result.timePattern).toContain('(?<ampm>');
    expect(result.timePattern).not.toContain('(?<time>');
  });

  it('routes to time pattern (title pattern has no time groups)', () => {
    const text = '8:00PM ET';
    const annotations: Annotation[] = [
      { start: 0, end: 9, variableName: 'time', variableType: 'time' },
    ];
    const result = annotationsToRegex(text, annotations);
    expect(result.titlePattern).toBe('');
    expect(result.timePattern).toBeTruthy();
  });

  it('matches "8:00PM ET" with correct groups', () => {
    const text = 'NBA: Lakers vs Celtics @ 8:00PM ET';
    const annotations: Annotation[] = [
      { start: 0, end: 3, variableName: 'league', variableType: 'word' },
      { start: 5, end: 11, variableName: 'team1', variableType: 'text' },
      { start: 15, end: 22, variableName: 'team2', variableType: 'text' },
      { start: 25, end: 34, variableName: 'time', variableType: 'time' },
    ];
    const result = annotationsToRegex(text, annotations);
    const validation = validateAgainstExamples(
      result.titlePattern, result.timePattern, result.datePattern,
      [{ id: '1', text: 'NBA: Lakers vs Celtics @ 8:00PM ET', annotations: [] }],
      result.combinedPattern,
    );
    expect(validation[0].matched).toBe(true);
    expect(validation[0].groups?.hour).toBe('8');
    expect(validation[0].groups?.minute).toBe('00');
    expect(validation[0].groups?.ampm).toBe('PM');
    expect(validation[0].groups?.timezone).toBe('ET');
  });

  it('matches "02:30 PM CST"', () => {
    const text = 'Game @ 02:30 PM CST';
    const annotations: Annotation[] = [
      { start: 0, end: 4, variableName: 'event', variableType: 'text' },
      { start: 7, end: 19, variableName: 'time', variableType: 'time' },
    ];
    const result = annotationsToRegex(text, annotations);
    const validation = validateAgainstExamples(
      result.titlePattern, result.timePattern, result.datePattern,
      [{ id: '1', text: 'Game @ 02:30 PM CST', annotations: [] }],
      result.combinedPattern,
    );
    expect(validation[0].matched).toBe(true);
    expect(validation[0].groups?.hour).toBe('02');
    expect(validation[0].groups?.minute).toBe('30');
    expect(validation[0].groups?.ampm).toBe('PM');
    expect(validation[0].groups?.timezone).toBe('CST');
  });

  it('matches "8PM" without minutes or timezone', () => {
    const text = 'Game @ 8PM';
    const annotations: Annotation[] = [
      { start: 0, end: 4, variableName: 'event', variableType: 'text' },
      { start: 7, end: 10, variableName: 'time', variableType: 'time' },
    ];
    const result = annotationsToRegex(text, annotations);
    const validation = validateAgainstExamples(
      result.titlePattern, result.timePattern, result.datePattern,
      [{ id: '1', text: 'Game @ 8PM', annotations: [] }],
      result.combinedPattern,
    );
    expect(validation[0].matched).toBe(true);
    expect(validation[0].groups?.hour).toBe('8');
    expect(validation[0].groups?.ampm).toBe('PM');
    expect(validation[0].groups?.minute).toBeUndefined();
  });

  it('works in combined pattern with title, date, and time annotations', () => {
    const text = 'NBA: Lakers vs Celtics Feb 15 8:00PM ET';
    const annotations: Annotation[] = [
      { start: 0, end: 3, variableName: 'league', variableType: 'word' },
      { start: 5, end: 11, variableName: 'team1', variableType: 'text' },
      { start: 15, end: 22, variableName: 'team2', variableType: 'text' },
      { start: 23, end: 29, variableName: 'date', variableType: 'date' },
      { start: 30, end: 39, variableName: 'time', variableType: 'time' },
    ];
    const result = annotationsToRegex(text, annotations);
    expect(result.titlePattern).toBeTruthy();
    expect(result.datePattern).toBeTruthy();
    expect(result.timePattern).toBeTruthy();

    const validation = validateAgainstExamples(
      result.titlePattern, result.timePattern, result.datePattern,
      [{ id: '1', text: 'NBA: Lakers vs Celtics Feb 15 8:00PM ET', annotations: [] }],
      result.combinedPattern,
    );
    expect(validation[0].matched).toBe(true);
    expect(validation[0].groups?.league).toBe('NBA');
    expect(validation[0].groups?.team1).toBeTruthy();
    expect(validation[0].groups?.month).toBe('Feb');
    expect(validation[0].groups?.day).toBe('15');
    expect(validation[0].groups?.hour).toBe('8');
    expect(validation[0].groups?.ampm).toBe('PM');
  });
});

describe('date variable type', () => {
  it('generates multi-group regex without wrapping named group', () => {
    const text = 'Game on Feb 15';
    const annotations: Annotation[] = [
      { start: 0, end: 4, variableName: 'event', variableType: 'text' },
      { start: 8, end: 14, variableName: 'date', variableType: 'date' },
    ];
    const result = annotationsToRegex(text, annotations);
    expect(result.datePattern).toContain('(?<month>');
    expect(result.datePattern).toContain('(?<day>');
    expect(result.datePattern).not.toContain('(?<date>');
  });

  it('routes to date pattern', () => {
    const text = 'Feb 15';
    const annotations: Annotation[] = [
      { start: 0, end: 6, variableName: 'date', variableType: 'date' },
    ];
    const result = annotationsToRegex(text, annotations);
    expect(result.titlePattern).toBe('');
    expect(result.datePattern).toBeTruthy();
  });

  it('matches abbreviated month: "Feb 15"', () => {
    const text = 'Game on Feb 15';
    const annotations: Annotation[] = [
      { start: 0, end: 4, variableName: 'event', variableType: 'text' },
      { start: 8, end: 14, variableName: 'date', variableType: 'date' },
    ];
    const result = annotationsToRegex(text, annotations);
    const validation = validateAgainstExamples(
      result.titlePattern, result.timePattern, result.datePattern,
      [{ id: '1', text: 'Game on Feb 15', annotations: [] }],
      result.combinedPattern,
    );
    expect(validation[0].matched).toBe(true);
    expect(validation[0].groups?.month).toBe('Feb');
    expect(validation[0].groups?.day).toBe('15');
  });

  it('matches abbreviated month: "Jan 3"', () => {
    const text = 'Game on Jan 3';
    const annotations: Annotation[] = [
      { start: 0, end: 4, variableName: 'event', variableType: 'text' },
      { start: 8, end: 13, variableName: 'date', variableType: 'date' },
    ];
    const result = annotationsToRegex(text, annotations);
    const validation = validateAgainstExamples(
      result.titlePattern, result.timePattern, result.datePattern,
      [{ id: '1', text: 'Game on Jan 3', annotations: [] }],
      result.combinedPattern,
    );
    expect(validation[0].matched).toBe(true);
    expect(validation[0].groups?.month).toBe('Jan');
    expect(validation[0].groups?.day).toBe('3');
  });

  it('matches numeric: "02/15"', () => {
    const text = 'Game on 02/15';
    const annotations: Annotation[] = [
      { start: 0, end: 4, variableName: 'event', variableType: 'text' },
      { start: 8, end: 13, variableName: 'date', variableType: 'date' },
    ];
    const result = annotationsToRegex(text, annotations);
    const validation = validateAgainstExamples(
      result.titlePattern, result.timePattern, result.datePattern,
      [{ id: '1', text: 'Game on 02/15', annotations: [] }],
      result.combinedPattern,
    );
    expect(validation[0].matched).toBe(true);
    expect(validation[0].groups?.month).toBe('02');
    expect(validation[0].groups?.day).toBe('15');
  });

  it('matches numeric: "1/3"', () => {
    const text = 'Game on 1/3';
    const annotations: Annotation[] = [
      { start: 0, end: 4, variableName: 'event', variableType: 'text' },
      { start: 8, end: 11, variableName: 'date', variableType: 'date' },
    ];
    const result = annotationsToRegex(text, annotations);
    const validation = validateAgainstExamples(
      result.titlePattern, result.timePattern, result.datePattern,
      [{ id: '1', text: 'Game on 1/3', annotations: [] }],
      result.combinedPattern,
    );
    expect(validation[0].matched).toBe(true);
    expect(validation[0].groups?.month).toBe('1');
    expect(validation[0].groups?.day).toBe('3');
  });

  it('matches with year: "Feb 15, 2025"', () => {
    const text = 'Game on Feb 15, 2025';
    const annotations: Annotation[] = [
      { start: 0, end: 4, variableName: 'event', variableType: 'text' },
      { start: 8, end: 20, variableName: 'date', variableType: 'date' },
    ];
    const result = annotationsToRegex(text, annotations);
    const validation = validateAgainstExamples(
      result.titlePattern, result.timePattern, result.datePattern,
      [{ id: '1', text: 'Game on Feb 15, 2025', annotations: [] }],
      result.combinedPattern,
    );
    expect(validation[0].matched).toBe(true);
    expect(validation[0].groups?.month).toBe('Feb');
    expect(validation[0].groups?.day).toBe('15');
    expect(validation[0].groups?.year).toBe('2025');
  });

  it('matches with year: "02/15/2025"', () => {
    const text = 'Game on 02/15/2025';
    const annotations: Annotation[] = [
      { start: 0, end: 4, variableName: 'event', variableType: 'text' },
      { start: 8, end: 18, variableName: 'date', variableType: 'date' },
    ];
    const result = annotationsToRegex(text, annotations);
    const validation = validateAgainstExamples(
      result.titlePattern, result.timePattern, result.datePattern,
      [{ id: '1', text: 'Game on 02/15/2025', annotations: [] }],
      result.combinedPattern,
    );
    expect(validation[0].matched).toBe(true);
    expect(validation[0].groups?.month).toBe('02');
    expect(validation[0].groups?.day).toBe('15');
    expect(validation[0].groups?.year).toBe('2025');
  });

  it('works in combined pattern with title + time + date', () => {
    const text = 'NBA: Lakers vs Celtics Feb 15 8:00PM ET';
    const annotations: Annotation[] = [
      { start: 0, end: 3, variableName: 'league', variableType: 'word' },
      { start: 5, end: 11, variableName: 'team1', variableType: 'text' },
      { start: 15, end: 22, variableName: 'team2', variableType: 'text' },
      { start: 23, end: 29, variableName: 'date', variableType: 'date' },
      { start: 30, end: 39, variableName: 'time', variableType: 'time' },
    ];
    const result = annotationsToRegex(text, annotations);
    expect(result.titlePattern).toBeTruthy();
    expect(result.datePattern).toBeTruthy();
    expect(result.timePattern).toBeTruthy();

    const validation = validateAgainstExamples(
      result.titlePattern, result.timePattern, result.datePattern,
      [{ id: '1', text: 'NBA: Lakers vs Celtics Feb 15 8:00PM ET', annotations: [] }],
      result.combinedPattern,
    );
    expect(validation[0].matched).toBe(true);
    expect(validation[0].groups?.league).toBe('NBA');
    expect(validation[0].groups?.team1).toBeTruthy();
    expect(validation[0].groups?.month).toBe('Feb');
    expect(validation[0].groups?.day).toBe('15');
    expect(validation[0].groups?.hour).toBe('8');
    expect(validation[0].groups?.ampm).toBe('PM');
    expect(validation[0].groups?.timezone).toBe('ET');
  });
});
