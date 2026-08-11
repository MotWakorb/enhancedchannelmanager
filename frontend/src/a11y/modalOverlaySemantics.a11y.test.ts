/// <reference types="node" />
/**
 * Audit ledger for the neutral ModalOverlay primitive (bead czmph).
 *
 * An entry in KNOWN_MISSING_SEMANTICS is debt, not an accessibility exception
 * or a claim that the caller is usable. Bead enhancedchannelmanager-hr4ft owns
 * classifying and remediating this explicit remainder. New omissions and stale
 * entries both fail, so the ledger cannot silently grow or rot.
 */
import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import * as ts from 'typescript';

const SRC = path.resolve(process.cwd(), 'src');
const EXPECTED_CALL_SITES = 75;
const KNOWN_MISSING_SEMANTICS = [
  'components/AutoSyncSettingsModal.tsx#1',
  'components/BackupRestoreModal.tsx#1',
  'components/BulkEPGAssignModal.tsx#1',
  'components/BulkLCNFetchModal.tsx#1',
  'components/CSVImportModal.tsx#1',
  'components/ChannelProfilesListModal.tsx#1',
  'components/ChannelStatsDetailModal.tsx#1',
  'components/DeleteOrphanedGroupsModal.tsx#1',
  'components/DummyEPGChannelPicker.tsx#1',
  'components/DummyEPGProfileModal.tsx#1',
  'components/DummyEPGSourceModal.tsx#1',
  'components/EditChannelModal.tsx#1',
  'components/FindDuplicatesModal.tsx#1',
  'components/GracenoteConflictModal.tsx#1',
  'components/HistoryToolbar.tsx#1',
  'components/ImportDummyEPGModal.tsx#1',
  'components/LogoModal.tsx#1',
  'components/M3UAccountModal.tsx#1',
  'components/M3UFiltersModal.tsx#1',
  'components/M3UGroupsModal.tsx#1',
  'components/M3ULinkedAccountsModal.tsx#1',
  'components/M3UProfileModal.tsx#1',
  'components/MergeChannelsModal.tsx#1',
  'components/NormalizeNamesModal.tsx#1',
  'components/PreviewStreamModal.tsx#1',
  'components/PrintGuideModal.tsx#1',
  'components/SecurityFirstRunModal.tsx#1',
  'components/ServerGroupsModal.tsx#1',
  'components/SettingsModal.tsx#1',
  'components/StreamProfilesListModal.tsx#1',
  'components/StreamsPane.tsx#2',
  'components/TaskEditorModal.tsx#1',
  'components/TaskEditorModal.tsx#2',
  'components/TaskEditorModal.tsx#3',
  'components/UserMenu.tsx#1',
  'components/UserMenu.tsx#2',
  'components/VLCProtocolHelperModal.tsx#1',
  'components/settings/CloudTargetEditor.tsx#1',
  'components/settings/CloudTargetsCard.tsx#1',
  'components/settings/LinkedAccountsSection.tsx#1',
  'components/settings/NormalizationEngineSection.tsx#1',
  'components/settings/NormalizationEngineSection.tsx#2',
  'components/settings/NormalizationEngineSection.tsx#3',
  'components/settings/NormalizationEngineSection.tsx#5',
  'components/settings/TagEngineSection.tsx#1',
  'components/settings/TagEngineSection.tsx#2',
  'components/tabs/EPGManagerTab.tsx#1',
  'components/tabs/LogoManagerTab.tsx#1',
  'components/tabs/SettingsTab.tsx#1',
  'components/tabs/SettingsTab.tsx#2',
] as const;

function attributeValue(
  opening: ts.JsxOpeningLikeElement,
  name: string,
  source: ts.SourceFile,
): string | boolean | undefined {
  const attribute = opening.attributes.properties.find(
    (candidate): candidate is ts.JsxAttribute =>
      ts.isJsxAttribute(candidate) && candidate.name.getText(source) === name,
  );
  if (!attribute?.initializer) return undefined;
  if (ts.isStringLiteral(attribute.initializer)) return attribute.initializer.text;
  if (ts.isJsxExpression(attribute.initializer)) {
    const expression = attribute.initializer.expression;
    if (expression?.kind === ts.SyntaxKind.TrueKeyword) return true;
    if (expression?.kind === ts.SyntaxKind.FalseKeyword) return false;
    if (expression && ts.isStringLiteral(expression)) return expression.text;
  }
  return undefined;
}

