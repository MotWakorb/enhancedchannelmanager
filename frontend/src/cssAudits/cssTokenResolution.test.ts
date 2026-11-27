import { describe, expect, it } from 'vitest';
import { analyze, collect, extractVars, selectorThemes, stripComments } from './cssTokenResolution';

const run = (files: Record<string, string>, opts = {}) =>
  analyze(collect(Object.entries(files).map(([file, text]) => ({ file, text }))), opts);

const ROOT = ':root { --bg: #111; --fg: #eee; }\n';

describe('stripComments', () => {
  it('blanks comment text but keeps line numbers', () => {
    const out = stripComments('/* var(--x)\n */\n.a { color: red; }');
    expect(out).not.toContain('var(');
    expect(out.split('\n')).toHaveLength(3);
  });

  it('does not treat /* inside a string as a comment', () => {
    expect(stripComments('.a::before { content: "/* x */"; }')).toContain('"/* x */"');
  });

  it('ignores a var() that only appears inside a comment', () => {
    expect(run({ 'a.css': `${ROOT}/* var(--ghost) */ .a { color: var(--fg); }` })).toEqual([]);
  });
});

describe('selectorThemes', () => {
  it.each([
    ['.a', ['dark', 'light', 'high-contrast']],
    ['[data-theme="light"] .a', ['light']],
    ["[data-theme='high-contrast'] .a", ['high-contrast']],
    ['[data-theme=""] .a', ['dark']],
    [':root:not([data-theme="light"])', ['dark', 'high-contrast']],
  ])('%s', (sel, themes) => {
    expect(selectorThemes(sel)).toEqual(themes);
  });
});

describe('extractVars', () => {
  it('sees both the token and a var() nested in its fallback', () => {
    expect(extractVars('var(--a, var(--b))')).toEqual([
      { token: '--a', hasFallback: true },
      { token: '--b', hasFallback: false },
    ]);
  });
});

describe('analyze: undefined references', () => {
  it('flags a token defined nowhere when it has no fallback', () => {
    const f = run({ 'a.css': `${ROOT}.a { color: var(--ghost); }` });
    expect(f).toHaveLength(1);
    expect(f[0]).toMatchObject({ kind: 'undefined', token: '--ghost' });
    expect(f[0].themes).toEqual(['dark', 'light', 'high-contrast']);
    expect(f[0].ref).toMatchObject({ file: 'a.css', line: 2 });
  });

  it('accepts a token defined in :root for every theme', () => {
    expect(run({ 'a.css': `${ROOT}.a { color: var(--fg); }` })).toEqual([]);
  });

  it('accepts a token defined in :root and overridden in a theme', () => {
    const css = `${ROOT}[data-theme="light"] { --fg: #111; }\n.a { color: var(--fg); }`;
    expect(run({ 'a.css': css })).toEqual([]);
  });

  it('sees definitions and uses in different files', () => {
    expect(run({ 'index.css': ROOT, 'b.css': '.b { color: var(--fg); }' })).toEqual([]);
  });

  it('flags a reference inside another custom property definition', () => {
    const f = run({ 'a.css': ':root { --x: var(--ghost); }' });
    expect(f).toMatchObject([{ kind: 'undefined', token: '--ghost' }]);
  });
});

describe('analyze: theme-only definitions', () => {
  const lightOnly = `${ROOT}[data-theme="light"] { --drag: #000; }\n`;

  it('does not let a light-only definition satisfy a reference in dark and high-contrast', () => {
    const f = run({ 'a.css': `${lightOnly}.a { color: var(--drag); }` });
    expect(f).toHaveLength(1);
    expect(f[0]).toMatchObject({ kind: 'undefined', token: '--drag' });
    expect(f[0].themes).toEqual(['dark', 'high-contrast']);
  });

  it('accepts a light-only definition used only under a light selector', () => {
    const css = `${lightOnly}[data-theme="light"] .a { color: var(--drag); }`;
    expect(run({ 'a.css': css })).toEqual([]);
  });

  it('flags a use under a high-contrast selector when only light defines it', () => {
    const css = `${lightOnly}[data-theme="high-contrast"] .a { color: var(--drag); }`;
    const f = run({ 'a.css': css });
    expect(f).toHaveLength(1);
    expect(f[0].themes).toEqual(['high-contrast']);
  });

  it('accepts a token defined in each theme block separately', () => {
    const css = `[data-theme="light"] { --t: 1; }\n:root:not([data-theme="light"]) { --t: 2; }\n.a { color: var(--t); }`;
    expect(run({ 'a.css': css })).toEqual([]);
  });

  it('reports a theme-only token with a fallback as fallback-masked', () => {
    const f = run({ 'a.css': `${lightOnly}.a { color: var(--drag, #fff); }` });
    expect(f).toHaveLength(1);
    expect(f[0]).toMatchObject({ kind: 'fallback-masked', token: '--drag' });
    expect(f[0].themes).toEqual(['dark', 'high-contrast']);
  });
});

