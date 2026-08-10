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
 * THREE TIERS, TWO AXES — deliberate, see beads enhancedchannelmanager-6z299.6
 * (tiers 1-2) and enhancedchannelmanager-mklhu (tier 3)
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
 * TIER 3 (same chunk, either property set): baselined against
 * `KNOWN_SAME_CHUNK_MERGES`. Added by bead enhancedchannelmanager-mklhu.
 *
 * Tiers 1 and 2 both key collisions on CHUNK IDENTITY — they fire when a bare
 * selector is declared in two DIFFERENT chunks. That is exactly right for the
 * visit-order defect above, and BLIND BY CONSTRUCTION to the case where both
 * files are in the SAME chunk. The eager bundle is one chunk carrying
 * index.css, common.css, App.css, ChannelsPane.css, StreamsPane.css,
 * ModalBase.css and ~35 more, so "same chunk" covers most of the tree.
 *
 * That blind spot shipped a P1. `ChannelProfilesListModal.css` and
 * `ChannelsPane.css` both declared `.channel-item`; both eager; ChannelsPane is
 * emitted later, so its `display: grid` beat the modal's `display: flex` while
 * the modal's `gap` survived, and Manage Channels rendered channel names inside
 * a 32px grid track. Tiers 1 and 2 were green the whole time. Fixed in 266356ff.
 *
 * So TIER 3 keys on BYTE ORDER WITHIN A CHUNK instead — see
 * `deriveEmissionRank()` for the model and for how it was validated against two
 * real builds. It reports a selector declared by TWO OR MORE FILES of one
 * chunk, classified by `classifySameChunkGroup()`:
 *   - PARTIAL  the rendered element draws from more than one file. This is the
 *              `.channel-item` shape: a merge of two authors' intentions that
 *              neither wrote. 17 when this tier landed, 0 today — bead
 *              enhancedchannelmanager-87o0c reconciled them.
 *   - SHADOW   the last-emitted file wins every contended property, so the
 *              earlier copy is dead weight. Harmless today, and one deleted
 *              property away from PARTIAL — tracked, not ignored, because the
 *              commit that adds the duplicate and the commit that makes it
 *              render wrong are usually different commits, and only tracking
 *              both puts the failure on the one that introduced it. 14 when
 *              this tier landed, 0 today (same bead).
 *   - DISJOINT the files declare non-overlapping properties, so nothing
 *              contends. NOT reported, for the same reason TIER 2 requires a
 *              shared property. 5 today.
 * A shape flip in either direction fails the check, as does a moved chunk.
 *
 * TIER 3 also closes tiers 1/2's largest known gap: it expands shorthands, so
 * `padding` and `padding-left` contend. That was measured before adopting —
 * see `SHORTHAND_SLOTS` — and costs zero extra reported selectors.
 *
 * WHAT TIER 3 STILL CANNOT SEE, measured rather than guessed:
 *   - `deriveChunks()` collapses everything statically reachable from the entry
 *     into one EAGER bucket, but the real build splits off any module that the
 *     entry AND every lazy root reach: today that is Toast.css + ToastContainer
 *     .css, emitted as NotificationContext-*.css and <link>ed BEFORE index-*.css.
 *     Those two files therefore sit in a different stylesheet from the rest of
 *     EAGER, and a collision between them and (say) common.css would be labelled
 *     same-chunk here. It would still be a real, deterministic override in the
 *     same direction — the emission rank and the <link> order agree — so the
 *     verdict is right and only the word "chunk" is loose. Neither file
 *     participates in any collision today. Unifying the two chunk models is
 *     follow-up work, not a correctness hole in the reported set.
 *   - Properties outside TIER1+TIER2's lists (transition, transform, ...) are
 *     invisible, so a group whose ONLY surviving contribution from the earlier
 *     file is such a property is classified SHADOW rather than PARTIAL.
 *   - Two rules in ONE file are skipped: that is the author's own ordering, not
 *     a collision between two authors.
 *   - Scoping a rule makes it a different key and therefore invisible, exactly
 *     as in tiers 1 and 2. That is the intended fix, not a gap.
 *
 * WHEN ANY OF THE THREE FAILS: do not scope-and-move-on and do not add a
 * baseline entry for new work. Delete the page copy and let the shared layer
 * own the class (leaving the pointer comment behind), or scope the page copy to
 * a page-root ancestor so it can no longer leak. See docs/css_guidelines.md
 * § Load order.
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
 * and the CSS file it reports as unreachable from the entry
 * (DummyEPGChannelPicker.css -- its component is reached only from the dev
 * harness) is correspondingly absent from every built chunk. Unreachable
 * files are skipped: nothing loads them, so they cannot leak.
 * ChannelDetail.css was the other one; its component was imported by nothing
 * at all and both files were deleted (bead enhancedchannelmanager-albfq).
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

