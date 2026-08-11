/// <reference types="node" />
import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

type Decision = {
  selector: string;
  elements: number;
  role: 'body' | 'label' | 'item-title';
  files: string[];
};

// Reviewed disposition of the 101 bare 16px elements measured on 8rszp.
// Counts are rendered catalog instances, not unique source nodes: the harness
// deliberately renders TaskEditorModal in three states. Inline strong/span
// elements inherit from the semantic owner named here.
const DECISIONS: Decision[] = [
  { selector: '.guide-migration-modal > .modal-body > p', elements: 1, role: 'body', files: ['components/tabs/EPGManagerTab.css'] },
  { selector: ':where(.bulk-rule-settings-modal) .bulk-apply-row', elements: 6, role: 'label', files: ['components/channelPipeline/BulkRuleSettingsModal.css'] },
  { selector: '.event-sync-autosync-fix-dialog > .modal-body > p', elements: 3, role: 'body', files: ['components/channelPipeline/EventSyncAutoSyncFixDialog.css'] },
  { selector: '.event-sync-review-row dt', elements: 3, role: 'item-title', files: ['components/channelPipeline/EventSyncRuleEditor.css'] },
  { selector: '.rule-active-window legend', elements: 2, role: 'label', files: ['components/channelPipeline/RuleBuilder.css'] },
  { selector: ':is(.event-sync-details, .modal-subgroup) > summary', elements: 11, role: 'label', files: ['components/ModalBase.css'] },
  { selector: '.rule-builder-enabled', elements: 1, role: 'label', files: ['components/channelPipeline/RuleBuilder.css'] },
  { selector: '.task-editor-modal .enable-label,\n.task-editor-modal .alert-toggle,\n.schedule-editor .config-section > label,\n.task-editor-modal .schedules-header label', elements: 35, role: 'label', files: ['components/TaskEditorModal.css'] },
  { selector: '.cloud-target-delete-confirm > .modal-body > p', elements: 2, role: 'body', files: ['components/settings/CloudTargetsCard.css'] },
  { selector: '.dummy-epg-delete-confirm > .modal-body > p', elements: 3, role: 'body', files: ['components/DummyEPGManagerSection.css'] },
  { selector: '.delete-confirm-modal > .modal-body > p', elements: 2, role: 'body', files: ['components/tabs/LogoManagerTab.css'] },
  { selector: '.logo-manager-tab .source-load-status', elements: 1, role: 'label', files: ['components/tabs/LogoManagerTab.css'] },
  { selector: '.norm-engine-apply-confirm .modal-body p', elements: 4, role: 'body', files: ['components/settings/NormalizationEngineSection.css'] },
  { selector: '.pending-merge-bulk-confirm > .modal-body > p', elements: 3, role: 'body', files: ['components/tabs/PendingMergesPage.css'] },
  { selector: '.plex-token-modal .plex-token-steps', elements: 8, role: 'body', files: ['components/tabs/SettingsTab.css'] },
  { selector: '.conflict-message', elements: 4, role: 'body', files: ['components/ChannelsPane.css'] },
  { selector: '.pipeline-delete-confirm > .modal-body > p', elements: 1, role: 'body', files: ['components/channelPipeline/ChannelPipelineTab.css'] },
  { selector: '.event-sync-run-confirm > .modal-body,\n.pipeline-rollback-confirm > .modal-body', elements: 7, role: 'body', files: ['components/channelPipeline/ChannelPipelineTab.css'] },
  { selector: ':where(.channel-pipeline-tab) .detail-row', elements: 4, role: 'body', files: ['components/channelPipeline/ChannelPipelineTab.css'] },
];

