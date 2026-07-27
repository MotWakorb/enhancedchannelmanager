/// <reference types="node" />
// NOTE: `@types/node` is not an explicit devDependency of this package --
// it's only present because it's hoisted transitively (via msw and others).
// This file needs it for fs/path/process typings to typecheck. It resolves
// today because the package happens to be physically present under
// node_modules/@types/node, but that's not a guarantee across future
// lockfile changes. If `npx tsc --noEmit` ever starts failing here with
// "Cannot find name 'node:fs'" again, add `@types/node` as a real
// devDependency (`npm install --save-dev @types/node`) -- this couldn't be
// done during the original change because node_modules on this host is
// root-owned and `npm install` isn't available to the editing session.
/**
 * Regression guard for enhancedchannelmanager-mtklp (UX M1): icon-only
 * buttons across the app used to expose the Material icon font ligature
 * ("more_vert", "delete", "folder"...) as their only accessible name —
 * screen readers announced "button, delete" instead of a description of
 * what the button does (WCAG 4.1.2).
 *
 * eslint-plugin-jsx-a11y was evaluated and rejected as the enforcement
 * mechanism here: turning on its recommended rule set flags a broad set of
 * pre-existing, unrelated a11y patterns across ~60 files, which would force
 * a big-bang cleanup far outside this bead's scope just to get CI green.
 * This test is a self-contained, targeted static-analysis check that
 * catches exactly the defect pattern from the audit — a `<button>` (or
 * `role="button"` element) whose only rendered content is one or more
 * `<span className="material-icons">` children, no visible text, and no
 * `aria-label`/`aria-labelledby` — without pulling in a dependency whose
 * rule set governs much more than this one pattern.
 *
 * If this test starts failing, the new/changed button needs either a
 * visible text label or an `aria-label` (see docs/frontend_lint.md and the
 * icon+context -> label conventions established across the codebase: reuse
 * the existing `title` text where one exists, otherwise describe the
 * ACTION, not the icon).
 */
import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import * as ts from 'typescript';

// vitest.config.ts runs with the frontend/ package root as cwd (see
// package.json "test" script), so `src` is always resolvable from there --
// this avoids relying on import.meta.url, which Vitest's transform doesn't
// guarantee resolves to a real file: URL for every test file.
const SRC_DIR = path.resolve(process.cwd(), 'src');

interface Violation {
  file: string;
  line: number;
  className: string;
  icons: string[];
}

function walkTsxFiles(dir: string, out: string[]): void {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      walkTsxFiles(full, out);
    } else if (/\.tsx$/.test(entry.name) && !/\.test\.tsx$/.test(entry.name)) {
      out.push(full);
    }
  }
}

function getAttr(attrs: readonly ts.JsxAttributeLike[], name: string): ts.JsxAttribute | undefined {
  for (const a of attrs) {
    if (ts.isJsxAttribute(a) && a.name.getText() === name) return a;
  }
  return undefined;
}

function tagName(node: ts.JsxElement): string {
  return node.openingElement.tagName.getText();
}

function classNameText(opening: ts.JsxOpeningElement): string {
  const attr = getAttr(opening.attributes.properties, 'className');
  if (!attr || !attr.initializer) return '';
  if (ts.isStringLiteral(attr.initializer)) return attr.initializer.text;
  if (ts.isJsxExpression(attr.initializer) && attr.initializer.expression) {
    return attr.initializer.expression.getText();
  }
  return '';
}

function isMaterialIconSpan(node: ts.Node): node is ts.JsxElement {
  return ts.isJsxElement(node) && tagName(node) === 'span' && /material-icons/.test(classNameText(node.openingElement));
}

