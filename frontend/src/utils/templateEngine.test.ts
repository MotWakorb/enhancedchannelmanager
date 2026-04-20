/**
 * Mirror of backend/tests/unit/test_template_engine.py — every case here must
 * produce the same output on both sides so preview UI and backend rendering
 * never diverge.
 */
import { describe, it, expect } from 'vitest';

import {
  MAX_INPUT_LEN,
  MAX_REGEX_LEN,
  MAX_TEMPLATE_LEN,
  TemplateEngine,
  TemplateSyntaxError,
  render,
} from './templateEngine';

// ---------------------------------------------------------------------------
// Placeholders
// ---------------------------------------------------------------------------
describe('placeholders', () => {
  it('renders a single placeholder', () => {
    expect(render('Hello {name}', { name: 'World' })).toBe('Hello World');
  });

  it('renders multiple placeholders', () => {
    expect(render('{a} and {b}', { a: 'foo', b: 'bar' })).toBe('foo and bar');
  });

  it('renders missing groups as empty', () => {
    expect(render('x={missing}y', {})).toBe('x=y');
  });

  it('passes plain text through unchanged', () => {
    expect(render('no braces here', {})).toBe('no braces here');
  });

  it('returns empty for empty template', () => {
    expect(render('', { anything: 'x' })).toBe('');
  });

  it('returns static text when no placeholders', () => {
    expect(render('static text', { ignored: 'x' })).toBe('static text');
  });
});

// ---------------------------------------------------------------------------
// Legacy {name_normalize}
// ---------------------------------------------------------------------------
describe('legacy _normalize suffix', () => {
  it('strips non-alphanumeric and lowercases', () => {
    expect(render('{name_normalize}', { name: 'ESPN 2 (HD)' })).toBe('espn2hd');
  });

  it('passes clean values through unchanged', () => {
    expect(render('{city_normalize}', { city: 'seattle' })).toBe('seattle');
  });

  it('returns empty for empty group', () => {
    expect(render('{name_normalize}', { name: '' })).toBe('');
  });
});

// ---------------------------------------------------------------------------
// Pipe transforms
// ---------------------------------------------------------------------------
describe('pipe transforms', () => {
  it('uppercase', () => {
    expect(render('{name|uppercase}', { name: 'hello' })).toBe('HELLO');
  });

  it('lowercase', () => {
    expect(render('{name|lowercase}', { name: 'HELLO' })).toBe('hello');
  });

  it('titlecase', () => {
    expect(render('{name|titlecase}', { name: 'the espn network' })).toBe('The Espn Network');
  });

  it('trim', () => {
    expect(render('{name|trim}', { name: '  hello  ' })).toBe('hello');
  });

  it('strip with single char arg', () => {
    expect(render('{name|strip:-}', { name: '--hello--' })).toBe('hello');
  });

  it('strip with multiple chars', () => {
    expect(render('{name|strip:-_}', { name: '_-hello-_' })).toBe('hello');
  });

  it('replace', () => {
    expect(render('{name|replace:foo:bar}', { name: 'foo-world' })).toBe('bar-world');
  });

  it('replace with empty destination removes all occurrences', () => {
    expect(render('{name|replace:x:}', { name: 'xaxbxc' })).toBe('abc');
  });

  it('normalize as pipe', () => {
    expect(render('{name|normalize}', { name: 'ESPN 2 (HD)' })).toBe('espn2hd');
  });

  it('chained pipes apply left to right', () => {
    expect(render('{name|strip:-|trim|uppercase}', { name: '-- hello --' })).toBe('HELLO');
  });

  it('unknown transform throws', () => {
    expect(() => render('{name|bogus}', { name: 'x' })).toThrow(TemplateSyntaxError);
  });

  it('pipe on missing group returns empty', () => {
    expect(render('{missing|uppercase}', {})).toBe('');
  });
});