// Exact measured rows assigned to the semantic owners above. This is kept
// separate from the aggregate counts so a count-preserving substitution cannot
// make a new residual disappear behind an unchanged total.
const ROW_OWNER: Record<string, string> = {
  'guide-migration|p|1': '.guide-migration-modal > .modal-body > p',
  'cp-bulk-rule-settings|span|6': ':where(.bulk-rule-settings-modal) .bulk-apply-row',
  'cp-event-sync-autosync-fix|p|1': '.event-sync-autosync-fix-dialog > .modal-body > p',
  'cp-event-sync-autosync-fix|strong|2': '.event-sync-autosync-fix-dialog > .modal-body > p',
  'cp-event-sync-rule-editor|dt|3': '.event-sync-review-row dt',
  'cp-event-sync-rule-editor|legend|1': '.rule-active-window legend',
  'cp-event-sync-rule-editor|summary|11': ':is(.event-sync-details, .modal-subgroup) > summary',
  'cp-rule-builder|legend|1': '.rule-active-window legend',
  'cp-rule-builder|span|1': '.rule-builder-enabled',
  'task-editor|label|1': '.task-editor-modal .enable-label,\n.task-editor-modal .alert-toggle,\n.schedule-editor .config-section > label,\n.task-editor-modal .schedules-header label',
  'task-editor|span|7': '.task-editor-modal .enable-label,\n.task-editor-modal .alert-toggle,\n.schedule-editor .config-section > label,\n.task-editor-modal .schedules-header label',
  'task-editor|span|3': '.task-editor-modal .enable-label,\n.task-editor-modal .alert-toggle,\n.schedule-editor .config-section > label,\n.task-editor-modal .schedules-header label',
  'task-schedule-add|label|2': '.task-editor-modal .enable-label,\n.task-editor-modal .alert-toggle,\n.schedule-editor .config-section > label,\n.task-editor-modal .schedules-header label',
  'task-schedule-add|span|7': '.task-editor-modal .enable-label,\n.task-editor-modal .alert-toggle,\n.schedule-editor .config-section > label,\n.task-editor-modal .schedules-header label',
  'task-schedule-add|span|3': '.task-editor-modal .enable-label,\n.task-editor-modal .alert-toggle,\n.schedule-editor .config-section > label,\n.task-editor-modal .schedules-header label',
  'task-schedule-edit|label|2': '.task-editor-modal .enable-label,\n.task-editor-modal .alert-toggle,\n.schedule-editor .config-section > label,\n.task-editor-modal .schedules-header label',
  'task-schedule-edit|span|7': '.task-editor-modal .enable-label,\n.task-editor-modal .alert-toggle,\n.schedule-editor .config-section > label,\n.task-editor-modal .schedules-header label',
  'task-schedule-edit|span|3': '.task-editor-modal .enable-label,\n.task-editor-modal .alert-toggle,\n.schedule-editor .config-section > label,\n.task-editor-modal .schedules-header label',
  'cloud-targets-card-delete|p|1': '.cloud-target-delete-confirm > .modal-body > p',
  'cloud-targets-card-delete|strong|1': '.cloud-target-delete-confirm > .modal-body > p',
  'dummy-epg-delete-confirm|p|2': '.dummy-epg-delete-confirm > .modal-body > p',
  'dummy-epg-delete-confirm|strong|1': '.dummy-epg-delete-confirm > .modal-body > p',
  'logo-delete-confirm|p|1': '.delete-confirm-modal > .modal-body > p',
  'logo-delete-confirm|span|1': '.logo-manager-tab .source-load-status',
  'logo-delete-confirm|strong|1': '.delete-confirm-modal > .modal-body > p',
  'norm-apply-confirm|p|1': '.norm-engine-apply-confirm .modal-body p',
  'norm-apply-confirm|strong|3': '.norm-engine-apply-confirm .modal-body p',
  'pending-merge-bulk|p|2': '.pending-merge-bulk-confirm > .modal-body > p',
  'pending-merge-bulk|strong|1': '.pending-merge-bulk-confirm > .modal-body > p',
  'settings-plex-token|li|5': '.plex-token-modal .plex-token-steps',
  'settings-plex-token|strong|3': '.plex-token-modal .plex-token-steps',
  'streams-bulk-create-conflict|p|2': '.conflict-message',
  'streams-bulk-create-conflict|strong|2': '.conflict-message',
  'cp-delete-confirm|p|1': '.pipeline-delete-confirm > .modal-body > p',
  'cp-execution-details|span|2': ':where(.channel-pipeline-tab) .detail-row',
  'cp-event-sync-run-confirm|p|3': '.event-sync-run-confirm > .modal-body,\n.pipeline-rollback-confirm > .modal-body',
  'cp-event-sync-run-confirm|strong|2': '.event-sync-run-confirm > .modal-body,\n.pipeline-rollback-confirm > .modal-body',
  'cp-rollback-confirm|p|1': '.event-sync-run-confirm > .modal-body,\n.pipeline-rollback-confirm > .modal-body',
  'cp-rollback-confirm|strong|1': '.event-sync-run-confirm > .modal-body,\n.pipeline-rollback-confirm > .modal-body',
  'cp-revert-result|span|2': ':where(.channel-pipeline-tab) .detail-row',
};