/**
 * Emission order of each CSS file WITHIN its chunk (0 = emitted first).
 *
 * Vite's `vite:css-post` renderChunk concatenates `styles.get(id)` while
 * iterating `Object.keys(chunk.modules)` — i.e. the chunk's modules in bundler
 * EXECUTION order. Execution order is a post-order DFS from the static entry
 * (each module's own static imports first, in source order, then the module
 * itself), followed by every dynamic-import target in discovery order. That is
 * what this function reimplements; `deriveChunks()` already parses the same
 * edges, so this only re-walks them.
 *
 * VALIDATED AGAINST TWO REAL BUILDS (2026-07-29, vite 8.0.16 / rolldown):
 * for every CSS file, the byte span of the class tokens that only that file
 * declares was located inside the built stylesheet, and the files were ordered
 * by the median hit. Across 15 built stylesheets and 108 adjacent comparisons
 * there were ZERO inversions against this model — on `newui` HEAD and again on
 * 266356ff^. The eager cascade was treated as one sequence in the order
 * index.html <link>s it, which is how the browser applies it.
 *
 * WHY IT MATTERS: within one stylesheet the later declaration wins outright.
 * Two files declaring the same bare selector therefore produce a deterministic
 * result — but one nobody wrote, and one that depends on an emission order no
 * source file states. See TIER 3.
 */
