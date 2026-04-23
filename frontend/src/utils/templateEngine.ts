/**
 * Template engine for dummy EPG title/description rendering.
 *
 * Mirrors backend/template_engine.py — any syntax change must be made in
 * both to keep live-preview output in sync with server-rendered output.
 *
 * Syntax:
 *   - Placeholders          {name}
 *   - Chained pipes         {name|uppercase|trim|strip:-}
 *   - Lookup pipes          {name|lookup:tablename}
 *   - Conditionals          {if:group}body{/if}
 *                           {if:group=value}body{/if}
 *                           {if:group~regex}body{/if}
 *   - Legacy suffix         {name_normalize}  (lowercase, strip non-alphanumeric)
 */

export class TemplateSyntaxError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'TemplateSyntaxError';
  }
}

export type LookupTables = Record<string, Record<string, string>>;

// ---------------------------------------------------------------------------
// Caps (mirror of the Python values)
// ---------------------------------------------------------------------------
export const MAX_TEMPLATE_LEN = 4096;
export const MAX_INPUT_LEN = 1024;
export const MAX_REGEX_LEN = 500;

// ---------------------------------------------------------------------------
// Legacy normalize — lowercase + strip everything that isn't a-z or 0-9.
// ---------------------------------------------------------------------------
function legacyNormalize(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, '');
}

// ---------------------------------------------------------------------------
// Transforms
// ---------------------------------------------------------------------------
type Transform = (value: string, arg: string | null) => string;

const TRANSFORMS: Record<string, Transform> = {
  uppercase: (v) => v.toUpperCase(),
  lowercase: (v) => v.toLowerCase(),
  titlecase: (v) => v.replace(/\b\w/g, (c) => c.toUpperCase()).replace(/\B\w/g, (c) => c.toLowerCase()),
  trim: (v) => v.trim(),
  strip: (v, arg) => stripChars(v, arg),
  replace: (v, arg) => replaceArg(v, arg),
  normalize: (v) => legacyNormalize(v),
};

function stripChars(value: string, arg: string | null): string {
  if (!arg) return value.trim();
  // Build a class of chars to strip from both ends.
  const escaped = arg.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = new RegExp(`^[${escaped}]+|[${escaped}]+$`, 'g');
  return value.replace(re, '');
}

function replaceArg(value: string, arg: string | null): string {
  if (arg === null) return value;
  let src: string;
  let dst: string;
  const idx = arg.indexOf(':');
  if (idx === -1) {
    src = arg;
    dst = '';
  } else {
    src = arg.slice(0, idx);
    dst = arg.slice(idx + 1);
  }
  // Literal (non-regex) global replace.
  return value.split(src).join(dst);
}

// ---------------------------------------------------------------------------
// TemplateEngine
// ---------------------------------------------------------------------------
/** Shape of a single entry in the trace returned by `renderWithTrace`. */
export type TraceStep =
  | { kind: 'literal'; text: string }
  | {
      kind: 'placeholder';
      raw: string;
      group_name: string;
      initial_value: string;
      pipes: PipeStep[];
      final_value: string;
    }
  | {
      kind: 'conditional';
      condition: string;
      kind_detail: 'truthy' | 'equality' | 'regex';
      taken: boolean;
      value: string;
      body: TraceStep[];
    };

export interface PipeStep {
  transform: string;
  arg: string | null;
  input: string;
  output: string;
  source?: string;
  matched?: boolean;
}

export class TemplateEngine {
  private lookups: LookupTables;

  constructor(lookups?: LookupTables) {
    this.lookups = lookups || {};
  }