const TOKEN_BY_ROLE = {
  body: 'var(--type-body-size)',
  label: 'var(--type-label-size)',
  'item-title': 'var(--type-item-title-size)',
} as const;

const SRC = path.resolve(process.cwd(), 'src');

function cssFor(decision: Decision): string {
  return decision.files
    .map((file) => fs.readFileSync(path.join(SRC, file), 'utf8'))
    .join('\n');
}

function assertComplete(decisions: Decision[]): void {
  expect(decisions.reduce((sum, decision) => sum + decision.elements, 0)).toBe(101);
  for (const decision of decisions) {
    const assignedElements = Object.entries(ROW_OWNER)
      .filter(([, owner]) => owner === decision.selector)
      .reduce((sum, [row]) => sum + Number(row.split('|')[2]), 0);
    expect(assignedElements, `${decision.selector} measured count`).toBe(decision.elements);
    const css = cssFor(decision);
    expect(css, decision.selector).toContain(decision.selector);
    const start = css.indexOf(decision.selector);
    const block = css.slice(start, css.indexOf('}', start) + 1);
    expect(block, decision.selector).toContain(`font-size: ${TOKEN_BY_ROLE[decision.role]}`);
  }
}

function measuredRows(): string[] {
  const baseline = JSON.parse(
    fs.readFileSync(
      path.join(SRC, 'devHarness/baseline/modal-typography.baseline.json'),
      'utf8',
    ),
  ) as {
    dialogs: Record<string, { rows?: Array<{ signature: string; count: number; style: Record<string, string> }> }>;
  };
  return Object.entries(baseline.dialogs)
    .flatMap(([dialog, result]) =>
      (result.rows ?? [])
        .filter(
          (row) =>
            row.style['font-size'] === '16px' &&
            /^(span|strong|p|summary|label|li|dt|legend)$/.test(row.signature),
        )
        .map((row) => `${dialog}|${row.signature}|${row.count}`),
    )
    .sort();
}

describe('modal bare typography decision ledger (8rszp)', () => {
  it('accounts for all 101 measured elements and pins every semantic owner to its token', () => {
    assertComplete(DECISIONS);
    expect(Object.keys(ROW_OWNER).sort()).toEqual(measuredRows());
    expect(new Set(Object.values(ROW_OWNER))).toEqual(
      new Set(DECISIONS.map((decision) => decision.selector)),
    );
  });

  it('fails closed when an unreviewed measured element appears', () => {
    expect(() =>
      assertComplete([...DECISIONS, { ...DECISIONS[0], elements: 1 }]),
    ).toThrow();
    expect([...measuredRows(), 'synthetic-dialog|p|1'].sort()).not.toEqual(
      Object.keys(ROW_OWNER).sort(),
    );
  });

  it('folds the StreamCreateMenu empty state into the body scale', () => {
    const css = fs.readFileSync(path.join(SRC, 'components/StreamCreateMenu.css'), 'utf8');
    const start = css.indexOf('.stream-create-menu-empty');
    expect(css.slice(start, css.indexOf('}', start) + 1)).toContain(
      'font-size: var(--type-body-size)',
    );
  });
});
