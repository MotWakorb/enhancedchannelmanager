/// <reference types="node" />
/**
 * Closed ownership audit for the neutral ModalOverlay primitive (hr4ft.3).
 *
 * The manifest records both accessible callers and explicit current debt. It
 * is bidirectional: production callers and reviewed entries must match exactly.
 */
import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import * as ts from 'typescript';
import { MODAL_OVERLAY_MANIFEST, type ModalOverlayManifestEntry } from './modalOverlayManifest';

const SRC = path.resolve(process.cwd(), 'src');
const EXPECTED_CALL_SITES = 75;

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

function roleValue(opening: ts.JsxOpeningLikeElement, source: ts.SourceFile): 'dialog' | 'alertdialog' | null {
  const role = attributeValue(opening, 'role', source);
  return role === 'dialog' || role === 'alertdialog' ? role : null;
}

function hasAttribute(opening: ts.JsxOpeningLikeElement, name: string, source: ts.SourceFile): boolean {
  return opening.attributes.properties.some(
    (candidate) => ts.isJsxAttribute(candidate) && candidate.name.getText(source) === name,
  );
}

function attributeExpressionText(
  opening: ts.JsxOpeningLikeElement,
  name: string,
  source: ts.SourceFile,
): string | null {
  const attribute = opening.attributes.properties.find(
    (candidate): candidate is ts.JsxAttribute =>
      ts.isJsxAttribute(candidate) && candidate.name.getText(source) === name,
  );
  return attribute?.initializer && ts.isJsxExpression(attribute.initializer) && attribute.initializer.expression
    ? attribute.initializer.expression.getText(source)
    : null;
}

function modalValue(opening: ts.JsxOpeningLikeElement, source: ts.SourceFile): 'true' | 'missing' | 'invalid' {
  if (!hasAttribute(opening, 'aria-modal', source)) return 'missing';
  const modal = attributeValue(opening, 'aria-modal', source);
  return modal === true || modal === 'true' ? 'true' : 'invalid';
}

function hasAccessibleName(opening: ts.JsxOpeningLikeElement, source: ts.SourceFile): boolean {
  return opening.attributes.properties.some((candidate) => {
    if (!ts.isJsxAttribute(candidate)) return false;
    const name = candidate.name.getText(source);
    if (name !== 'aria-labelledby' && name !== 'aria-label') return false;
    const initializer = candidate.initializer;
    if (!initializer) return false;
    if (ts.isStringLiteral(initializer)) return initializer.text.trim().length > 0;
    if (!ts.isJsxExpression(initializer) || !initializer.expression) return false;
    const expression = initializer.expression;
    if (ts.isStringLiteral(expression) || ts.isNoSubstitutionTemplateLiteral(expression)) {
      return expression.text.trim().length > 0;
    }
    return expression.kind !== ts.SyntaxKind.FalseKeyword &&
      expression.kind !== ts.SyntaxKind.NullKeyword &&
      expression.getText(source).trim().length > 0;
  });
}

function isPartialSemanticSurface(opening: ts.JsxOpeningLikeElement, source: ts.SourceFile): boolean {
  return roleValue(opening, source) !== null || hasAttribute(opening, 'aria-modal', source);
}