  render(
    template: string,
    groups: Record<string, unknown>,
    lookups?: LookupTables,
  ): string {
    if (template === null || template === undefined) return '';
    if (template.length > MAX_TEMPLATE_LEN) {
      throw new TemplateSyntaxError(
        `Template exceeds maximum length of ${MAX_TEMPLATE_LEN} chars`,
      );
    }

    const effectiveLookups = lookups !== undefined ? lookups : this.lookups;
    const boundedGroups: Record<string, string> = {};
    for (const [k, v] of Object.entries(groups)) {
      boundedGroups[k] = truncate(String(v ?? ''));
    }
    return this.renderSegment(template, boundedGroups, effectiveLookups);
  }

  renderWithTrace(
    template: string,
    groups: Record<string, unknown>,
    lookups?: LookupTables,
  ): { output: string; trace: TraceStep[] } {
    if (template === null || template === undefined) return { output: '', trace: [] };
    if (template.length > MAX_TEMPLATE_LEN) {
      throw new TemplateSyntaxError(
        `Template exceeds maximum length of ${MAX_TEMPLATE_LEN} chars`,
      );
    }
    const effectiveLookups = lookups !== undefined ? lookups : this.lookups;
    const boundedGroups: Record<string, string> = {};
    for (const [k, v] of Object.entries(groups)) {
      boundedGroups[k] = truncate(String(v ?? ''));
    }
    const trace: TraceStep[] = [];
    const output = this.renderSegment(template, boundedGroups, effectiveLookups, trace);
    return { output, trace };
  }

  private renderSegment(
    template: string,
    groups: Record<string, string>,
    lookups: LookupTables,
    traceOut?: TraceStep[],
  ): string {
    const out: string[] = [];
    let i = 0;
    const n = template.length;

    while (i < n) {
      const brace = template.indexOf('{', i);
      if (brace === -1) {
        const tail = template.slice(i);
        out.push(tail);
        if (traceOut && tail) traceOut.push({ kind: 'literal', text: tail });
        break;
      }

      if (brace > i) {
        const literal = template.slice(i, brace);
        out.push(literal);
        if (traceOut) traceOut.push({ kind: 'literal', text: literal });
      }

      const close = template.indexOf('}', brace);
      if (close === -1) {
        throw new TemplateSyntaxError(`Unclosed '{' at position ${brace}`);
      }

      const directive = template.slice(brace + 1, close);

      if (directive.startsWith('if:')) {
        const bodyStart = close + 1;
        const [bodyEnd, after] = this.findMatchingEndif(template, bodyStart);
        const condition = directive.slice(3);
        const { taken, detail } = this.evaluateCondition(condition, groups);
        const bodyTrace: TraceStep[] = [];
        if (taken) {
          out.push(this.renderSegment(
            template.slice(bodyStart, bodyEnd),
            groups,
            lookups,
            traceOut ? bodyTrace : undefined,
          ));
        }
        if (traceOut) {
          traceOut.push({
            kind: 'conditional',
            condition,
            kind_detail: detail.kind,
            taken,
            value: detail.value,
            body: bodyTrace,
          });
        }
        i = after;
        continue;
      }

      if (directive === '/if') {
        throw new TemplateSyntaxError("Unmatched '{/if}'");
      }

      out.push(this.renderPlaceholder(directive, groups, lookups, traceOut));
      i = close + 1;
    }

    return out.join('');
  }

  private findMatchingEndif(template: string, bodyStart: number): [number, number] {
    let depth = 1;
    let i = bodyStart;
    const n = template.length;
    while (i < n) {
      const b = template.indexOf('{', i);
      if (b === -1) break;
      const c = template.indexOf('}', b);
      if (c === -1) break;
      const directive = template.slice(b + 1, c);
      if (directive.startsWith('if:')) depth += 1;
      else if (directive === '/if') {
        depth -= 1;
        if (depth === 0) return [b, c + 1];
      }
      i = c + 1;
    }
    throw new TemplateSyntaxError("'{if:...}' without matching '{/if}'");
  }

