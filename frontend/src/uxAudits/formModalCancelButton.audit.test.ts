/// <reference types="node" />
// NOTE: `@types/node` is not an explicit devDependency of this package --
// see the identical note in `src/a11y/iconOnlyButtons.a11y.test.ts` for why
// this resolves today via transitive hoisting and what to do if it stops.
/**
 * Regression guard for enhancedchannelmanager-09x38.3: two independent form
 * modals (M3UAccountModal, LogoModal) shipped with a `.modal-footer` that
 * contained ONLY the mutating primary/danger action button — no Cancel
 * secondary, so the header X-close (or, in some cases, Escape) was the only
 * way to back out without saving. A follow-up audit found four more
 * instances of the exact same shape (BulkLCNFetchModal, DeleteOrphaned-
 * GroupsModal, NormalizeNamesModal, TaskEditorModal, M3ULinkedAccountsModal,
 * BulkEPGAssignModal). Two independent misses in the same UX pass pointed at
 * a component-level gap rather than one-off authoring accidents.
 *
 * There is no single shared "modal footer" React component in this codebase
 * to centralize the fix in (see docs/css_guidelines.md "Modal Patterns" --
 * ModalBase.css supplies the CSS classes, but every modal hand-assembles its
 * own footer JSX). Introducing one now would mean touching ~50 call sites
 * for a one-line invariant, which is disproportionate to the defect. This
 * test is a self-contained static-analysis check -- modeled directly on
 * `src/a11y/iconOnlyButtons.a11y.test.ts` -- that catches exactly the defect
 * shape: a `.modal-footer` with a mutating (primary/danger) button and no
 * secondary (Cancel/Close) button alongside it.
 *
 * Taxonomy (bead 09x38.3 note): a single X-only close IS an acceptable
 * pattern for non-mutating pickers/info views (e.g. Create Rule picker,
 * Execution Details, Probe Results) -- those modals' footers either have no
 * primary/danger button at all, or are covered by NON_MUTATING_ALLOWLIST
 * below with a justification. The rule below only fires when a footer
 * contains a *mutating* action with nothing beside it.
 *
 * A modal that already guards its own close path with unsaved-changes
 * tracking (a confirm-before-discard dialog, not just a plain onClose) is a
 * DIFFERENT, already-solved problem -- adding a bare Cancel button next to
 * its primary action would bypass that guard and reintroduce silent data
 * loss. Those are in DIRTY_GUARD_ALLOWLIST below, each with a pointer to the
 * guard that makes a bare Cancel redundant/unsafe.
 *
 * If this test starts failing on a NEW file: add a Cancel/secondary button
 * next to the mutating one (see M3UAccountModal.tsx or LogoModal.tsx for the
 * reference shape), OR -- only if the footer is a genuinely non-mutating
 * info view, or the modal already has its own dirty-guard -- add it to the
 * relevant allowlist below with a one-line justification. Do not allowlist
 * to make a real gap go away.
 */
import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import * as ts from 'typescript';

// vitest.config.ts runs with the frontend/ package root as cwd -- see the
// identical comment in iconOnlyButtons.a11y.test.ts.
const SRC_DIR = path.resolve(process.cwd(), 'src');
const COMPONENTS_DIR = path.join(SRC_DIR, 'components');

/**
 * Files with a `.modal-footer` whose only visible button is a mutating
 * primary/danger action, where that is CORRECT because the footer is a
 * non-mutating info/results view and the mutating-looking button is a
 * supplementary action, not the modal's core commit.
 */
const NON_MUTATING_ALLOWLIST: Record<string, string> = {
  'components/tabs/SettingsTab.tsx':
    'Probe Results modal is a non-mutating info view (viewing probed stream ' +
    'results); the header X-close is the escape hatch, matching the ' +
    'Execution-Details/Create-Rule-picker taxonomy. "Re-probe Failed ' +
    'Streams" is a supplementary re-run action, not a form commit.',
};

/**
 * Files whose modal already guards every close path (X, Escape, backdrop)
 * with its own unsaved-changes confirmation -- a bare Cancel next to the
 * primary button would bypass that guard and silently discard changes.
 */
const DIRTY_GUARD_ALLOWLIST: Record<string, string> = {
  'components/M3UGroupsModal.tsx':
    'PR #681 added a window.confirm dirty-guard on close (bead ' +
    'enhancedchannelmanager-09x38.7); a plain Cancel button would bypass it.',
  'components/EditChannelModal.tsx':
    'handleClose() checks hasChanges and shows a "Discard Changes" confirm ' +
    'dialog before closing; a plain Cancel button would bypass it.',
};

const ALLOWLIST: Record<string, string> = {
  ...NON_MUTATING_ALLOWLIST,
  ...DIRTY_GUARD_ALLOWLIST,
};

const MUTATING_CLASS_RE = /\b(modal-btn-primary|modal-btn-danger|btn-primary|btn-danger|user-modal-btn-primary)\b/;
const SECONDARY_CLASS_RE = /\b(modal-btn-secondary|btn-secondary|user-modal-btn-secondary)\b/;
const FOOTER_CLASS_RE = /\bmodal-footer\b/;

