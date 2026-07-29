/// <reference types="node" />
/**
 * Regression guard for the cross-chunk shared-class leak.
 *
 * THE DEFECT (repaired five times, recurred five times: `.list-header`
 * (bead qlc4h), `.status-label` (f4yc7), `.group-count` (sccol),
 * `.action-btn .material-icons`, and `.action-btn`'s box geometry):
 *
 *   A page CSS file redeclares a class that `shared/common.css` already
 *   owns, with no ancestor scoping, at equal specificity. Every route tab
 *   is a lazily-imported chunk whose stylesheet is appended to <head> on
 *   first visit and NEVER removed. `common.css` is emitted last inside the
 *   EAGER bundle (byte 263,255 of 263,419), so it beats every other eager
 *   file — but it loses permanently to any bare rule in any tab chunk the
 *   user has visited. The winner therefore depends on which tabs were
 *   visited and in what order, which is why the bug reappears as "the icons
 *   are the wrong size but only sometimes".
 *
 * WHAT THIS CHECKS: the same normalised selector, under the same at-rule
 * context, declaring a property this guard covers, in TWO DIFFERENT CHUNKS.
 *
 * "Bare" is operationalised as "identical selector text". That is exact for
 * this defect class and needs no hardcoded list of page-scope roots:
 *   - `.action-btn` in common.css vs `.action-btn` in LogoManagerTab.css
 *     -> identical text, different chunks -> FAIL (the real bug).
 *   - `.action-btn` in common.css vs `.logo-manager-tab .action-btn`
 *     -> different text AND higher specificity -> pass (correctly scoped).
 * It also catches descendant selectors like `.action-btn .material-icons`,
 * which a naive "selector contains no combinator" rule would miss — that
 * was instance #4.
 *
 * This property — that scoping a rule makes it a different key, and
 * therefore invisible to the check — is why covering a high-traffic
 * property like `display` costs almost nothing in false positives. Every
 * page-scoped `display` in the tree is already a distinct selector.
 *
 * CHUNK MEMBERSHIP IS DERIVED, NEVER HARDCODED, so a page added next year is
 * covered automatically. See `deriveChunks()` for the model and the
 * tradeoff against reading the built output.
 *
 * ---------------------------------------------------------------------------
 * TWO TIERS, TWO BASELINES — deliberate, see bead enhancedchannelmanager-6z299.6
 *
 * TIER 1 (typography): unbaselined. Every violation is reported. It is RED on
 * purpose while the P1 type-scale consolidation burns it down, and its count
 * is the metric that sweep is tracked against. Do not baseline it, do not
 * add exceptions to it, do not narrow its property list.
 *
 * TIER 2 (layout + visual): baselined against `KNOWN_CROSS_CHUNK_LAYOUT`
 * below. This tier was added after tier 1 and found 49 pre-existing
 * collisions that no wave of the consolidation owns end-to-end. Reporting all
 * 49 as a second permanent failure would have added no regression signal —
 * a check that is always red cannot tell you that a 50th appeared. So the 49
 * are recorded explicitly, with the bead that retires each, and the assertion
 * fails on:
 *   - any collision NOT in the baseline           (a new leak — the point)
 *   - any baseline entry whose collision is GONE  (stale — delete the line)
 *   - any baseline entry whose CHUNK SET moved    (partly fixed, or spread)
 * so the list cannot rot and cannot silently grow.
 *
 * WHEN EITHER FAILS: do not scope-and-move-on and do not add a baseline entry
 * for new work. Delete the page copy and let the shared layer own the class
 * (leaving the pointer comment behind), or scope the page copy to a page-root
 * ancestor so it can no longer leak. See docs/css_guidelines.md § Load order.
 *
 * NOTE ON `postcss`: declared as an explicit devDependency of
 * frontend/package.json (previously resolved only transitively through
 * vite, which was fragile — a vite upgrade could have dropped it and broken
 * this guard with a confusing error).
 */