function deriveEmissionRank(chunks: Map<string, string>): Map<string, number> {
  const ordered: string[] = [];
  const visited = new Set<string>();
  const dynamicEntries = new Set<string>();
  const analyse = (mod: string): void => {
    if (visited.has(mod)) return;
    visited.add(mod);
    const e = edges(mod);
    for (const dep of e.statik) analyse(dep);
    for (const d of e.dynamic) dynamicEntries.add(d);
    ordered.push(mod);
  };
  analyse(ENTRY);
  // A Set iterated with for..of picks up entries appended during iteration,
  // which is exactly how Rollup drains its dynamic-entry queue.
  for (const entry of dynamicEntries) analyse(entry);

  const nextRank = new Map<string, number>();
  const rank = new Map<string, number>();
  for (const mod of ordered) {
    const chunk = chunks.get(mod);
    if (chunk === undefined || chunk === 'UNREACHED') continue;
    const n = nextRank.get(chunk) ?? 0;
    rank.set(mod, n);
    nextRank.set(chunk, n + 1);
  }
  return rank;
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

// --- tier 3 model -----------------------------------------------------------

/** One rule declaring covered properties, positioned in its chunk's cascade. */
interface SameChunkSite {
  file: string;
  line: number;
  /** Emission rank of `file` within its chunk. Lower = earlier = loses. */
  rank: number;
  decls: Array<{ prop: string; value: string; important: boolean }>;
}

interface SameChunkGroup {
  chunk: string;
  selector: string;
  sites: SameChunkSite[];
}

/**
 * TIER 3 covers TIER 1's and TIER 2's properties together. The two are split
 * upstream because they are burned down by different work, not because a
 * merged box is less of a defect when the merge is a font size: an element
 * whose weight comes from one file and whose colour comes from another is the
 * same failure either way. Splitting them here would only let a two-property
 * merge hide by straddling the boundary.
 */
const TIER3_PROPS = new Set([...TYPOGRAPHY, ...LAYOUT_VISUAL]);

/**
 * Longhand slots each shorthand writes, restricted to properties this file
 * covers. Named as the largest gap in TIER 1/TIER 2, which compare literal
 * property names and so read `padding` vs `padding-left` as no collision.
 *
 * MEASURED BEFORE ADOPTING, on `newui` HEAD and on 266356ff^: expanding
 * shorthands adds ZERO reported selectors on either tree — every same-chunk
 * group containing a shorthand/longhand pair was already reported through some
 * literal property. So there is no false-positive budget being spent, and two
 * things are bought: the reported `contended` list becomes truthful (at
 * 266356ff^ it is what surfaces `gap` vs `column-gap` on `.channel-item`), and
 * a page can no longer evade this tier by writing `padding-left` against a
 * shared `padding`.
 *
 * `border-radius` is deliberately absent from `border`: it is not part of the
 * `border` shorthand and is not reset by it.
 */
const SHORTHAND_SLOTS: Record<string, string[]> = {
  padding: ['padding-top', 'padding-right', 'padding-bottom', 'padding-left'],
  'padding-inline': ['padding-left', 'padding-right'],
  'padding-block': ['padding-top', 'padding-bottom'],
  margin: ['margin-top', 'margin-right', 'margin-bottom', 'margin-left'],
  'margin-inline': ['margin-left', 'margin-right'],
  'margin-block': ['margin-top', 'margin-bottom'],
  border: ['border-width', 'border-style', 'border-color', 'border-top', 'border-right', 'border-bottom', 'border-left'],
  'border-width': ['border-top', 'border-right', 'border-bottom', 'border-left'],
  'border-style': ['border-top', 'border-right', 'border-bottom', 'border-left'],
  'border-color': ['border-top', 'border-right', 'border-bottom', 'border-left'],
  background: ['background-color', 'background-image'],
  flex: ['flex-grow', 'flex-shrink', 'flex-basis'],
  gap: ['row-gap', 'column-gap'],
  overflow: ['overflow-x', 'overflow-y'],
  'grid-area': ['grid-row', 'grid-column'],
};

const slotsOf = (prop: string): string[] => {
  const expanded = SHORTHAND_SLOTS[prop];
  return expanded ? [prop, ...expanded] : [prop];
};

type SameChunkShape = 'PARTIAL' | 'SHADOW' | 'DISJOINT';

/**
 * Resolve one chunk's cascade for a single selector and name the shape.
 *
 *   PARTIAL   the surviving declarations come from MORE THAN ONE file, so the
 *             element renders a merge of two authors' intentions that neither
 *             wrote. This is the shape that produced the `.channel-item` P1.
 *   SHADOW    contention exists but the last-emitted file wins every contended
 *             slot, so the earlier copy is dead weight. Deterministic and
 *             harmless TODAY — and one deleted property away from PARTIAL,
 *             which is why it is tracked rather than ignored (see TIER 3).
 *   DISJOINT  the files declare non-overlapping properties, so nothing
 *             contends and both apply exactly as written. Not reported, for
 *             the same reason TIER 2 requires a shared property.
 *
 * Specificity is not consulted because the key IS the normalised selector, so
 * every site in a group has identical specificity by construction. Within one
 * origin at equal specificity the cascade is: `!important` beats normal, and
 * among equals the later declaration wins — which is what the winner map does.
 */
function classifySameChunkGroup(sites: SameChunkSite[]): {
  shape: SameChunkShape;
  contended: string[];
  divergent: string[];
} {
  const ordered = [...sites].sort((a, b) => a.rank - b.rank || a.line - b.line);
  const winner = new Map<string, { file: string; important: boolean }>();
  const writers = new Map<string, Set<string>>();
  const values = new Map<string, Set<string>>();

  for (const site of ordered) {
    for (const d of site.decls) {
      if (!values.has(d.prop)) values.set(d.prop, new Set());
      values.get(d.prop)!.add(d.value);
      for (const slot of slotsOf(d.prop)) {
        const held = winner.get(slot);
        if (!held || d.important || !held.important) winner.set(slot, { file: site.file, important: d.important });
        if (!writers.has(slot)) writers.set(slot, new Set());
        writers.get(slot)!.add(site.file);
      }
    }
  }

  // Report the property names authors actually wrote, not the expanded slots.
  const contended = [...new Set(ordered.flatMap((s) => s.decls.map((d) => d.prop)))]
    .filter((prop) => slotsOf(prop).some((slot) => (writers.get(slot)?.size ?? 0) > 1))
    .sort();
  if (contended.length === 0) return { shape: 'DISJOINT', contended: [], divergent: [] };

  const survivors = new Set([...winner.values()].map((w) => w.file));
  return {
    shape: survivors.size > 1 ? 'PARTIAL' : 'SHADOW',
    contended,
    divergent: contended.filter((prop) => (values.get(prop)?.size ?? 0) > 1),
  };
}

/**
 * Index every reachable CSS file once, into one map per tier. All three tiers
 * key on (at-rule context, normalised selector); a rule lands in a tier's map
 * only if it declares at least one property that tier covers. TIER 3 keys on
 * (chunk, at-rule context, normalised selector) because its question is asked
 * inside one chunk, not across chunks.
 */
function buildIndex(): {
  typography: Map<string, Decl[]>;
  layout: Map<string, Decl[]>;
  sameChunk: Map<string, SameChunkGroup>;
} {
  const typography = new Map<string, Decl[]>();
  const layout = new Map<string, Decl[]>();
  const sameChunk = new Map<string, SameChunkGroup>();
  const typoProps = new Set(TYPOGRAPHY);
  const layoutProps = new Set(LAYOUT_VISUAL);

  const chunks = deriveChunks();
  const emissionRank = deriveEmissionRank(chunks);

  for (const [cssFile, chunk] of chunks) {
    if (chunk === 'UNREACHED') continue; // no module imports it, so it never loads
    const root = parse(fs.readFileSync(cssFile, 'utf8'), { from: cssFile });
    const rank = emissionRank.get(cssFile) ?? 0;
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

      const tier3 = decls.filter((d) => TIER3_PROPS.has(d.prop.toLowerCase()));
      if (tier3.length === 0) return;
      const site: SameChunkSite = {
        file: path.relative(SRC, cssFile),
        line: rule.source?.start?.line ?? 0,
        rank,
        decls: tier3.map((d) => ({ prop: d.prop.toLowerCase(), value: d.value.trim(), important: !!d.important })),
      };
      for (const sel of rule.selectors) {
        const selector = [...ctx, normalize(sel)].join(' :: ');
        const key = `${chunk} :: ${selector}`;
        if (!sameChunk.has(key)) sameChunk.set(key, { chunk, selector, sites: [] });
        sameChunk.get(key)!.sites.push(site);
      }
    });
  }
  return { typography, layout, sameChunk };
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
  // The unconditional `.filters-bar` pair is retired: neither tab rendered the
  // class, so both copies were deleted outright rather than scoped, and the
  // real owner `.watch-history-panel .filters-bar` now writes out the
  // flex-shrink it was borrowing (bead enhancedchannelmanager-albfq). The two
  // narrow-viewport copies below are still declared and stay baselined.
  // StatsTab's copy is gone (bead enhancedchannelmanager-wjbwr): StatsTab.tsx
  // rendered no `.header-actions` element at all — its Refresh button reaches
  // PageHeader's `.header-actions` through `RouteHeaderSlot name="primary-action"`
  // — and the copy declared common.css's five properties with identical values,
  // so deleting it is render-neutral. Journal and M3U Changes still declare it.
  { selector: '.header-actions', chunks: ['EAGER', 'JournalTab', 'M3UChangesTab'], bead: 'enhancedchannelmanager-6z299.1', where: 'common.css:1238 | JournalTab.css:60 | M3UChangesTab.css:63' },
  { selector: '.header-left', chunks: ['JournalTab', 'M3UChangesTab', 'StatsTab'], bead: 'enhancedchannelmanager-6z299.1', where: 'StatsTab.css:33 | JournalTab.css:26 | M3UChangesTab.css:34 — divergent row-gap' },
  { selector: '.header-stats', chunks: ['JournalTab', 'M3UChangesTab'], bead: 'enhancedchannelmanager-6z299.1', where: 'JournalTab.css:41 | M3UChangesTab.css:49' },
  { selector: '@media (max-width: 600px) :: .filter-select', chunks: ['JournalTab', 'M3UChangesTab'], bead: 'enhancedchannelmanager-6z299.1', where: 'JournalTab.css:483 | M3UChangesTab.css:527' },
  { selector: '@media (max-width: 600px) :: .filters-bar', chunks: ['JournalTab', 'M3UChangesTab'], bead: 'enhancedchannelmanager-6z299.1', where: 'JournalTab.css:571 | M3UChangesTab.css:587' },
  { selector: '@media (max-width: 600px) :: .header-actions', chunks: ['JournalTab', 'LogoManagerTab', 'M3UChangesTab'], bead: 'enhancedchannelmanager-6z299.1', where: 'JournalTab.css:471 | M3UChangesTab.css:511 | LogoManagerTab.css:456' },
  { selector: '@media (max-width: 768px) :: .filters-bar', chunks: ['JournalTab', 'M3UChangesTab'], bead: 'enhancedchannelmanager-6z299.1', where: 'JournalTab.css:551 | M3UChangesTab.css:559' },
  // § 28 METRIC TILE absorbs Pipeline's and Stats' shared tile.
  { selector: '.stat-item', chunks: ['ChannelPipelineTab', 'StatsTab'], bead: 'enhancedchannelmanager-6z299.1', where: 'ChannelPipelineTab.css:48 | StatsTab.css:507' },
  // § 4.4 rogue-duplicate deletions.
  { selector: '.btn-danger', chunks: ['ChannelPipelineTab', 'EAGER'], bead: 'enhancedchannelmanager-6z299.1', where: 'common.css:173 | RuleBuilder.css:387 — same family as the .btn-primary/.btn-secondary deletions at RuleBuilder.css:385,395' },
  { selector: '.search-box', chunks: ['EAGER', 'SettingsTab'], bead: 'enhancedchannelmanager-6z299.1', where: 'common.css:882 | TagEngineSection.css:23 — divergent padding, border' },
  { selector: '.status-disabled', chunks: ['EAGER', 'SettingsTab'], bead: 'enhancedchannelmanager-6z299.1', where: 'common.css:477 | CloudTargetsCard.css:125 — § 4.2 retokenises the common.css side; the CloudTargetsCard copy must go with it' },
];

