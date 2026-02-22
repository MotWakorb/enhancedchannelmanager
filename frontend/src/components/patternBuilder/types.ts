/**
 * Types for the Visual Pattern Builder.
 */

/** Variable type determines the regex fragment generated for an annotation. */
export type VariableType = 'text' | 'number' | 'word' | 'date' | 'time' | 'custom';

/** Map from variable type to its regex fragment. */
export const VARIABLE_TYPE_REGEX: Record<VariableType, string> = {
  text: '.+?',
  number: '\\d+',
  word: '\\w+',
  date: '(?<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|\\d{1,2})[/\\s]\\s*(?<day>\\d{1,2})(?:[/,\\s]+(?<year>\\d{2,4}))?',
  time: '(?<hour>\\d{1,2})(?:\\s*:\\s*(?<minute>\\d{2}))?\\s*(?<ampm>[AaPp][Mm])(?:\\s+(?<timezone>[A-Z]{1,5}))?',
  custom: '',
};

/** Human-readable labels for variable types. */
export const VARIABLE_TYPE_LABELS: Record<VariableType, string> = {
  text: 'Text (.+?)',
  number: 'Number (\\d+)',
  word: 'Word (\\w+)',
  date: 'Date (Mon DD or MM/DD)',
  time: 'Time (H:MM AM/PM)',
  custom: 'Custom regex',
};

/** Which pattern a variable routes to, based on its name. */
export type PatternTarget = 'title' | 'time' | 'date';

/** Time-related variable names that route to the time pattern. */
export const TIME_VARIABLES = new Set([
  'hour', 'hours', 'hrs',
  'minute', 'minutes', 'mins',
  'second', 'seconds', 'secs',
  'ampm', 'time', 'timezone', 'tz',
]);

/** Date-related variable names that route to the date pattern. */
export const DATE_VARIABLES = new Set(['month', 'day', 'date', 'year']);

/** Suggested variable type based on variable name (overrides text-based auto-detect). */
export const NAME_TYPE_HINTS: Record<string, VariableType> = {
  hour: 'number', hours: 'number', hrs: 'number',
  minute: 'number', minutes: 'number', mins: 'number',
  second: 'number', seconds: 'number', secs: 'number',
  month: 'word',
  day: 'number', year: 'number',
  date: 'date',
  time: 'time',
  timezone: 'word', tz: 'word',
};

/** A single annotation on an example string — a highlighted span assigned to a variable. */
export interface Annotation {
  /** Character offset where the annotation starts (inclusive). */
  start: number;
  /** Character offset where the annotation ends (exclusive). */
  end: number;
  /** Variable name (e.g., "league", "team1", "hour"). */
  variableName: string;
  /** Variable type determining regex fragment. */
  variableType: VariableType;
  /** Custom regex fragment (only used when variableType is 'custom'). */
  customRegex?: string;
}

/** An example title string with its annotations. */
export interface Example {
  /** Unique ID for React keys. */
  id: string;
  /** The example title text. */
  text: string;
  /** Annotations on this example (only the active example has editable annotations). */
  annotations: Annotation[];
}

/** Full state persisted as JSON in pattern_builder_examples. */
export interface PatternBuilderState {
  examples: Example[];
  /** Index of the active (annotated) example. */
  activeExampleIndex: number;
}

/** Result of validating a generated regex against a single example. */
export interface ValidationResult {
  /** The example text. */
  text: string;
  /** Whether the regex matched. */
  matched: boolean;
  /** Captured groups (if matched). */
  groups: Record<string, string> | null;
}

/** Props for the PatternBuilder component. */
export interface PatternBuilderProps {
  titlePattern: string;
  timePattern: string;
  datePattern: string;
  onTitlePatternChange: (pattern: string) => void;
  onTimePatternChange: (pattern: string) => void;
  onDatePatternChange: (pattern: string) => void;
  builderExamples: string | null;
  onBuilderExamplesChange: (json: string) => void;
}