import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import * as ts from 'typescript';
import { parse, type Rule, type AtRule, type Declaration } from 'postcss';

// vitest runs with frontend/ as cwd (package.json "test" script).
const SRC = path.resolve(process.cwd(), 'src');
const ENTRY = path.join(SRC, 'main.tsx');

const TYPOGRAPHY = ['font-size', 'font-weight', 'line-height', 'letter-spacing', 'text-transform'];

/**
 * TIER 2 property list. The line drawn here, and why:
 *
 * IN — a cross-chunk flip of any of these changes what the user sees, in a
 * way a screenshot diff would catch and a reviewer would call a bug:
 *   box geometry   the `.action-btn` defect exactly (32x32 vs padding:.375rem)
 *   layout mode    display/position/flex/grid/gap — a flip restructures the box
 *   paint          color/background/border/radius/shadow/opacity
 *   text layout    text-align/white-space/text-overflow/overflow — truncation
 *   cursor         an affordance flip (pointer vs default) is a real defect
 *
 * OUT — measured on this tree before excluding, not assumed:
 *   transition/animation (3 collisions) and transform (0): motion only. A
 *     duplicated `transition` differing in easing is not a defect anyone
 *     would file, and these are the highest-churn declarations in the tree.
 *     Excluding them costs zero coverage today: all 3 sit on selectors
 *     (.drag-handle, .settings-btn, .group-item) this tier already catches
 *     via other properties.
 *   font-family, text-decoration, vertical-align, box-sizing, visibility,
 *     content (0 collisions each): no signal to buy, and every property
 *     added lengthens the failure message a reader has to parse.
 * Re-measure before adding any of them; do not add on intuition.
 */
const LAYOUT_VISUAL = [
  // box geometry
  'width', 'height', 'min-width', 'min-height', 'max-width', 'max-height',
  'padding', 'padding-top', 'padding-right', 'padding-bottom', 'padding-left', 'padding-inline', 'padding-block',
  'margin', 'margin-top', 'margin-right', 'margin-bottom', 'margin-left', 'margin-inline', 'margin-block',
  // layout mode
  'display', 'position', 'top', 'right', 'bottom', 'left', 'z-index', 'float', 'order',
  'flex', 'flex-direction', 'flex-wrap', 'flex-grow', 'flex-shrink', 'flex-basis',
  'gap', 'row-gap', 'column-gap',
  'align-items', 'align-self', 'align-content', 'justify-content', 'justify-items', 'justify-self',
  'grid-template-columns', 'grid-template-rows', 'grid-template-areas',
  'grid-column', 'grid-row', 'grid-area', 'grid-auto-flow', 'grid-auto-rows', 'grid-auto-columns',
  // paint
  'color', 'background', 'background-color', 'background-image',
  'border', 'border-width', 'border-style', 'border-color',
  'border-top', 'border-right', 'border-bottom', 'border-left', 'border-radius',
  'box-shadow', 'opacity',
  // text layout / affordance
  'overflow', 'overflow-x', 'overflow-y', 'text-align', 'white-space', 'text-overflow', 'cursor',
];

// --- module graph -----------------------------------------------------------

function resolveSpec(from: string, spec: string): string | null {
  if (!spec.startsWith('.')) return null; // bare specifier -> node_modules, irrelevant
  const base = path.resolve(path.dirname(from), spec);
  for (const suffix of ['', '.ts', '.tsx', '.js', '.jsx', '/index.ts', '/index.tsx', '/index.js']) {
    if (fs.existsSync(base + suffix) && fs.statSync(base + suffix).isFile()) return base + suffix;
  }
  return null;
}

