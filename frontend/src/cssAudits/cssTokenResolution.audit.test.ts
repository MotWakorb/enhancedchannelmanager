/// <reference types="node" />
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { analyze, collect, type Finding } from './cssTokenResolution';
import { FALLBACK_MASKED_BASELINE } from './cssTokenFallbackBaseline';

const SRC = path.resolve(__dirname, '..');

function productionFiles(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const item = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === 'test' || entry.name === 'cssAudits' ? [] : productionFiles(item);
    return /\.(?:css|tsx?)$/.test(entry.name) && !/\.(?:test|spec)\./.test(entry.name) ? [item] : [];
  });
}

const findings: Finding[] = analyze(
  collect(
    productionFiles(SRC).map((f) => ({
      file: path.relative(SRC, f).split(path.sep).join('/'),
      text: fs.readFileSync(f, 'utf8'),
    })),
  ),
);

const describeFinding = (f: Finding) =>
  `${f.ref.file}:${f.ref.line} ${f.token} unresolved in [${f.themes.join(', ')}] (${f.kind})`;

describe('CSS custom property resolution, per theme', () => {
  it('has no var() reference without a fallback that is undefined in a theme where it applies', () => {
    expect(findings.filter((f) => f.kind === 'undefined').map(describeFinding)).toEqual([]);
  });

  it('has no new fallback-masked reference (ratchet: counts per file and token)', () => {
    const actual: Record<string, number> = {};
    for (const f of findings.filter((x) => x.kind === 'fallback-masked')) {
      const key = `${f.ref.file}|${f.token}`;
      actual[key] = (actual[key] ?? 0) + 1;
    }
    const grown = Object.keys(actual).filter((k) => actual[k] > (FALLBACK_MASKED_BASELINE[k] ?? 0));
    expect(
      grown.map((k) => `${k}: ${actual[k]} > baseline ${FALLBACK_MASKED_BASELINE[k] ?? 0}. Define the token in :root and every theme, or use a defined one.`),
    ).toEqual([]);
  });

  it('has no stale baseline entry (a fixed site must be deleted from the baseline)', () => {
    const actual: Record<string, number> = {};
    for (const f of findings.filter((x) => x.kind === 'fallback-masked')) {
      const key = `${f.ref.file}|${f.token}`;
      actual[key] = (actual[key] ?? 0) + 1;
    }
    const stale = Object.keys(FALLBACK_MASKED_BASELINE).filter((k) => (actual[k] ?? 0) < FALLBACK_MASKED_BASELINE[k]);
    expect(stale.map((k) => `${k}: baseline ${FALLBACK_MASKED_BASELINE[k]}, now ${actual[k] ?? 0}. Lower or delete the entry.`)).toEqual([]);
  });
});