// ---------------------------------------------------------------------------
// Lookup tables
// ---------------------------------------------------------------------------
describe('lookups', () => {
  it('resolves key to value', () => {
    const lookups = { callsigns: { ESPN: 'espn.com' } };
    expect(render('{name|lookup:callsigns}', { name: 'ESPN' }, lookups)).toBe('espn.com');
  });

  it('falls back to input when key missing', () => {
    const lookups = { callsigns: { ESPN: 'espn.com' } };
    expect(render('{name|lookup:callsigns}', { name: 'UNKNOWN' }, lookups)).toBe('UNKNOWN');
  });

  it('unknown table throws', () => {
    expect(() => render('{name|lookup:none}', { name: 'ESPN' }, {})).toThrow(TemplateSyntaxError);
  });

  it('chained after transform', () => {
    const lookups = { stations: { ESPN: 'Entertainment Sports Programming Network' } };
    expect(
      render('{name|uppercase|lookup:stations}', { name: 'espn' }, lookups),
    ).toBe('Entertainment Sports Programming Network');
  });

  it('no lookups dict throws', () => {
    expect(() => render('{name|lookup:callsigns}', { name: 'ESPN' })).toThrow(TemplateSyntaxError);
  });
});

// ---------------------------------------------------------------------------
// Conditionals
// ---------------------------------------------------------------------------
describe('conditionals', () => {
  it('renders body when group non-empty', () => {
    expect(render('{if:city}City: {city}{/if}', { city: 'Seattle' })).toBe('City: Seattle');
  });

  it('omits body when group empty', () => {
    expect(render('{if:city}City: {city}{/if}', { city: '' })).toBe('');
  });

  it('omits body when group missing', () => {
    expect(render('{if:city}City: {city}{/if}', {})).toBe('');
  });

  it('equality match', () => {
    expect(render('{if:type=sports}SPORTS{/if}', { type: 'sports' })).toBe('SPORTS');
  });

  it('equality no match', () => {
    expect(render('{if:type=sports}SPORTS{/if}', { type: 'news' })).toBe('');
  });

  it('regex match', () => {
    expect(render('{if:channel~^ESPN}espn channel{/if}', { channel: 'ESPN2' })).toBe('espn channel');
  });

  it('regex no match', () => {
    expect(render('{if:channel~^ESPN}espn channel{/if}', { channel: 'CNN' })).toBe('');
  });

  it('invalid regex evaluates false', () => {
    expect(render('{if:channel~[unclosed}body{/if}', { channel: 'ESPN' })).toBe('');
  });

  it('conditional body resolves placeholders and pipes', () => {
    expect(render('{if:city}Located in {city|uppercase}.{/if}', { city: 'denver' })).toBe('Located in DENVER.');
  });

  it('nested conditionals', () => {
    const tpl = '{if:a}A:{a}{if:b}-B:{b}{/if}{/if}';
    expect(render(tpl, { a: '1', b: '2' })).toBe('A:1-B:2');
    expect(render(tpl, { a: '1' })).toBe('A:1');
    expect(render(tpl, { b: '2' })).toBe('');
  });

  it('conditional without close throws', () => {
    expect(() => render('{if:city}unclosed', { city: 'x' })).toThrow(TemplateSyntaxError);
  });

  it('unmatched close throws', () => {
    expect(() => render('no open{/if}', {})).toThrow(TemplateSyntaxError);
  });
});

// ---------------------------------------------------------------------------
// Guards
// ---------------------------------------------------------------------------
describe('guards', () => {
  it('rejects oversized template', () => {
    const big = 'x'.repeat(MAX_TEMPLATE_LEN + 1);
    expect(() => render(big, {})).toThrow(TemplateSyntaxError);
  });

  it('truncates oversized group values', () => {
    const bigValue = 'a'.repeat(MAX_INPUT_LEN + 100);
    const out = render('{x|uppercase}', { x: bigValue });
    expect(out.length).toBeLessThanOrEqual(MAX_INPUT_LEN);
  });

  it('oversized regex in conditional evaluates false', () => {
    const hugeRegex = '(' + 'a?'.repeat(MAX_REGEX_LEN / 2 + 10) + ')' + 'a'.repeat(20);
    const tpl = '{if:x~' + hugeRegex + '}match{/if}';
    expect(render(tpl, { x: 'a'.repeat(20) })).toBe('');
  });
});