interface Violation {
  file: string;
  line: number;
  mutatingButtons: string[];
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

function classNameText(opening: ts.JsxOpeningElement | ts.JsxSelfClosingElement): string {
  const attr = getAttr(opening.attributes.properties, 'className');
  if (!attr || !attr.initializer) return '';
  if (ts.isStringLiteral(attr.initializer)) return attr.initializer.text;
  if (ts.isJsxExpression(attr.initializer) && attr.initializer.expression) {
    return attr.initializer.expression.getText();
  }
  return '';
}

function isDivWithClass(node: ts.Node, classRe: RegExp): node is ts.JsxElement {
  if (!ts.isJsxElement(node)) return false;
  if (node.openingElement.tagName.getText() !== 'div') return false;
  return classRe.test(classNameText(node.openingElement));
}

/** Collect every `<button>` JsxElement anywhere in this subtree. */
function collectButtons(node: ts.Node, out: ts.JsxElement[]): void {
  if (ts.isJsxElement(node) && node.openingElement.tagName.getText() === 'button') {
    out.push(node);
  }
  ts.forEachChild(node, (child) => collectButtons(child, out));
}

/**
 * Concatenated literal text content of a button, ignoring
 * `<span className="material-icons">` children. Only picks up *static*
 * JsxText -- dynamic text (`{cond ? 'A' : 'B'}`) is intentionally not
 * resolved here; those buttons are classified by CSS class instead (see
 * classNameText / SECONDARY_CLASS_RE), which already handles every dynamic-
 * text button found in this codebase (they use a non-primary/danger class).
 */
function staticButtonText(button: ts.JsxElement): string {
  let text = '';
  for (const child of button.children) {
    if (ts.isJsxText(child)) {
      text += child.getText();
    }
  }
  return text.trim();
}

function findViolationsInFile(file: string): Violation[] {
  const text = fs.readFileSync(file, 'utf8');
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const violations: Violation[] = [];

  function visit(node: ts.Node): void {
    if (isDivWithClass(node, FOOTER_CLASS_RE)) {
      const buttons: ts.JsxElement[] = [];
      collectButtons(node, buttons);

      const mutating: string[] = [];
      let hasSecondary = false;

      for (const btn of buttons) {
        const cls = classNameText(btn.openingElement);
        const isMutatingClass = MUTATING_CLASS_RE.test(cls);
        // A button whose static text is exactly "Close" is a non-mutating
        // dismiss action regardless of its CSS class -- some single-button
        // "acknowledge and close this results view" footers style that lone
        // button as visually prominent (btn-primary) even though it saves
        // nothing.
        const isCloseText = /^close$/i.test(staticButtonText(btn));
        if (SECONDARY_CLASS_RE.test(cls) || !isMutatingClass || isCloseText) hasSecondary = true;
        if (isMutatingClass && !isCloseText) mutating.push(cls || '(no className)');
      }

      if (mutating.length > 0 && !hasSecondary) {
        const { line } = sf.getLineAndCharacterOfPosition(node.getStart());
        violations.push({
          file: path.relative(SRC_DIR, file),
          line: line + 1,
          mutatingButtons: mutating,
        });
      }
    }

    ts.forEachChild(node, visit);
  }

  visit(sf);
  return violations;
}

describe('form modal footers pair a mutating action with a Cancel/secondary (bead 09x38.3)', () => {
  it('every .modal-footer with a primary/danger button also has a secondary button, unless allowlisted', () => {
    const files: string[] = [];
    walkTsxFiles(COMPONENTS_DIR, files);

    const allViolations = files.flatMap(findViolationsInFile);
    const unexplained = allViolations.filter((v) => !(v.file in ALLOWLIST));

    if (unexplained.length > 0) {
      const report = unexplained
        .map((v) => `  ${v.file}:${v.line}  mutating button(s)=[${v.mutatingButtons.join(', ')}]`)
        .join('\n');
      throw new Error(
        `${unexplained.length} modal-footer(s) have a mutating primary/danger button with no ` +
          `Cancel/secondary button alongside it. Add a "modal-btn modal-btn-secondary" Cancel button ` +
          `(see M3UAccountModal.tsx or LogoModal.tsx), or -- only if this is a non-mutating info view or ` +
          `already dirty-guarded -- add it to NON_MUTATING_ALLOWLIST / DIRTY_GUARD_ALLOWLIST in this test ` +
          `with a one-line justification:\n${report}`
      );
    }

    // Every allowlist entry must still correspond to a real, currently-detected
    // footer -- an entry that stops matching means the underlying file changed
    // shape and the allowlist comment is now stale (or the gap was silently
    // fixed and the entry should be deleted).
    const violatingFiles = new Set(allViolations.map((v) => v.file));
    const staleAllowlistEntries = Object.keys(ALLOWLIST).filter((f) => !violatingFiles.has(f));
    expect(staleAllowlistEntries, 'stale allowlist entries (file no longer matches the pattern -- remove the entry)').toEqual([]);
  });
});