function analyzeChildren(children: ts.NodeArray<ts.JsxChild> | readonly ts.JsxChild[]): {
  onlyIcons: boolean;
  hasVisibleText: boolean;
  iconNames: string[];
} {
  let onlyIcons = true;
  let hasVisibleText = false;
  const iconNames: string[] = [];

  for (const child of children) {
    if (ts.isJsxText(child)) {
      if (child.getText().trim().length > 0) hasVisibleText = true;
      continue;
    }
    if (ts.isJsxElement(child)) {
      if (isMaterialIconSpan(child)) {
        const firstChild = child.children[0];
        iconNames.push(firstChild && ts.isJsxText(firstChild) ? firstChild.getText().trim() : '<dynamic>');
        continue;
      }
      onlyIcons = false;
      continue;
    }
    if (ts.isJsxExpression(child)) {
      const expr = child.expression;
      if (expr === undefined) {
        continue; // {/* comment */}
      }
      // `condition && <Badge/>` (e.g. an unread-count pill) renders nothing
      // at all when the condition is falsy -- in that state the button's
      // accessible name-from-content collapses to just the icon span(s), so
      // this doesn't get a pass. This is the exact shape that let the
      // NotificationCenter bell slip through the original sweep: it looked
      // "not icon-only" because of the badge, but with unreadCount === 0 the
      // badge doesn't render and the button was really icon-only.
      const isPossiblyAbsentAndExpr = ts.isBinaryExpression(expr) && expr.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken;
      if (!isPossiblyAbsentAndExpr) {
        // Anything else dynamic (ternary, plain variable, etc.) always
        // renders *something* -- could be real text. Treat conservatively
        // as "not icon-only" so we never false-positive on it.
        onlyIcons = false;
      }
      continue;
    }
    if (ts.isJsxFragment(child)) {
      const sub = analyzeChildren(child.children);
      if (!sub.onlyIcons) onlyIcons = false;
      if (sub.hasVisibleText) hasVisibleText = true;
      iconNames.push(...sub.iconNames);
      continue;
    }
    onlyIcons = false;
  }

  return { onlyIcons, hasVisibleText, iconNames };
}

function findViolationsInFile(file: string): Violation[] {
  const text = fs.readFileSync(file, 'utf8');
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const violations: Violation[] = [];

  function visit(node: ts.Node): void {
    let isButtonLike = false;
    if (ts.isJsxElement(node)) {
      const tag = tagName(node);
      const roleAttr = getAttr(node.openingElement.attributes.properties, 'role');
      const roleIsButton =
        !!roleAttr &&
        !!roleAttr.initializer &&
        ts.isStringLiteral(roleAttr.initializer) &&
        roleAttr.initializer.text === 'button';
      if (tag === 'button' || roleIsButton) isButtonLike = true;
    }

    if (isButtonLike && ts.isJsxElement(node)) {
      const attrs = node.openingElement.attributes.properties;
      const hasAccessibleName = !!getAttr(attrs, 'aria-label') || !!getAttr(attrs, 'aria-labelledby');
      const analysis = analyzeChildren(node.children);

      if (!hasAccessibleName && analysis.onlyIcons && !analysis.hasVisibleText && analysis.iconNames.length > 0) {
        const { line } = sf.getLineAndCharacterOfPosition(node.getStart());
        violations.push({
          file: path.relative(SRC_DIR, file),
          line: line + 1,
          className: classNameText(node.openingElement),
          icons: analysis.iconNames,
        });
      }
    }

    ts.forEachChild(node, visit);
  }

  visit(sf);
  return violations;
}

describe('icon-only buttons have an accessible name (WCAG 4.1.2)', () => {
  it(
    'every button whose only content is a material-icons span has aria-label or aria-labelledby',
    () => {
      const files: string[] = [];
      walkTsxFiles(SRC_DIR, files);

      const violations = files.flatMap(findViolationsInFile);

      if (violations.length > 0) {
        const report = violations
          .map((v) => `  ${v.file}:${v.line}  class="${v.className}"  icon(s)=[${v.icons.join(', ')}]`)
          .join('\n');
        throw new Error(
          `${violations.length} icon-only button(s) expose the Material icon ligature as their only ` +
            `accessible name. Add aria-label (describing the ACTION, e.g. "Delete channel" not "delete") ` +
            `and aria-hidden="true" on the icon span:\n${report}`
        );
      }

      expect(violations).toEqual([]);
    },
    // This is a static-analysis file walk (parse every .tsx under src/ with
    // the TypeScript compiler API) -- I/O + CPU bound on file count and
    // machine speed, not a hang risk. The default 5000ms vitest timeout
    // flaked twice on busy CI runners (PR #698 run 29650141697, PR #701 run
    // 29657847429) with zero frontend source touched by either PR -- a
    // wall-clock budget mismatch, not a real regression. 60s gives generous
    // headroom over the ~150-300ms this takes even on a loaded runner; if it
    // ever legitimately takes that long, something is actually wrong.
    60_000
  );
});
