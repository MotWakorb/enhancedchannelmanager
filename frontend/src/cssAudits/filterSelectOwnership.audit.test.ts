/// <reference types="node" />
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = path.resolve(__dirname, '..');
const OWNERS = [
  ['components/StreamsPane.tsx', 'streams-filter-select', 2],
  ['components/tabs/JournalTab.tsx', 'journal-filter-select', 3],
  ['components/tabs/M3UChangesTab.tsx', 'm3u-changes-filter-select', 4],
] as const;

const legacyToken = /(?:^|[^A-Za-z0-9_-])filter-select(?:$|[^A-Za-z0-9_-])/;

function productionFiles(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const item = path.join(dir, entry.name);
    if (entry.isDirectory()) return productionFiles(item);
    return /\.(?:css|tsx?)$/.test(entry.name) && !entry.name.includes('.test.') ? [item] : [];
  });
}

describe('filter select ownership', () => {
  it('has no production use or declaration of the legacy shared token', () => {
    const offenders = productionFiles(SRC)
      .filter((file) => legacyToken.test(fs.readFileSync(file, 'utf8')))
      .map((file) => path.relative(SRC, file));
    expect(offenders).toEqual([]);
  });

  it.each(OWNERS)('%s owns exactly $2 controls through $1', (file, className, count) => {
    const source = fs.readFileSync(path.join(SRC, file), 'utf8');
    expect(source.match(new RegExp(`className=["']${className}["']`, 'g')) ?? []).toHaveLength(count);
  });

  it('pins the complete M3U state and responsive contract', () => {
    const css = fs.readFileSync(path.join(SRC, 'components/tabs/M3UChangesTab.css'), 'utf8').replace(/\s+/g, ' ');
    expect(css).toContain('.m3u-changes-filter-select .custom-select-trigger:hover { color: var(--button-text); }');
    expect(css).toContain('.m3u-changes-filter-select .custom-select.open .custom-select-trigger { box-shadow: none; }');
    expect(css).toMatch(/@media \(max-width: 768px\).*?\.m3u-changes-filter-select \{ flex: 1 1 auto; min-width: 150px; \}/);
    expect(css).toMatch(/@media \(max-width: 600px\).*?\.m3u-changes-filter-select \{ width: 100%; \}/);
  });

  it('pins Journal responsive ownership', () => {
    const css = fs.readFileSync(path.join(SRC, 'components/tabs/JournalTab.css'), 'utf8').replace(/\s+/g, ' ');
    expect(css).toMatch(/@media \(max-width: 600px\).*?\.journal-filter-select \{ width: 100%; \}/);
  });
});
