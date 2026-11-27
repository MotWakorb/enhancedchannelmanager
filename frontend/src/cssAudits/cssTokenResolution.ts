/// <reference types="node" />
/**
 * Per-theme resolution analysis for CSS custom properties.
 *
 * Pure functions: callers hand in `{ file, text }` sources, so the same code
 * runs over fixtures and over the real tree. See docs/css_guidelines.md,
 * "Custom property resolution contract".
 */

export type Theme = 'dark' | 'light' | 'high-contrast';
export const THEMES: readonly Theme[] = ['dark', 'light', 'high-contrast'];

export interface Source {
  file: string;
  text: string;
}

export interface Definition {
  token: string;
  file: string;
  line: number;
  /** Themes in which the declaring selector can match. */
  themes: Theme[];
  /** true when declared on :root/html/body/*, so every element inherits it. */
  global: boolean;
  selector: string;
}

export interface Reference {
  token: string;
  file: string;
  line: number;
  /** Themes in which the referencing rule can apply. */
  themes: Theme[];
  hasFallback: boolean;
  selector: string;
  /** Property name receiving the value; a `--*` name means a definition value. */
  property: string;
}

export type Finding =
  | { kind: 'undefined'; token: string; themes: Theme[]; ref: Reference }
  | { kind: 'fallback-masked'; token: string; themes: Theme[]; ref: Reference };

// ---------------------------------------------------------------------------
// Comment stripping and declaration extraction
// ---------------------------------------------------------------------------

/** Blank out /* *\/ comments, keeping newlines so line numbers survive. */
export function stripComments(text: string): string {
  let out = '';
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    if (ch === '"' || ch === "'") {
      let j = i + 1;
      while (j < text.length && text[j] !== ch) j += text[j] === '\\' ? 2 : 1;
      out += text.slice(i, j + 1);
      i = j + 1;
    } else if (ch === '/' && text[i + 1] === '*') {
      const end = text.indexOf('*/', i + 2);
      const stop = end === -1 ? text.length : end + 2;
      out += text.slice(i, stop).replace(/[^\n]/g, ' ');
      i = stop;
    } else {
      out += ch;
      i += 1;
    }
  }
  return out;
}

interface Decl {
  selector: string;
  property: string;
  value: string;
  line: number;
}

/**
 * Walk a comment-stripped stylesheet and emit every declaration with the
 * selector of its nearest style rule. At-rules that only group rules (@media,
 * @supports, @layer, @container, @keyframes) are transparent.
 */
export function extractDeclarations(clean: string): Decl[] {
  const decls: Decl[] = [];
  const stack: { selector: string; isRule: boolean }[] = [];
  let seg = '';
  let paren = 0;
  let line = 1;
  let segLine = 1;
  let quote = '';
  for (let i = 0; i < clean.length; i += 1) {
    const ch = clean[i];
    if (ch === '\n') line += 1;
    if (quote) {
      seg += ch;
      if (ch === '\\') {
        seg += clean[i + 1] ?? '';
        i += 1;
      } else if (ch === quote) quote = '';
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      if (!seg.trim()) segLine = line;
      seg += ch;
      continue;
    }
    if (ch === '(') paren += 1;
    if (ch === ')') paren = Math.max(0, paren - 1);
    if (paren === 0 && ch === '{') {
      const prelude = seg.trim();
      const isAt = prelude.startsWith('@');
      const parent = [...stack].reverse().find((s) => s.isRule);
      stack.push({
        selector: isAt ? (parent?.selector ?? '') : prelude,
        isRule: !isAt,
      });
      seg = '';
      continue;
    }
    if (paren === 0 && (ch === ';' || ch === '}')) {
      const text = seg.trim();
      const top = stack[stack.length - 1];
      if (text && top && top.isRule) {
        const colon = text.indexOf(':');
        if (colon > 0) {
          decls.push({
            selector: top.selector,
            property: text.slice(0, colon).trim(),
            value: text.slice(colon + 1).trim(),
            line: segLine,
          });
        }
      }
      if (ch === '}') stack.pop();
      seg = '';
      continue;
    }
    if (!seg.trim()) segLine = line;
    seg += ch;
  }
  return decls;
}