function semanticDescendants(
  dialog: ts.JsxElement,
  source: ts.SourceFile,
  overlayNames: ReadonlySet<string>,
): ts.JsxOpeningLikeElement[] {
  const found: ts.JsxOpeningLikeElement[] = [];
  const visit = (node: ts.Node): void => {
    if (ts.isJsxElement(node)) {
      if (overlayNames.has(node.openingElement.tagName.getText(source))) return;
      if (isPartialSemanticSurface(node.openingElement, source)) {
        found.push(node.openingElement);
      }
    } else if (ts.isJsxSelfClosingElement(node) && isPartialSemanticSurface(node, source)) {
      found.push(node);
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

type AuditedEntry = Omit<ModalOverlayManifestEntry, 'family' | 'focus'>;

function auditModalOverlays(): AuditedEntry[] {
  const files = fs
    .readdirSync(SRC, { recursive: true, withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith('.tsx') && !entry.name.endsWith('.test.tsx'))
    .map((entry) => path.join(entry.parentPath, entry.name))
    .sort();
  const audited: AuditedEntry[] = [];

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
    const overlayStack: string[] = [];
    const visit = (node: ts.Node): void => {
      const opening = ts.isJsxElement(node)
        ? node.openingElement
        : ts.isJsxSelfClosingElement(node)
          ? node
          : null;
      if (opening && overlayNames.has(opening.tagName.getText(source))) {
        index += 1;
        const identity = `${path.relative(SRC, file)}#${index}`;
        const descendants = ts.isJsxElement(node) ? semanticDescendants(node, source, overlayNames) : [];
        const overlayIsSurface = isPartialSemanticSurface(opening, source);
        if (overlayIsSurface && descendants.length > 0) throw new Error(`${identity}: nested dialog semantics`);
        if (descendants.length > 1) throw new Error(`${identity}: multiple descendant dialog owners`);
        const semanticOpening = overlayIsSurface || descendants.length === 0 ? opening : descendants[0];
        audited.push({
          identity,
          owner: descendants.length === 1 ? 'descendant' : 'overlay',
          role: roleValue(semanticOpening, source),
          modal: modalValue(semanticOpening, source),
          name: hasAccessibleName(semanticOpening, source) ? 'named' : 'missing',
          relation: overlayStack.length ? `nested:${overlayStack[overlayStack.length - 1]}` : 'root',
        });
        overlayStack.push(identity);
        ts.forEachChild(node, visit);
        overlayStack.pop();
        return;
      }
      ts.forEachChild(node, visit);
    };
    visit(source);
  }
  return audited;
}

interface ManagedDialogContract {
  titleSymbol: string;
  containerSymbol: string;
  overlay: ts.JsxElement;
}

function managedDialogContract(identity: string): ManagedDialogContract {
  const relativeFile = identity.replace(/^components\//, '').replace(/#\d+$/, '');
  const wantedIndex = Number(identity.match(/#(\d+)$/)?.[1]);
  const file = path.join(SRC, 'components', relativeFile);
  const source = ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const overlayNames = modalOverlayNames(source);
  let overlayIndex = 0;
  let overlay: ts.JsxElement | undefined;
  const findOverlay = (node: ts.Node): void => {
    if (ts.isJsxElement(node) && overlayNames.has(node.openingElement.tagName.getText(source))) {
      overlayIndex += 1;
      if (overlayIndex === wantedIndex) overlay = node;
    }
    if (!overlay) ts.forEachChild(node, findOverlay);
  };
  findOverlay(source);
  if (!overlay) throw new Error(`${identity}: ModalOverlay not found`);

  let owner: ts.Node | undefined = overlay.parent;
  while (owner && !ts.isFunctionLike(owner)) owner = owner.parent;
  if (!owner) throw new Error(`${identity}: enclosing caller function not found`);
  let titleSymbol: string | undefined;
  let containerSymbol: string | undefined;
  const findBinding = (node: ts.Node): void => {
    if (ts.isFunctionLike(node) && node !== owner) return;
    if (ts.isVariableDeclaration(node) && ts.isObjectBindingPattern(node.name) &&
        node.initializer && ts.isCallExpression(node.initializer) &&
        node.initializer.expression.getText(source) === 'useOwnedDialog') {
      for (const element of node.name.elements) {
        const property = (element.propertyName ?? element.name).getText(source);
        const local = element.name.getText(source);
        if (property === 'titleId') titleSymbol = local;
        if (property === 'containerRef') containerSymbol = local;
      }
    }
    ts.forEachChild(node, findBinding);
  };
  findBinding(owner);
  if (!titleSymbol || !containerSymbol) throw new Error(`${identity}: bound useOwnedDialog result not found`);
  return { titleSymbol, containerSymbol, overlay };
}

describe('ModalOverlay caller semantics ledger', () => {
  it(
    'matches the exact reviewed ownership manifest with no unrecorded or stale callers',
    () => {
      const audit = auditModalOverlays();
      expect(audit).toHaveLength(EXPECTED_CALL_SITES);
      expect(MODAL_OVERLAY_MANIFEST).toHaveLength(EXPECTED_CALL_SITES);
      expect(new Set(MODAL_OVERLAY_MANIFEST.map((entry) => entry.identity)).size).toBe(EXPECTED_CALL_SITES);
      expect(audit).toEqual(MODAL_OVERLAY_MANIFEST.map(({ family: _family, focus: _focus, ...entry }) => entry));
      expect(MODAL_OVERLAY_MANIFEST.every((entry) => entry.owner === 'overlay' || entry.owner === 'descendant')).toBe(true);
    },
    // This authoritative census parses every production TSX file. V8 coverage
    // instrumentation can push it beyond Vitest's 5 s default on CI runners;
    // keep the complete AST scan and give this repository-wide assertion a
    // narrow, explicit budget instead of introducing a lossy lexical prefilter.
    15_000,
  );

  it('binds every managed-focus caller to one structural dialog contract', () => {
    for (const entry of MODAL_OVERLAY_MANIFEST.filter(({ focus }) => focus === 'managed-helper')) {
      // TypeToConfirmDialog is the foundation's direct helper integration; this
      // bounded contract covers the hr4ft.1 callers using useOwnedDialog.
      if (entry.identity === 'components/TypeToConfirmDialog.tsx#1') continue;
      const { titleSymbol, containerSymbol, overlay } = managedDialogContract(entry.identity);
      const source = overlay.getSourceFile();
      const opening = overlay.openingElement;
      expect(roleValue(opening, source), `${entry.identity}: overlay role`).toBe('dialog');
      expect(modalValue(opening, source), `${entry.identity}: overlay modal state`).toBe('true');
      expect(attributeExpressionText(opening, 'aria-labelledby', source), `${entry.identity}: label binding`)
        .toBe(titleSymbol);

      const container = overlay.children.find(ts.isJsxElement);
      expect(container, `${entry.identity}: semantic container`).toBeDefined();
      expect(attributeExpressionText(container!.openingElement, 'ref', source), `${entry.identity}: focus container`)
        .toBe(containerSymbol);
      const headingIds: string[] = [];
      const visit = (node: ts.Node): void => {
        if (ts.isJsxElement(node) && /^h[1-6]$/.test(node.openingElement.tagName.getText(source))) {
          const id = attributeExpressionText(node.openingElement, 'id', source);
          if (id) headingIds.push(id);
        }
        ts.forEachChild(node, visit);
      };
      overlay.children.forEach(visit);
      expect(headingIds, `${entry.identity}: visible heading ID must resolve the overlay label`).toContain(titleSymbol);
    }
  });

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

  it.each([
    ['role only', '<div role="dialog" />', 'dialog', 'missing', 'missing'],
    ['modal only', '<div aria-modal="true" />', null, 'true', 'missing'],
    ['empty aria-label', '<div role="dialog" aria-modal="true" aria-label="" />', 'dialog', 'true', 'missing'],
    ['empty labelledby', '<div role="dialog" aria-modal="true" aria-labelledby={""} />', 'dialog', 'true', 'missing'],
    ['valid static label', '<div role="dialog" aria-modal="true" aria-label="Named" />', 'dialog', 'true', 'named'],
    ['valid reference', '<div role="alertdialog" aria-modal="true" aria-labelledby={titleId} />', 'alertdialog', 'true', 'named'],
    ['invalid modal', '<div role="dialog" aria-modal="false" aria-label="Named" />', 'dialog', 'invalid', 'named'],
  ] as const)('extracts %s role, modal, and name independently', (_case, jsx, role, modal, name) => {
    const source = ts.createSourceFile('surface.tsx', jsx, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    let opening: ts.JsxOpeningLikeElement | undefined;
    const visit = (node: ts.Node): void => {
      if (ts.isJsxElement(node)) opening = node.openingElement;
      if (ts.isJsxSelfClosingElement(node)) opening = node;
      ts.forEachChild(node, visit);
    };
    visit(source);
    expect(opening).toBeDefined();
    expect(roleValue(opening!, source)).toBe(role);
    expect(modalValue(opening!, source)).toBe(modal);
    expect(hasAccessibleName(opening!, source) ? 'named' : 'missing').toBe(name);
  });

  it.each([
    '<div role="dialog" />',
    '<div role="alertdialog" aria-modal="false" />',
    '<div aria-modal="true" />',
  ])('treats a partial descendant semantic surface as owned debt: %s', (descendant) => {
    const source = ts.createSourceFile(
      'descendant.tsx',
      `import { ModalOverlay } from './ModalOverlay'; <ModalOverlay onClose={() => {}}>${descendant}</ModalOverlay>;`,
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TSX,
    );
    let overlay: ts.JsxElement | undefined;
    const visit = (node: ts.Node): void => {
      if (ts.isJsxElement(node) && node.openingElement.tagName.getText(source) === 'ModalOverlay') overlay = node;
      ts.forEachChild(node, visit);
    };
    visit(source);
    expect(semanticDescendants(overlay!, source, new Set(['ModalOverlay']))).toHaveLength(1);
  });
});