// ---------------------------------------------------------------------------
// renderWithTrace — must mirror Python render_with_trace shape
// ---------------------------------------------------------------------------
describe('renderWithTrace', () => {
  it('emits a literal entry for plain text', () => {
    const engine = new TemplateEngine();
    const { output, trace } = engine.renderWithTrace('hello world', {});
    expect(output).toBe('hello world');
    expect(trace).toEqual([{ kind: 'literal', text: 'hello world' }]);
  });

  it('records each pipe input/output under a placeholder entry', () => {
    const engine = new TemplateEngine();
    const { output, trace } = engine.renderWithTrace('{name|uppercase|trim}', { name: '  hi  ' });
    expect(output).toBe('HI');
    const placeholder = trace.find((t) => t.kind === 'placeholder');
    expect(placeholder).toBeTruthy();
    if (placeholder && placeholder.kind === 'placeholder') {
      expect(placeholder.group_name).toBe('name');
      expect(placeholder.initial_value).toBe('  hi  ');
      expect(placeholder.final_value).toBe('HI');
      expect(placeholder.pipes.map((p) => p.transform)).toEqual(['uppercase', 'trim']);
      expect(placeholder.pipes[0]).toMatchObject({ input: '  hi  ', output: '  HI  ' });
      expect(placeholder.pipes[1]).toMatchObject({ input: '  HI  ', output: 'HI' });
    }
  });

  it('annotates lookup hits with matched=true', () => {
    const engine = new TemplateEngine();
    const { output, trace } = engine.renderWithTrace(
      '{code|lookup:countries}',
      { code: 'US' },
      { countries: { US: 'USA' } },
    );
    expect(output).toBe('USA');
    const placeholder = trace[0];
    if (placeholder.kind !== 'placeholder') throw new Error('unexpected trace shape');
    const pipe = placeholder.pipes[0];
    expect(pipe.transform).toBe('lookup');
    expect(pipe.source).toBe('countries');
    expect(pipe.matched).toBe(true);
  });

  it('annotates lookup misses with matched=false', () => {
    const engine = new TemplateEngine();
    const { output, trace } = engine.renderWithTrace(
      '{code|lookup:countries}',
      { code: 'ZZ' },
      { countries: { US: 'USA' } },
    );
    expect(output).toBe('ZZ');
    const placeholder = trace[0];
    if (placeholder.kind !== 'placeholder') throw new Error('unexpected trace shape');
    expect(placeholder.pipes[0].matched).toBe(false);
  });

  it('conditional taken=true includes body trace', () => {
    const engine = new TemplateEngine();
    const { output, trace } = engine.renderWithTrace(
      '{if:sport=nfl}Go {team|uppercase}{/if}',
      { sport: 'nfl', team: 'chiefs' },
    );
    expect(output).toBe('Go CHIEFS');
    const cond = trace.find((t) => t.kind === 'conditional');
    expect(cond).toBeTruthy();
    if (cond && cond.kind === 'conditional') {
      expect(cond.taken).toBe(true);
      expect(cond.kind_detail).toBe('equality');
      expect(cond.body.length).toBeGreaterThan(0);
    }
  });

  it('conditional taken=false has empty body', () => {
    const engine = new TemplateEngine();
    const { trace } = engine.renderWithTrace(
      '{if:sport=nba}never{/if}',
      { sport: 'nfl' },
    );
    const cond = trace.find((t) => t.kind === 'conditional');
    if (cond && cond.kind === 'conditional') {
      expect(cond.taken).toBe(false);
      expect(cond.body).toEqual([]);
    }
  });
});

// ---------------------------------------------------------------------------
// Engine class
// ---------------------------------------------------------------------------
describe('TemplateEngine class', () => {
  it('is reusable across renders', () => {
    const engine = new TemplateEngine();
    expect(engine.render('{x|uppercase}', { x: 'a' })).toBe('A');
    expect(engine.render('{y|lowercase}', { y: 'B' })).toBe('b');
  });

  it('uses instance-level lookups', () => {
    const engine = new TemplateEngine({ flags: { usa: '🇺🇸' } });
    expect(engine.render('{country|lookup:flags}', { country: 'usa' })).toBe('🇺🇸');
  });

  it('render-level lookups override instance lookups', () => {
    const engine = new TemplateEngine({ tbl: { a: 'from-instance' } });
    const out = engine.render('{k|lookup:tbl}', { k: 'a' }, { tbl: { a: 'from-call' } });
    expect(out).toBe('from-call');
  });
});