// ---------------------------------------------------------------------------
// Selector -> theme scope
// ---------------------------------------------------------------------------

const THEME_ATTR = /\[data-theme\s*(?:=\s*(?:"([^"]*)"|'([^']*)'|([^\]\s]*)))?\s*\]/g;
const NOT_THEME = /:not\(\s*\[data-theme\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\]\s]*))\s*\]\s*\)/g;

function themeOfValue(v: string): Theme | null {
  if (v === '' || v === 'dark') return 'dark';
  if (v === 'light') return 'light';
  if (v === 'high-contrast') return 'high-contrast';
  return null;
}

/** Themes in which a single (comma-free) selector can match. */
export function selectorThemes(selector: string): Theme[] {
  let themes: Theme[] = [...THEMES];
  let rest = selector;
  rest = rest.replace(NOT_THEME, (_m, a, b, c) => {
    const t = themeOfValue(a ?? b ?? c ?? '');
    if (t) themes = themes.filter((x) => x !== t);
    return '';
  });
  const positive: Theme[] = [];
  rest.replace(THEME_ATTR, (_m, a, b, c) => {
    const t = themeOfValue(a ?? b ?? c ?? '');
    if (t) positive.push(t);
    return '';
  });
  if (positive.length) themes = themes.filter((t) => positive.includes(t));
  return themes;
}