  private evaluateCondition(
    condition: string,
    groups: Record<string, string>,
  ): { taken: boolean; detail: { kind: 'truthy' | 'equality' | 'regex'; value: string } } {
    // Equality check first — but only if the '=' comes before any '~'.
    const eqIdx = condition.indexOf('=');
    const tildeIdx = condition.indexOf('~');
    if (eqIdx !== -1 && (tildeIdx === -1 || eqIdx < tildeIdx)) {
      const name = condition.slice(0, eqIdx);
      const expected = condition.slice(eqIdx + 1);
      const value = groups[name] ?? '';
      return { taken: value === expected, detail: { kind: 'equality', value } };
    }

    if (tildeIdx !== -1) {
      const name = condition.slice(0, tildeIdx);
      const pattern = condition.slice(tildeIdx + 1);
      const value = groups[name] ?? '';
      if (pattern.length > MAX_REGEX_LEN) {
        return { taken: false, detail: { kind: 'regex', value } };
      }
      try {
        const re = new RegExp(pattern);
        return { taken: re.test(value), detail: { kind: 'regex', value } };
      } catch {
        return { taken: false, detail: { kind: 'regex', value } };
      }
    }

    const value = groups[condition] ?? '';
    return { taken: Boolean(value), detail: { kind: 'truthy', value } };
  }

  private renderPlaceholder(
    body: string,
    groups: Record<string, string>,
    lookups: LookupTables,
    traceOut?: TraceStep[],
  ): string {
    const parts = body.split('|');
    const name = parts[0].trim();
    const pipes = parts.slice(1);
    const raw = '{' + body + '}';

    if (name.endsWith('_normalize') && !(name in groups)) {
      const base = name.slice(0, -'_normalize'.length);
      const initial = groups[base] ?? '';
      const final = legacyNormalize(initial);
      if (traceOut) {
        traceOut.push({
          kind: 'placeholder',
          raw,
          group_name: base,
          initial_value: initial,
          pipes: [{
            transform: 'normalize',
            arg: null,
            input: initial,
            output: final,
            source: 'legacy _normalize suffix',
          }],
          final_value: final,
        });
      }
      return final;
    }

    const initial = groups[name] ?? '';
    let value = initial;
    const pipeSteps: PipeStep[] = [];

    for (const rawPipe of pipes) {
      const pipeSpec = rawPipe.trim();
      if (!pipeSpec) continue;

      const colonIdx = pipeSpec.indexOf(':');
      const transform = colonIdx === -1 ? pipeSpec : pipeSpec.slice(0, colonIdx);
      const arg = colonIdx === -1 ? null : pipeSpec.slice(colonIdx + 1);
      const stepInput = value;

      if (transform === 'lookup') {
        if (arg === null) throw new TemplateSyntaxError('lookup transform requires a table name');
        if (!(arg in lookups)) throw new TemplateSyntaxError(`Unknown lookup table: "${arg}"`);
        const table = lookups[arg];
        const matched = value in table;
        value = matched ? table[value] : value;
        if (traceOut) {
          pipeSteps.push({
            transform: 'lookup',
            arg,
            input: stepInput,
            output: value,
            source: arg,
            matched,
          });
        }
        continue;
      }

      const fn = TRANSFORMS[transform];
      if (!fn) throw new TemplateSyntaxError(`Unknown transform: "${transform}"`);
      value = fn(value, arg);
      if (traceOut) {
        pipeSteps.push({ transform, arg, input: stepInput, output: value });
      }
    }

    const final = truncate(value);
    if (traceOut) {
      traceOut.push({
        kind: 'placeholder',
        raw,
        group_name: name,
        initial_value: initial,
        pipes: pipeSteps,
        final_value: final,
      });
    }
    return final;
  }
}

function truncate(value: string): string {
  return value.length > MAX_INPUT_LEN ? value.slice(0, MAX_INPUT_LEN) : value;
}

export function render(
  template: string,
  groups: Record<string, unknown>,
  lookups?: LookupTables,
): string {
  return new TemplateEngine().render(template, groups, lookups);
}