function ownsSemantics(opening: ts.JsxOpeningLikeElement, source: ts.SourceFile): boolean {
  const role = attributeValue(opening, 'role', source);
  const modal = attributeValue(opening, 'aria-modal', source);
  return (role === 'dialog' || role === 'alertdialog') && (modal === true || modal === 'true');
}

function descendantOwnsSemantics(
  dialog: ts.JsxElement,
  source: ts.SourceFile,
  overlayNames: ReadonlySet<string>,
): boolean {
  let found = false;
  const visit = (node: ts.Node): void => {
    if (found) return;
    if (ts.isJsxElement(node)) {
      if (overlayNames.has(node.openingElement.tagName.getText(source))) return;
      if (ownsSemantics(node.openingElement, source)) {
        found = true;
        return;
      }
    } else if (ts.isJsxSelfClosingElement(node) && ownsSemantics(node, source)) {
      found = true;
      return;
    }
    ts.forEachChild(node, visit);
  };
  dialog.children.forEach(visit);
  return found;
}

function modalOverlayNames(source: ts.SourceFile): Set<string> {
  const names = new Set<string>();
  for (const statement of source.statements) {
    if (!ts.isImportDeclaration(statement) || !ts.isStringLiteral(statement.moduleSpecifier)) continue;
    if (!/(^|\/)ModalOverlay$/.test(statement.moduleSpecifier.text)) continue;
    const bindings = statement.importClause?.namedBindings;
    if (bindings && ts.isNamedImports(bindings)) {
      for (const element of bindings.elements) {
        if ((element.propertyName ?? element.name).text === 'ModalOverlay') names.add(element.name.text);
      }
    } else if (bindings && ts.isNamespaceImport(bindings)) {
      names.add(`${bindings.name.text}.ModalOverlay`);
    }
  }
  return names;
}

function auditModalOverlays(): { total: number; missing: string[] } {
  const files = fs
    .readdirSync(SRC, { recursive: true, withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith('.tsx') && !entry.name.endsWith('.test.tsx'))
    .map((entry) => path.join(entry.parentPath, entry.name))
    .sort();
  const missing: string[] = [];
  let total = 0;

  for (const file of files) {
    const source = ts.createSourceFile(
      file,
      fs.readFileSync(file, 'utf8'),
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TSX,
    );
    const overlayNames = modalOverlayNames(source);
    let index = 0;
    const visit = (node: ts.Node): void => {
      const opening = ts.isJsxElement(node)
        ? node.openingElement
        : ts.isJsxSelfClosingElement(node)
          ? node
          : null;
      if (opening && overlayNames.has(opening.tagName.getText(source))) {
        index += 1;
        total += 1;
        const descendantSemantics = ts.isJsxElement(node)
          ? descendantOwnsSemantics(node, source, overlayNames)
          : false;
        if (!ownsSemantics(opening, source) && !descendantSemantics) {
          missing.push(`${path.relative(SRC, file)}#${index}`);
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(source);
  }
  return { total, missing };
}

describe('ModalOverlay caller semantics ledger', () => {
  it(
    'has no unrecorded or stale semantics omissions',
    () => {
      const audit = auditModalOverlays();
      expect(audit.total).toBe(EXPECTED_CALL_SITES);
      expect(audit.missing).toEqual([...KNOWN_MISSING_SEMANTICS]);
    },
    // This authoritative census parses every production TSX file. V8 coverage
    // instrumentation can push it beyond Vitest's 5 s default on CI runners;
    // keep the complete AST scan and give this repository-wide assertion a
    // narrow, explicit budget instead of introducing a lossy lexical prefilter.
    15_000,
  );

  it('discovers named and namespace import aliases', () => {
    const named = ts.createSourceFile(
      'named.tsx',
      "import { ModalOverlay as Overlay } from './ModalOverlay'; <Overlay onClose={() => {}} />;",
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TSX,
    );
    const namespace = ts.createSourceFile(
      'namespace.tsx',
      "import * as modal from './ModalOverlay'; <modal.ModalOverlay onClose={() => {}} />;",
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TSX,
    );
    expect([...modalOverlayNames(named)]).toEqual(['Overlay']);
    expect([...modalOverlayNames(namespace)]).toEqual(['modal.ModalOverlay']);
  });
});