describe('analyze: fallback-masked references', () => {
  it('flags a fallback that is the only thing making an undefined token resolve', () => {
    const f = run({ 'a.css': `${ROOT}.a { color: var(--ghost, #f00); }` });
    expect(f).toMatchObject([{ kind: 'fallback-masked', token: '--ghost' }]);
  });

  it('does not flag a fallback on a token that is defined in every theme', () => {
    expect(run({ 'a.css': `${ROOT}.a { color: var(--fg, #f00); }` })).toEqual([]);
  });

  it('flags the undefined token nested in a fallback chain', () => {
    const f = run({ 'a.css': `${ROOT}.a { color: var(--fg, var(--ghost)); }` });
    expect(f).toMatchObject([{ kind: 'undefined', token: '--ghost' }]);
  });

  it('honours an explicit fallbackAllowed seam', () => {
    const css = `${ROOT}.a { color: var(--seam, #f00); }`;
    expect(run({ 'a.css': css }, { fallbackAllowed: new Set(['--seam']) })).toEqual([]);
  });
});

describe('analyze: local definitions and inline styles', () => {
  it('accepts a token declared on a component selector as a local seam', () => {
    const css = `${ROOT}.card { --gap: 4px; }\n.card .row { margin: var(--gap); }`;
    expect(run({ 'a.css': css })).toEqual([]);
  });

  it('collects var() from TSX and accepts inline custom properties', () => {
    const tsx = `const a = <div style={{ '--w': 1, color: 'var(--fg)', border: 'var(--ghost)' }} />;\n// var(--commented)\n`;
    const f = run({ 'index.css': ROOT, 'x.tsx': tsx });
    expect(f).toMatchObject([{ kind: 'undefined', token: '--ghost' }]);
    expect(f[0].ref.file).toBe('x.tsx');
  });

  it('handles selector lists with a theme-scoped member', () => {
    const css = `${ROOT}[data-theme="light"] .a, .b { color: var(--fg); }`;
    expect(run({ 'a.css': css })).toEqual([]);
  });

  it('treats @media as transparent for definitions', () => {
    const css = `@media (min-width: 1px) { :root { --m: 1; } }\n.a { width: var(--m); }`;
    expect(run({ 'a.css': css })).toEqual([]);
  });

  it('does not let a local declaration excuse a token that is global in only some themes', () => {
    const css = ':root { --b: 1; }\n[data-theme="light"] { --a: 2; }\n.y { --a: 5; }\n.x { color: var(--a); }';
    const f = run({ 'a.css': css });
    expect(f).toMatchObject([{ kind: 'undefined', token: '--a' }]);
    expect(f[0].themes).toEqual(['dark', 'high-contrast']);
  });

  it('does not let a theme-scoped local declaration excuse a partially defined token', () => {
    const css = ':root { --b: 1; }\n[data-theme="light"] { --a: 2; }\n[data-theme="light"] .card { --a: 5; }\n.x { color: var(--a); }';
    const f = run({ 'a.css': css });
    expect(f).toMatchObject([{ kind: 'undefined', token: '--a' }]);
    expect(f[0].themes).toEqual(['dark', 'high-contrast']);
  });
});

describe('analyze: nested var() forms', () => {
  it('reports an undefined token nested in the fallback of a fully defined token', () => {
    const f = run({ 'a.css': `${ROOT}.a { color: var(--fg, var(--zz)); }` });
    expect(f).toMatchObject([{ kind: 'undefined', token: '--zz' }]);
  });

  it('reports an undefined token inside calc()', () => {
    const f = run({ 'a.css': `${ROOT}.a { width: calc(var(--nope)); }` });
    expect(f).toMatchObject([{ kind: 'undefined', token: '--nope' }]);
  });
});