/** Split on top-level commas (not inside () or []). */
export function splitSelectorList(list: string): string[] {
  const out: string[] = [];
  let depth = 0;
  let cur = '';
  for (const ch of list) {
    if (ch === '(' || ch === '[') depth += 1;
    if (ch === ')' || ch === ']') depth -= 1;
    if (ch === ',' && depth === 0) {
      out.push(cur.trim());
      cur = '';
    } else cur += ch;
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

/** True when the selector (theme attributes ignored) targets the document root. */
export function isRootSelector(selector: string): boolean {
  const stripped = selector.replace(NOT_THEME, '').replace(THEME_ATTR, '').trim();
  return stripped === '' || stripped === ':root' || stripped === 'html' || stripped === 'body' || stripped === '*' || stripped === 'html body';
}

// ---------------------------------------------------------------------------
// var() extraction
// ---------------------------------------------------------------------------

interface VarUse {
  token: string;
  hasFallback: boolean;
}

/** Every var(--x[, fallback]) in a value, including ones nested in a fallback. */
export function extractVars(value: string): VarUse[] {
  const uses: VarUse[] = [];
  const re = /var\(\s*(--[A-Za-z0-9_-]+)\s*(,)?/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(value))) {
    uses.push({ token: m[1], hasFallback: m[2] === ',' });
  }
  return uses;
}

// ---------------------------------------------------------------------------
// Whole-tree collection
// ---------------------------------------------------------------------------

export interface Collected {
  definitions: Definition[];
  references: Reference[];
}

function collectCss(src: Source, out: Collected): void {
  const clean = stripComments(src.text);
  for (const d of extractDeclarations(clean)) {
    const selectors = splitSelectorList(d.selector);
    if (d.property.startsWith('--')) {
      for (const sel of selectors) {
        out.definitions.push({
          token: d.property,
          file: src.file,
          line: d.line,
          themes: selectorThemes(sel),
          global: isRootSelector(sel),
          selector: sel,
        });
      }
    }
    const uses = extractVars(d.value);
    if (!uses.length) continue;
    const themes = [
      ...new Set(selectors.flatMap((s) => selectorThemes(s))),
    ] as Theme[];
    for (const u of uses) {
      out.references.push({
        token: u.token,
        file: src.file,
        line: d.line,
        themes,
        hasFallback: u.hasFallback,
        selector: d.selector,
        property: d.property,
      });
    }
  }
}

/**
 * TSX/TS: inline `var(--x)` strings are references that apply in every theme.
 * Custom properties set inline (`'--x': v` or setProperty('--x')) are
 * element-local definitions.
 */
function collectScript(src: Source, out: Collected): void {
  const text = src.text;
  const lineAt = (offset: number) => text.slice(0, offset).split('\n').length;
  for (const m of text.matchAll(/var\(\s*(--[A-Za-z0-9_-]+)\s*(,)?/g)) {
    // Skip comment-only lines (`// ...`, ` * ...`).
    const ls = text.lastIndexOf('\n', m.index) + 1;
    const lineText = text.slice(ls, text.indexOf('\n', m.index) === -1 ? undefined : text.indexOf('\n', m.index));
    if (/^\s*(\/\/|\*|\/\*)/.test(lineText)) continue;
    out.references.push({
      token: m[1],
      file: src.file,
      line: lineAt(m.index ?? 0),
      themes: [...THEMES],
      hasFallback: m[2] === ',',
      selector: '(inline style)',
      property: '(inline)',
    });
  }
  for (const m of text.matchAll(/['"](--[A-Za-z0-9_-]+)['"]\s*:|setProperty\(\s*['"](--[A-Za-z0-9_-]+)/g)) {
    out.definitions.push({
      token: (m[1] ?? m[2]) as string,
      file: src.file,
      line: lineAt(m.index ?? 0),
      themes: [...THEMES],
      global: false,
      selector: '(inline style)',
    });
  }
}

export function collect(sources: Source[]): Collected {
  const out: Collected = { definitions: [], references: [] };
  for (const s of sources) {
    if (s.file.endsWith('.css')) collectCss(s, out);
    else collectScript(s, out);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Resolution
// ---------------------------------------------------------------------------

export function globalThemes(defs: Definition[], token: string): Set<Theme> {
  const set = new Set<Theme>();
  for (const d of defs) if (d.token === token && d.global) d.themes.forEach((t) => set.add(t));
  return set;
}

/**
 * Policy (docs/css_guidelines.md):
 *  - A token resolves in a theme only when a root-scope declaration matches
 *    that theme. A declaration scoped to another theme never counts.
 *  - An element-local declaration (`.card { --x: ... }`, inline style) makes
 *    the token a "local seam": the reference is accepted, because ancestry is
 *    not decidable from CSS text. Local seams are reported separately.
 *  - Unresolved with no fallback: `undefined` (declaration silently dropped).
 *  - Unresolved but carrying a fallback: `fallback-masked`. The fallback is a
 *    permitted contract only for tokens named in `fallbackAllowed`.
 */
export interface AnalyzeOptions {
  /** Tokens whose fallback IS the contract (an optional per-component seam). */
  fallbackAllowed?: ReadonlySet<string>;
}

export function analyze(c: Collected, opts: AnalyzeOptions = {}): Finding[] {
  const allowed = opts.fallbackAllowed ?? new Set<string>();
  const localTokens = new Set(c.definitions.filter((d) => !d.global).map((d) => d.token));
  const findings: Finding[] = [];
  const cache = new Map<string, Set<Theme>>();
  for (const ref of c.references) {
    if (localTokens.has(ref.token) && !cache.has(ref.token) && globalThemes(c.definitions, ref.token).size === 0) {
      cache.set(ref.token, new Set(THEMES));
    }
    let defined = cache.get(ref.token);
    if (!defined) {
      defined = globalThemes(c.definitions, ref.token);
      cache.set(ref.token, defined);
    }
    const missing = ref.themes.filter((t) => !defined.has(t));
    if (!missing.length) continue;
    if (localTokens.has(ref.token)) continue;
    if (!ref.hasFallback) findings.push({ kind: 'undefined', token: ref.token, themes: missing, ref });
    else if (!allowed.has(ref.token)) findings.push({ kind: 'fallback-masked', token: ref.token, themes: missing, ref });
  }
  return findings;
}