/**
 * Ratchet on the untriaged bucket. It may only go DOWN. Lowering it is the
 * commit that files the bead; raising it is how the allowlist rots, so the
 * assertion refuses.
 */
const UNTRIAGED_BUDGET = 0;

// --- tier 3 baseline --------------------------------------------------------

interface SameChunkBaselineEntry {
  /** Chunk the collision happens inside. Drift here fails the check. */
  chunk: string;
  /** Exactly the key this file reports: `[@at-rule ... :: ]<normalised selector>`. */
  selector: string;
  /** PARTIAL or SHADOW. A shape flip fails the check — see below. */
  shape: 'PARTIAL' | 'SHADOW';
  /** The bead that deletes this entry. Never blank. */
  bead: string;
  /** Sites + what contends, so a reader does not have to re-run the audit. */
  where: string;
}

/**
 * EMPTY, and it stays empty. This tier was born with 31 pre-existing same-chunk
 * collisions (17 PARTIAL, 14 SHADOW, measured 2026-07-29 on branch `newui`);
 * bead enhancedchannelmanager-87o0c reconciled all 31 and deleted their lines,
 * so every remaining same-chunk group in the tree is DISJOINT and unreported.
 *
 * `where` is documentation, not an assertion — line numbers move under
 * unrelated edits and asserting on them would make this list fail for reasons
 * that are not defects. `chunk`, `selector` and `shape` are the asserted
 * identity.
 *
 * TO REMOVE A LINE: reconcile the collision (delete the duplicate copy, or
 * scope one of them to a page-root ancestor). The check then fails with "no
 * longer collides" and you delete the line — the same workflow as TIER 2.
 *
 * DO NOT ADD A LINE for new work. A new same-chunk collision is the defect
 * this tier exists to stop, and there is no longer a backlog to hide in.
 */