const edgeCache = new Map<string, { statik: string[]; dynamic: string[] }>();
function edges(file: string): { statik: string[]; dynamic: string[] } {
  const hit = edgeCache.get(file);
  if (hit) return hit;
  const out: { statik: string[]; dynamic: string[] } = { statik: [], dynamic: [] };
  if (/\.css$/.test(file)) {
    edgeCache.set(file, out);
    return out;
  }
  const sf = ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const visit = (node: ts.Node): void => {
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) {
      // `import type {...}` is erased by the compiler and creates no chunk edge.
      const typeOnly = ts.isImportDeclaration(node) ? node.importClause?.isTypeOnly : node.isTypeOnly;
      const target = typeOnly ? null : resolveSpec(file, node.moduleSpecifier.text);
      if (target) out.statik.push(target);
    } else if (
      ts.isCallExpression(node) &&
      node.expression.kind === ts.SyntaxKind.ImportKeyword &&
      node.arguments[0] &&
      ts.isStringLiteral(node.arguments[0])
    ) {
      // A real dynamic import() call. `Promise<import('../types').Foo>` is an
      // ImportTypeNode, not a CallExpression, so it is correctly ignored — it
      // would otherwise register src/types/index.ts as a bogus chunk root.
      const target = resolveSpec(file, (node.arguments[0] as ts.StringLiteral).text);
      if (target) out.dynamic.push(target);
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  edgeCache.set(file, out);
  return out;
}

function reachStatic(root: string): Set<string> {
  const seen = new Set<string>();
  const stack = [root];
  while (stack.length) {
    const cur = stack.pop()!;
    if (seen.has(cur)) continue;
    seen.add(cur);
    stack.push(...edges(cur).statik);
  }
  return seen;
}

/**
 * Map every reachable CSS file to the bundle chunk that will carry it.
 *
 * Model: Rollup's default code-splitting assigns a module to a chunk keyed by
 * the SET of entry points (the static entry plus every dynamic-import target)
 * that statically reach it. Anything the static entry reaches ships with the
 * initial paint, in a deterministic order, so all of it collapses to one
 * "EAGER" bucket — that is the load-order unit the defect cares about.
 * Everything else is keyed by the set of lazy roots that reach it, which is
 * why e.g. DenseToolbar.css (shared by five tabs) is correctly its own chunk
 * rather than being attributed to any one tab.
 *
 * WHY THE IMPORT GRAPH AND NOT THE BUILD OUTPUT: `frontend/dist/assets/` is
 * ground truth, but it requires a build to exist, it is gitignored, and its
 * chunk files are minified with no source-file boundaries, so mapping a
 * violation back to a file:line needs sourcemaps. The graph needs no build,
 * runs in the normal vitest gate, and points straight at the offending line.
 * The model was validated against a real build: it reproduces every chunk in
 * dist/assets/*.css, including the four shared-across-lazy-roots chunks
 * (DenseToolbar, OverflowMenu, GroupMultiSelectDropdown, StickySectionNav),
 * and the two CSS files it reports as unreachable from the entry
 * (ChannelDetail.css, DummyEPGChannelPicker.css -- their components are
 * imported by nothing) are correspondingly absent from every built chunk.
 * Unreachable files are skipped: nothing loads them, so they cannot leak.
 */
function deriveChunks(): Map<string, string> {
  const universe = new Set<string>();
  const lazyRoots = new Set<string>();
  const stack = [ENTRY];
  while (stack.length) {
    const cur = stack.pop()!;
    if (universe.has(cur)) continue;
    universe.add(cur);
    const e = edges(cur);
    stack.push(...e.statik);
    for (const d of e.dynamic) {
      lazyRoots.add(d);
      stack.push(d);
    }
  }

  const eager = reachStatic(ENTRY);
  const lazyReach = [...lazyRoots].map((r) => [path.basename(r).replace(/\.tsx?$/, ''), reachStatic(r)] as const);

  const chunks = new Map<string, string>();
  for (const css of [...universe].filter((f) => f.endsWith('.css'))) {
    chunks.set(
      css,
      eager.has(css) ? 'EAGER' : lazyReach.filter(([, r]) => r.has(css)).map(([n]) => n).sort().join('+') || 'UNREACHED'
    );
  }
  return chunks;
}

// --- CSS declarations -------------------------------------------------------

interface Decl {
  file: string;
  line: number;
  chunk: string;
  props: string[];
  /** prop -> declared value, for the value-divergence annotation on tier 2. */
  values: Map<string, string>;
}

/** Whitespace/combinator normalisation so `.a>.b` and `.a > .b` are one key. */
const normalize = (sel: string): string => sel.replace(/\s*([>+~])\s*/g, ' $1 ').replace(/\s+/g, ' ').trim();

/**
 * Index every reachable CSS file once, into one map per tier. Both tiers key
 * on (at-rule context, normalised selector); a rule lands in a tier's map only
 * if it declares at least one property that tier covers.
 */
function buildIndex(): { typography: Map<string, Decl[]>; layout: Map<string, Decl[]> } {
  const typography = new Map<string, Decl[]>();
  const layout = new Map<string, Decl[]>();
  const typoProps = new Set(TYPOGRAPHY);
  const layoutProps = new Set(LAYOUT_VISUAL);

  for (const [cssFile, chunk] of deriveChunks()) {
    if (chunk === 'UNREACHED') continue; // no module imports it, so it never loads
    const root = parse(fs.readFileSync(cssFile, 'utf8'), { from: cssFile });
    root.walkRules((rule: Rule) => {
      const decls = rule.nodes.filter((n): n is Declaration => n.type === 'decl');
      if (decls.length === 0) return;
      // At-rule context is part of the identity: two `@media (max-width:900px)`
      // blocks collide with each other, not with the unconditional rule.
      const ctx: string[] = [];
      for (let p = rule.parent; p && p.type === 'atrule'; p = p.parent) ctx.unshift(`@${(p as AtRule).name} ${(p as AtRule).params}`);

      for (const [props, into] of [
        [typoProps, typography],
        [layoutProps, layout],
      ] as const) {
        const matched = decls.filter((d) => props.has(d.prop.toLowerCase()));
        if (matched.length === 0) continue;
        const decl: Omit<Decl, 'chunk'> = {
          file: path.relative(SRC, cssFile),
          line: rule.source?.start?.line ?? 0,
          props: matched.map((d) => d.prop.toLowerCase()),
          values: new Map(matched.map((d) => [d.prop.toLowerCase(), d.value.trim()])),
        };
        for (const sel of rule.selectors) {
          const key = [...ctx, normalize(sel)].join(' :: ');
          if (!into.has(key)) into.set(key, []);
          into.get(key)!.push({ ...decl, chunk });
        }
      }
    });
  }
  return { typography, layout };
}

let indexCache: ReturnType<typeof buildIndex> | null = null;
const cssIndex = (): ReturnType<typeof buildIndex> => (indexCache ??= buildIndex());

// --- tier 2 baseline --------------------------------------------------------

interface BaselineEntry {
  /** Exactly the key this file reports: `[@at-rule ... :: ]<normalised selector>`. */
  selector: string;
  /** Sorted chunk names the collision spans today. Drift here fails the check. */
  chunks: string[];
  /** The bead that deletes this entry. Never blank. */
  bead: string;
  /** Sites, so a reader does not have to re-run the audit to see the damage. */
  where: string;
}

/**
 * The 49 layout/visual cross-chunk collisions that pre-date this tier
 * (measured 2026-07-28 on branch `newui`). Each line is debt with an owner.
 *
 * TO REMOVE A LINE: fix the collision (delete the page copy, or scope it to a
 * page-root ancestor). The check then fails with "no longer collides" and you
 * delete the line. That is the intended workflow — the list shrinks as the
 * consolidation lands and CANNOT be left behind, because a stale entry is a
 * failure.
 *
 * DO NOT ADD A LINE for new work. A new collision is the defect this file
 * exists to stop.
 *
 * `bead: NEEDS-TRIAGE` = found by this tier, owned by no wave of the P1
 * consolidation. `UNTRIAGED_BUDGET` below caps how many may exist so the
 * bucket can only shrink.
 */
const KNOWN_CROSS_CHUNK_LAYOUT: BaselineEntry[] = [
  // --- Wave 1 (6z299.2) — § 5 scoping table already names these selectors ---
  { selector: '.settings-btn', chunks: ['EAGER', 'M3UManagerTab'], bead: 'enhancedchannelmanager-6z299.2', where: 'M3UGroupsModal.css:210 | App.css:95 — divergent border, padding, color, border-radius' },

  // --- Wave 0 (6z299.1) — § 4.3 hoists / § 4.4 deletes these outright ---
  // § 27 FILTER BAR absorbs the whole Journal/M3U-Changes header block.
  { selector: '.filter-select', chunks: ['EAGER', 'JournalTab', 'M3UChangesTab'], bead: 'enhancedchannelmanager-6z299.1', where: 'JournalTab.css:111 | M3UChangesTab.css:160 | StreamsPane.css:194 — divergent width; § 4.3 flags the StreamsPane copy for verification' },
  { selector: '.filter-select .custom-select', chunks: ['JournalTab', 'M3UChangesTab'], bead: 'enhancedchannelmanager-6z299.1', where: 'JournalTab.css:116 | M3UChangesTab.css:165' },
  { selector: '.filters-bar', chunks: ['JournalTab', 'M3UChangesTab'], bead: 'enhancedchannelmanager-6z299.1', where: 'JournalTab.css:96 | M3UChangesTab.css:153' },
  { selector: '.header-actions', chunks: ['EAGER', 'JournalTab', 'M3UChangesTab', 'StatsTab'], bead: 'enhancedchannelmanager-6z299.1', where: 'common.css:1141 | StatsTab.css:196 | JournalTab.css:65 | M3UChangesTab.css:68' },
  { selector: '.header-left', chunks: ['JournalTab', 'M3UChangesTab', 'StatsTab'], bead: 'enhancedchannelmanager-6z299.1', where: 'StatsTab.css:33 | JournalTab.css:26 | M3UChangesTab.css:34 — divergent row-gap' },
  { selector: '.header-stats', chunks: ['JournalTab', 'M3UChangesTab'], bead: 'enhancedchannelmanager-6z299.1', where: 'JournalTab.css:41 | M3UChangesTab.css:49' },
  { selector: '@media (max-width: 600px) :: .filter-select', chunks: ['JournalTab', 'M3UChangesTab'], bead: 'enhancedchannelmanager-6z299.1', where: 'JournalTab.css:483 | M3UChangesTab.css:527' },
  { selector: '@media (max-width: 600px) :: .filters-bar', chunks: ['JournalTab', 'M3UChangesTab'], bead: 'enhancedchannelmanager-6z299.1', where: 'JournalTab.css:475 | M3UChangesTab.css:523' },
  { selector: '@media (max-width: 600px) :: .header-actions', chunks: ['JournalTab', 'LogoManagerTab', 'M3UChangesTab'], bead: 'enhancedchannelmanager-6z299.1', where: 'JournalTab.css:471 | M3UChangesTab.css:511 | LogoManagerTab.css:456' },
  { selector: '@media (max-width: 768px) :: .filters-bar', chunks: ['JournalTab', 'M3UChangesTab'], bead: 'enhancedchannelmanager-6z299.1', where: 'JournalTab.css:455 | M3UChangesTab.css:495' },
  // § 28 METRIC TILE absorbs Pipeline's and Stats' shared tile.
  { selector: '.stat-item', chunks: ['ChannelPipelineTab', 'StatsTab'], bead: 'enhancedchannelmanager-6z299.1', where: 'ChannelPipelineTab.css:48 | StatsTab.css:507' },
  // § 4.4 rogue-duplicate deletions.
  { selector: '.btn-danger', chunks: ['ChannelPipelineTab', 'EAGER'], bead: 'enhancedchannelmanager-6z299.1', where: 'common.css:173 | RuleBuilder.css:387 — same family as the .btn-primary/.btn-secondary deletions at RuleBuilder.css:385,395' },
  { selector: '.search-box', chunks: ['EAGER', 'SettingsTab'], bead: 'enhancedchannelmanager-6z299.1', where: 'common.css:882 | TagEngineSection.css:23 — divergent padding, border' },
  { selector: '.status-disabled', chunks: ['EAGER', 'SettingsTab'], bead: 'enhancedchannelmanager-6z299.1', where: 'common.css:477 | CloudTargetsCard.css:125 — § 4.2 retokenises the common.css side; the CloudTargetsCard copy must go with it' },

  // --- NEEDS-TRIAGE: found by this tier, owned by no wave (see UNTRIAGED_BUDGET) ---
  { selector: '.checkbox-group', chunks: ['ChannelPipelineTab', 'EAGER'], bead: 'NEEDS-TRIAGE', where: 'common.css:1497 | RuleBuilder.css:141 — divergent gap' },
  { selector: '.current-logo-preview', chunks: ['EAGER', 'LogoManagerTab'], bead: 'NEEDS-TRIAGE', where: 'LogoModal.css:184 | ChannelsPane.css:3253 — divergent margin-bottom' },
  { selector: '.details-grid', chunks: ['JournalTab', 'M3UChangesTab', 'StatsTab'], bead: 'NEEDS-TRIAGE', where: 'StatsTab.css:860 | JournalTab.css:370 | M3UChangesTab.css:376 — divergent grid-template-columns' },
  { selector: '.drag-handle', chunks: ['EAGER', 'EPGManagerTab'], bead: 'NEEDS-TRIAGE', where: 'common.css:780 | EPGManagerTab.css:99 | StreamsPane.css:344 — divergent color' },
  { selector: '.drag-handle:active', chunks: ['EAGER', 'EPGManagerTab'], bead: 'NEEDS-TRIAGE', where: 'common.css:792 | EPGManagerTab.css:112' },
  { selector: '.drag-handle:hover', chunks: ['EAGER', 'EPGManagerTab'], bead: 'NEEDS-TRIAGE', where: 'common.css:788 | EPGManagerTab.css:108' },
  { selector: '.form-row', chunks: ['ChannelPipelineTab', 'M3UManagerTab', 'SettingsTab'], bead: 'NEEDS-TRIAGE', where: 'RuleBuilder.css:129 | SettingsTab.css:294,1034 | M3UAccountModal.css:48 — divergent display AND gap' },
  { selector: '.group-item', chunks: ['GuideTab', 'SettingsTab'], bead: 'NEEDS-TRIAGE', where: 'DeleteOrphanedGroupsModal.css:22 | PrintGuideModal.css:23 — divergent align-items' },
  { selector: '.profile-actions', chunks: ['EAGER', 'M3UManagerTab'], bead: 'NEEDS-TRIAGE', where: 'M3UProfileModal.css:140 | ChannelProfilesListModal.css:148 — divergent gap' },
  { selector: '.profile-card', chunks: ['M3UManagerTab', 'SettingsTab'], bead: 'NEEDS-TRIAGE', where: 'CloudTargetsCard.css:42 | M3UProfileModal.css:47 — divergent border-radius' },
  { selector: '.profiles-list', chunks: ['EAGER', 'M3UManagerTab'], bead: 'NEEDS-TRIAGE', where: 'M3UProfileModal.css:15 | ChannelProfilesListModal.css:68' },
  { selector: '.radio-group', chunks: ['EAGER', 'M3UManagerTab'], bead: 'NEEDS-TRIAGE', where: 'common.css:1497 | M3UAccountModal.css:15 | StreamsPane.css:899 — divergent gap' },
  { selector: '.updated-label', chunks: ['EPGManagerTab', 'M3UManagerTab'], bead: 'NEEDS-TRIAGE', where: 'EPGManagerTab.css:249 | M3UManagerTab.css:349' },
  { selector: '.updated-time', chunks: ['EPGManagerTab', 'M3UManagerTab'], bead: 'NEEDS-TRIAGE', where: 'EPGManagerTab.css:253 | M3UManagerTab.css:353' },
  { selector: '@media (max-width: 900px) :: .details-grid', chunks: ['JournalTab', 'M3UChangesTab'], bead: 'NEEDS-TRIAGE', where: 'JournalTab.css:449 | M3UChangesTab.css:489' },
];

/**
 * Ratchet on the untriaged bucket. It may only go DOWN. Lowering it is the
 * commit that files the bead; raising it is how the allowlist rots, so the
 * assertion refuses.
 */
const UNTRIAGED_BUDGET = 15;

// --- shared reporting -------------------------------------------------------

const chunksOf = (decls: Decl[]): string[] => [...new Set(decls.map((d) => d.chunk))].sort();

const describeSites = (decls: Decl[]): string =>
  decls.map((d) => `      [${d.chunk}] ${d.file}:${d.line}  (${d.props.join(', ')})`).join('\n');

/**
 * Properties this selector declares in more than one chunk, split by whether
 * the declared values actually differ. A divergent property renders
 * differently depending on tab-visit order TODAY; a same-value one is a latent
 * trap that becomes divergent the moment either copy is edited.
 */
function collidingProps(decls: Decl[]): { shared: string[]; divergent: string[] } {
  const byProp = new Map<string, Map<string, string>>();
  for (const d of decls) {
    for (const [prop, value] of d.values) {
      if (!byProp.has(prop)) byProp.set(prop, new Map());
      byProp.get(prop)!.set(d.chunk, value);
    }
  }
  const shared = [...byProp.entries()].filter(([, byChunk]) => byChunk.size > 1);
  return {
    shared: shared.map(([prop]) => prop),
    divergent: shared.filter(([, byChunk]) => new Set(byChunk.values()).size > 1).map(([prop]) => prop),
  };
}

// --- the checks -------------------------------------------------------------

describe('shared classes are not redeclared across bundle chunks', () => {
  it('TIER 1 (typography): no selector declares typography in two different chunks', () => {
    const violations = [...cssIndex().typography.entries()]
      .filter(([, decls]) => new Set(decls.map((d) => d.chunk)).size > 1)
      .sort(([a], [b]) => a.localeCompare(b));

    if (violations.length > 0) {
      const report = violations.map(([sel, decls]) => `  ${sel}\n${describeSites(decls)}`).join('\n');
      throw new Error(
        `${violations.length} selector(s) declare typography in more than one bundle chunk. ` +
          `The winner depends on which tabs the user visited and in what order — see the file header ` +
          `and docs/css_guidelines.md § Load order. Fix by deleting the page copy (leave a pointer ` +
          `comment) or scoping it to a page-root ancestor:\n${report}`
      );
    }

    expect(violations).toEqual([]);
  }, 60_000);

  it('TIER 2 (layout + visual): no NEW selector declares box, layout or paint properties in two different chunks', () => {
    // Same property in two chunks — not merely "some covered property in each".
    // `.foo{display:flex}` in chunk A and `.foo{padding:4px}` in chunk B do not
    // contend in the cascade, so they are not a collision.
    const current = new Map<string, { decls: Decl[]; chunks: string[]; shared: string[]; divergent: string[] }>();
    for (const [sel, decls] of cssIndex().layout) {
      if (new Set(decls.map((d) => d.chunk)).size < 2) continue;
      const props = collidingProps(decls);
      if (props.shared.length === 0) continue;
      current.set(sel, { decls, chunks: chunksOf(decls), ...props });
    }

    const baseline = new Map(KNOWN_CROSS_CHUNK_LAYOUT.map((e) => [e.selector, e]));
    expect(baseline.size, 'KNOWN_CROSS_CHUNK_LAYOUT has duplicate selector entries').toBe(KNOWN_CROSS_CHUNK_LAYOUT.length);
    for (const entry of KNOWN_CROSS_CHUNK_LAYOUT) {
      expect(entry.bead, `KNOWN_CROSS_CHUNK_LAYOUT entry "${entry.selector}" has no owning bead`).toBeTruthy();
    }
    const untriaged = KNOWN_CROSS_CHUNK_LAYOUT.filter((e) => e.bead === 'NEEDS-TRIAGE').length;
    expect(
      untriaged,
      `${untriaged} untriaged baseline entries but UNTRIAGED_BUDGET is ${UNTRIAGED_BUDGET}. ` +
        `The budget may only be LOWERED — file a bead and name it on the entry instead of raising it.`
    ).toBeLessThanOrEqual(UNTRIAGED_BUDGET);

    const problems: string[] = [];

    // 1. New collisions. This is the regression this tier exists to catch.
    for (const [sel, hit] of [...current].sort(([a], [b]) => a.localeCompare(b))) {
      if (baseline.has(sel)) continue;
      problems.push(
        `  NEW LEAK  ${sel}\n` +
          `      colliding: ${hit.shared.join(', ')}` +
          `${hit.divergent.length ? `   DIVERGENT VALUES: ${hit.divergent.join(', ')}` : '   (same values today — still order-dependent the moment either copy changes)'}\n` +
          describeSites(hit.decls)
      );
    }

    // 2. Baseline entries whose chunk span moved — partly fixed, or spread further.
    for (const entry of KNOWN_CROSS_CHUNK_LAYOUT) {
      const hit = current.get(entry.selector);
      if (!hit) continue;
      if (hit.chunks.join('+') === entry.chunks.join('+')) continue;
      problems.push(
        `  CHUNKS MOVED  ${entry.selector}\n` +
          `      baseline: ${entry.chunks.join(', ')}\n` +
          `      now:      ${hit.chunks.join(', ')}\n` +
          `      Finish the fix and delete the entry, or update its "chunks" deliberately (${entry.bead}).\n` +
          describeSites(hit.decls)
      );
    }

    // 3. Stale entries. An allowlist nobody prunes is how a guard rots.
    for (const entry of KNOWN_CROSS_CHUNK_LAYOUT) {
      if (current.has(entry.selector)) continue;
      problems.push(
        `  STALE ENTRY  ${entry.selector}\n` +
          `      No longer collides — delete this line from KNOWN_CROSS_CHUNK_LAYOUT (${entry.bead}).`
      );
    }

    if (problems.length > 0) {
      throw new Error(
        `${problems.length} layout/visual cross-chunk problem(s). A page CSS file and a shared or ` +
          `sibling-page file declare the SAME property on the SAME bare selector in different bundle ` +
          `chunks, so the winner depends on which tabs the user visited and in what order — this is the ` +
          `class of defect that shipped .action-btn at two geometries. Fix by deleting the page copy ` +
          `(leave a pointer comment) or scoping it to a page-root ancestor. See the file header and ` +
          `docs/css_guidelines.md § Load order.\n${problems.join('\n')}`
      );
    }

    expect(problems).toEqual([]);
  }, 60_000);

  it('vite.config.ts still uses default code-splitting, so the chunk model holds', () => {
    // deriveChunks() reimplements Rollup's DEFAULT splitting. A `manualChunks`
    // entry would silently move CSS between chunks and make every result above
    // wrong while the test still reported green.
    const viteConfig = fs.readFileSync(path.resolve(process.cwd(), 'vite.config.ts'), 'utf8');
    expect(viteConfig, 'vite.config.ts declares manualChunks — update deriveChunks() to model it').not.toMatch(/manualChunks/);
  });
});