const KNOWN_SAME_CHUNK_MERGES: SameChunkBaselineEntry[] = [];

/**
 * Ratchet on TIER 3's untriaged bucket, mirroring UNTRIAGED_BUDGET. It has been
 * zero since the tier was added — every entry the baseline ever held named its
 * owning bead — so a `NEEDS-TRIAGE` entry fails immediately rather than
 * accumulating. It may only be LOWERED, and it is already at the floor.
 */
const SAME_CHUNK_UNTRIAGED_BUDGET = 0;

// --- shared reporting -------------------------------------------------------

const chunksOf = (decls: Decl[]): string[] => [...new Set(decls.map((d) => d.chunk))].sort();

const describeSites = (decls: Decl[]): string =>
  decls.map((d) => `      [${d.chunk}] ${d.file}:${d.line}  (${d.props.join(', ')})`).join('\n');

/** TIER 3 sites, in cascade order — the last line printed is the one that wins. */
const describeSameChunkSites = (sites: SameChunkSite[]): string =>
  [...sites]
    .sort((a, b) => a.rank - b.rank || a.line - b.line)
    .map((s) => `      emitted #${String(s.rank).padStart(2)}  ${s.file}:${s.line}  {${s.decls.map((d) => `${d.prop}: ${d.value}${d.important ? ' !important' : ''}`).join('; ')}}`)
    .join('\n');

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

  it('TIER 3 (same-chunk emission order): no NEW selector is declared by two files inside one chunk', () => {
    const current = new Map<string, { group: SameChunkGroup; shape: SameChunkShape; contended: string[]; divergent: string[] }>();
    for (const [key, group] of cssIndex().sameChunk) {
      // Two rules in ONE file are that author's own deliberate ordering, not a
      // collision between two authors. Require at least two files.
      if (new Set(group.sites.map((s) => s.file)).size < 2) continue;
      const verdict = classifySameChunkGroup(group.sites);
      if (verdict.shape === 'DISJOINT') continue;
      current.set(key, { group, ...verdict });
    }

    const baseline = new Map(KNOWN_SAME_CHUNK_MERGES.map((e) => [`${e.chunk} :: ${e.selector}`, e]));
    expect(baseline.size, 'KNOWN_SAME_CHUNK_MERGES has duplicate chunk+selector entries').toBe(KNOWN_SAME_CHUNK_MERGES.length);
    for (const entry of KNOWN_SAME_CHUNK_MERGES) {
      expect(entry.bead, `KNOWN_SAME_CHUNK_MERGES entry "${entry.chunk} :: ${entry.selector}" has no owning bead`).toBeTruthy();
    }
    const untriaged = KNOWN_SAME_CHUNK_MERGES.filter((e) => e.bead === 'NEEDS-TRIAGE').length;
    expect(
      untriaged,
      `${untriaged} untriaged TIER 3 baseline entries but SAME_CHUNK_UNTRIAGED_BUDGET is ${SAME_CHUNK_UNTRIAGED_BUDGET}. ` +
        `The budget may only be LOWERED — file a bead and name it on the entry instead of raising it.`
    ).toBeLessThanOrEqual(SAME_CHUNK_UNTRIAGED_BUDGET);

    const problems: string[] = [];

    // 1. New collisions. This is the regression this tier exists to catch.
    for (const [key, hit] of [...current].sort(([a], [b]) => a.localeCompare(b))) {
      if (baseline.has(key)) continue;
      problems.push(
        `  NEW ${hit.shape}  ${key}\n` +
          `      contended: ${hit.contended.join(', ')}` +
          `${hit.divergent.length ? `   DIVERGENT VALUES: ${hit.divergent.join(', ')}` : '   (same values today — still a merge the moment either copy changes)'}\n` +
          describeSameChunkSites(hit.group.sites)
      );
    }

    // 2. Shape flips. SHADOW -> PARTIAL means an edit just turned dead weight
    //    into a live merge; PARTIAL -> SHADOW means it was half-fixed. Either
    //    way the entry no longer describes what is in the tree.
    for (const entry of KNOWN_SAME_CHUNK_MERGES) {
      const hit = current.get(`${entry.chunk} :: ${entry.selector}`);
      if (!hit || hit.shape === entry.shape) continue;
      problems.push(
        `  SHAPE CHANGED  ${entry.chunk} :: ${entry.selector}\n` +
          `      baseline: ${entry.shape}\n` +
          `      now:      ${hit.shape}  (contended: ${hit.contended.join(', ')})\n` +
          `      Finish the reconciliation and delete the entry, or update "shape" deliberately (${entry.bead}).\n` +
          describeSameChunkSites(hit.group.sites)
      );
    }

    // 3. Stale entries. An allowlist nobody prunes is how a guard rots. `chunk`
    //    is part of the key, so a collision that MOVED to another chunk surfaces
    //    here as stale AND above as new; say so, or the reader is told to delete
    //    a line describing a collision that still exists somewhere else.
    for (const entry of KNOWN_SAME_CHUNK_MERGES) {
      if (current.has(`${entry.chunk} :: ${entry.selector}`)) continue;
      const movedTo = [...current.values()].filter((h) => h.group.selector === entry.selector).map((h) => h.group.chunk);
      problems.push(
        `  STALE ENTRY  ${entry.chunk} :: ${entry.selector}\n` +
          (movedTo.length
            ? `      No longer collides in ${entry.chunk}, but still collides in ${movedTo.join(', ')} — the ` +
              `collision MOVED. Finish the fix, or retarget this entry's "chunk" deliberately (${entry.bead}).`
            : `      No longer collides — delete this line from KNOWN_SAME_CHUNK_MERGES (${entry.bead}).`)
      );
    }

    if (problems.length > 0) {
      throw new Error(
        `${problems.length} same-chunk problem(s). Two files in ONE bundle chunk declare the same bare ` +
          `selector, so the later-emitted file silently overrides the earlier one on every property they ` +
          `share — deterministically, but in an order no source file states. This is the class that ` +
          `shipped .channel-item as a flex row wearing ChannelsPane's display:grid. Fix by deleting the ` +
          `duplicate copy (leave a pointer comment) or scoping one to a page-root ancestor. See the file ` +
          `header and docs/css_guidelines.md § Load order.\n${problems.join('\n')}`
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

  it('vite.config.ts declares no chunk-grouping override, so TIER 3 emission order holds', () => {
    // TIER 3 needs more than "chunk membership is default": it needs the ORDER
    // of files inside a chunk to be plain execution order. vite 8 bundles with
    // rolldown, whose grouping and ordering knobs are named differently from
    // Rollup's `manualChunks` — so the check above does not cover them.
    const viteConfig = fs.readFileSync(path.resolve(process.cwd(), 'vite.config.ts'), 'utf8');
    for (const knob of ['advancedChunks', 'rolldownOptions', 'codeSplitting', 'chunkModulesOrder', 'preserveModules']) {
      expect(
        viteConfig,
        `vite.config.ts declares ${knob} — it can reorder or regroup a chunk's modules, which invalidates deriveEmissionRank()`
      ).not.toContain(knob);
    }
  });
});

/**
 * Self-tests for TIER 3's cascade resolver.
 *
 * The tree-driven assertion above can only exercise the shapes that happen to
 * exist in the tree today; `!important` in particular is declared 47 times in
 * this codebase but never inside a same-chunk multi-file group, so the branch
 * that handles it would otherwise ship untested. These cases pin the semantics
 * directly — a guard whose classifier is wrong reports the wrong severity and
 * is worse than no guard.
 */
describe('classifySameChunkGroup', () => {
  const site = (
    file: string,
    rank: number,
    decls: Array<[string, string] | [string, string, true]>
  ): SameChunkSite => ({
    file,
    line: 1,
    rank,
    decls: decls.map(([prop, value, important]) => ({ prop, value, important: important === true })),
  });

  it('is PARTIAL when the surviving declarations come from more than one file', () => {
    // The .channel-item defect in miniature: the later file wins `display`,
    // the earlier file's `gap` survives, and the box is neither author's.
    const v = classifySameChunkGroup([
      site('ChannelProfilesListModal.css', 2, [['display', 'flex'], ['gap', '0.75rem']]),
      site('ChannelsPane.css', 18, [['display', 'grid']]),
    ]);
    expect(v.shape).toBe('PARTIAL');
    expect(v.contended).toEqual(['display']);
    expect(v.divergent).toEqual(['display']);
  });

  it('is SHADOW when the last-emitted file wins every contended slot', () => {
    const v = classifySameChunkGroup([
      site('StreamsPane.css', 21, [['max-height', '180px']]),
      site('shared/common.css', 41, [['max-height', '300px'], ['overflow-y', 'auto']]),
    ]);
    expect(v.shape).toBe('SHADOW');
    expect(v.contended).toEqual(['max-height']);
    expect(v.divergent).toEqual(['max-height']);
  });

  it('is DISJOINT when the two files declare non-overlapping properties', () => {
    const v = classifySameChunkGroup([
      site('StreamsPane.css', 21, [['overflow', 'visible']]),
      site('shared/common.css', 41, [['padding', '0.75rem']]),
    ]);
    expect(v.shape).toBe('DISJOINT');
    expect(v.contended).toEqual([]);
  });

  it('reports identical values as a collision, because it is one edit from a merge', () => {
    const v = classifySameChunkGroup([
      site('a.css', 1, [['display', 'flex'], ['gap', '0.5rem']]),
      site('b.css', 2, [['display', 'flex']]),
    ]);
    expect(v.shape).toBe('PARTIAL');
    expect(v.divergent).toEqual([]);
  });

  it('resolves emission order, not file order, when ranks disagree with the argument order', () => {
    const later = site('later.css', 9, [['color', 'blue']]);
    const earlier = site('earlier.css', 1, [['color', 'red'], ['padding', '1rem']]);
    expect(classifySameChunkGroup([later, earlier]).shape).toBe('PARTIAL');
    // Flip which file is emitted last: now the survivor set is a single file.
    const shadowing = site('later.css', 9, [['color', 'blue'], ['padding', '2rem']]);
    expect(classifySameChunkGroup([shadowing, earlier]).shape).toBe('SHADOW');
  });

  it('treats several rules in one file as a single author', () => {
    // A file overriding itself further down is its own deliberate ordering; it
    // must not read as a merge just because the selector appears twice.
    const v = classifySameChunkGroup([
      { ...site('a.css', 1, [['color', 'red']]), line: 10 },
      { ...site('a.css', 1, [['color', 'green']]), line: 90 },
      site('b.css', 2, [['color', 'blue']]),
    ]);
    expect(v.shape).toBe('SHADOW');
    expect(v.contended).toEqual(['color']);
  });

  it('sees a shorthand overwriting an earlier longhand as a full shadow', () => {
    const v = classifySameChunkGroup([
      site('a.css', 1, [['padding-left', '8px']]),
      site('b.css', 2, [['padding', '4px']]),
    ]);
    expect(v.shape).toBe('SHADOW');
    expect(v.contended).toEqual(['padding', 'padding-left']);
  });

  it('sees a longhand refining an earlier shorthand as a partial merge', () => {
    const v = classifySameChunkGroup([
      site('a.css', 1, [['padding', '4px']]),
      site('b.css', 2, [['padding-left', '8px']]),
    ]);
    expect(v.shape).toBe('PARTIAL');
    expect(v.contended).toEqual(['padding', 'padding-left']);
  });

  it('does not treat border-radius as part of the border shorthand', () => {
    const v = classifySameChunkGroup([
      site('a.css', 1, [['border-radius', '4px']]),
      site('b.css', 2, [['border', '1px solid red']]),
    ]);
    expect(v.shape).toBe('DISJOINT');
  });

  it('lets an earlier !important beat a later normal declaration', () => {
    const v = classifySameChunkGroup([
      site('a.css', 1, [['color', 'red', true]]),
      site('b.css', 2, [['color', 'blue'], ['padding', '1rem']]),
    ]);
    // b.css is emitted later and wins `padding`, but a.css keeps `color`, so
    // the rendered box still draws from both files.
    expect(v.shape).toBe('PARTIAL');
    expect(v.contended).toEqual(['color']);
  });

  it('lets a later !important beat an earlier !important', () => {
    const v = classifySameChunkGroup([
      site('a.css', 1, [['color', 'red', true]]),
      site('b.css', 2, [['color', 'blue', true]]),
    ]);
    expect(v.shape).toBe('SHADOW');
  });
});
