/**
 * Declared coverage for the dev-only modal harness
 * (bead enhancedchannelmanager-xhldy.1).
 *
 * This file is NOT the source of truth for *which files contain dialogs* —
 * `discoverDialogs.ts` derives that from source text, and
 * `harnessCoverage.test.ts` fails if the `file` values below drift from what
 * the scan finds. This file is the source of truth for *what we do about
 * each one*: how many distinct dialogs it contains, what they are called, and
 * whether the harness can force-render each one or not.
 *
 * `status: 'gap'` is a first-class outcome. A dialog that cannot be rendered
 * without editing the component is recorded here with a reason and shows up
 * in the measurement output as an explicit hole, because the alternative —
 * quietly measuring most of them and reporting "all green" — is the failure
 * mode this whole harness exists to remove. As of the first baseline capture
 * there is exactly one gap, and it is not a dialog (see the last entry).
 *
 * NOTE ON THE COUNT. The brief and the parent bead both say "62 dialogs".
 * 62 is the number of FILES that match the four dialog markers, and that
 * number is correct — `harnessCoverage.test.ts` re-derives it. But several of
 * those files contain more than one dialog (ChannelPipelineTab has nine,
 * NormalizationEngineSection five) and one contains none of its own, so the
 * real dialog count is 83: the length of this array. Measuring "62" would
 * have measured about three quarters of the estate.
 */

export interface DialogCatalogEntry {
  /** Stable, URL-safe. `?dialog=<id>` addresses exactly this dialog. */
  readonly id: string
  /** Repo-relative path of the file that renders it. */
  readonly file: string
  /** Human label for the harness index. */
  readonly label: string
  readonly status: 'stubbed' | 'gap'
  /** Required when status is 'gap'. Why it cannot be force-rendered. */
  readonly reason?: string
  /**
   * How the dialog is reached. 'direct' = the exported component IS the
   * dialog. 'host' = the dialog is inline JSX inside a larger component and
   * the harness renders that host, then drives it open through the real UI.
   */
  readonly via?: 'direct' | 'host'
}

export const DIALOG_CATALOG = [
  // ---------------------------------------------------------------- direct
  { id: 'auto-sync-settings', file: 'src/components/AutoSyncSettingsModal.tsx', label: 'Auto-sync settings', status: 'stubbed', via: 'direct' },
  { id: 'backup-restore', file: 'src/components/BackupRestoreModal.tsx', label: 'Backup / restore', status: 'stubbed', via: 'direct' },
  { id: 'bulk-epg-assign', file: 'src/components/BulkEPGAssignModal.tsx', label: 'Bulk EPG assign', status: 'stubbed', via: 'direct' },
  { id: 'bulk-lcn-fetch', file: 'src/components/BulkLCNFetchModal.tsx', label: 'Bulk LCN fetch', status: 'stubbed', via: 'direct' },
  { id: 'channel-profiles-list', file: 'src/components/ChannelProfilesListModal.tsx', label: 'Channel profiles list', status: 'stubbed', via: 'direct' },
  { id: 'channel-stats-detail', file: 'src/components/ChannelStatsDetailModal.tsx', label: 'Channel stats detail', status: 'stubbed', via: 'direct' },
  { id: 'csv-import', file: 'src/components/CSVImportModal.tsx', label: 'CSV import', status: 'stubbed', via: 'direct' },
  { id: 'dbas-restore', file: 'src/components/DbasRestoreModal.tsx', label: 'DBAS restore (upload)', status: 'stubbed', via: 'direct' },
  { id: 'dbas-restore-saved', file: 'src/components/DbasRestoreSavedModal.tsx', label: 'DBAS restore (saved)', status: 'stubbed', via: 'direct' },
  { id: 'delete-orphaned-groups', file: 'src/components/DeleteOrphanedGroupsModal.tsx', label: 'Delete orphaned groups', status: 'stubbed', via: 'direct' },
  { id: 'dummy-epg-channel-picker', file: 'src/components/DummyEPGChannelPicker.tsx', label: 'Dummy EPG channel picker', status: 'stubbed', via: 'direct' },
  { id: 'dummy-epg-profile', file: 'src/components/DummyEPGProfileModal.tsx', label: 'Dummy EPG profile editor', status: 'stubbed', via: 'direct' },
  { id: 'dummy-epg-source', file: 'src/components/DummyEPGSourceModal.tsx', label: 'Dummy EPG source editor', status: 'stubbed', via: 'direct' },
  { id: 'edit-channel', file: 'src/components/EditChannelModal.tsx', label: 'Edit channel', status: 'stubbed', via: 'direct' },
  { id: 'find-duplicates', file: 'src/components/FindDuplicatesModal.tsx', label: 'Find duplicate channels', status: 'stubbed', via: 'direct' },
  { id: 'gracenote-conflict', file: 'src/components/GracenoteConflictModal.tsx', label: 'Gracenote conflict', status: 'stubbed', via: 'direct' },
  { id: 'group-multi-select-dropdown', file: 'src/components/GroupMultiSelectDropdown.tsx', label: 'Group multi-select (portal listbox)', status: 'stubbed', via: 'direct' },
  { id: 'guide-migration', file: 'src/components/GuideMigrationModal.tsx', label: 'Guide migration', status: 'stubbed', via: 'direct' },
  { id: 'import-dummy-epg', file: 'src/components/ImportDummyEPGModal.tsx', label: 'Import dummy EPG', status: 'stubbed', via: 'direct' },
  { id: 'logo-editor', file: 'src/components/LogoModal.tsx', label: 'Logo editor', status: 'stubbed', via: 'direct' },
  { id: 'm3u-account', file: 'src/components/M3UAccountModal.tsx', label: 'M3U account editor', status: 'stubbed', via: 'direct' },
  { id: 'm3u-filters', file: 'src/components/M3UFiltersModal.tsx', label: 'M3U filters', status: 'stubbed', via: 'direct' },
  { id: 'm3u-groups', file: 'src/components/M3UGroupsModal.tsx', label: 'M3U groups', status: 'stubbed', via: 'direct' },
  { id: 'm3u-linked-accounts', file: 'src/components/M3ULinkedAccountsModal.tsx', label: 'M3U linked accounts', status: 'stubbed', via: 'direct' },
  { id: 'm3u-profile', file: 'src/components/M3UProfileModal.tsx', label: 'M3U profile editor', status: 'stubbed', via: 'direct' },
  { id: 'merge-channels', file: 'src/components/MergeChannelsModal.tsx', label: 'Merge channels', status: 'stubbed', via: 'direct' },
  { id: 'normalize-names', file: 'src/components/NormalizeNamesModal.tsx', label: 'Normalize names', status: 'stubbed', via: 'direct' },
  { id: 'preview-stream', file: 'src/components/PreviewStreamModal.tsx', label: 'Preview stream', status: 'stubbed', via: 'direct' },
  { id: 'print-guide', file: 'src/components/PrintGuideModal.tsx', label: 'Print guide', status: 'stubbed', via: 'direct' },
  { id: 'security-first-run', file: 'src/components/SecurityFirstRunModal.tsx', label: 'Security first run', status: 'stubbed', via: 'direct' },
  { id: 'selection-action-bar', file: 'src/components/SelectionActionBar.tsx', label: 'Selection action bar (portal, role=dialog)', status: 'stubbed', via: 'direct' },
  { id: 'server-groups', file: 'src/components/ServerGroupsModal.tsx', label: 'Server groups', status: 'stubbed', via: 'direct' },
  { id: 'settings-modal', file: 'src/components/SettingsModal.tsx', label: 'Settings modal', status: 'stubbed', via: 'direct' },
  { id: 'stream-create-menu', file: 'src/components/StreamCreateMenu.tsx', label: 'Stream create menu (role=dialog popover)', status: 'stubbed', via: 'direct' },
  { id: 'stream-dedup', file: 'src/components/StreamDedupModal.tsx', label: 'Stream dedup prompt', status: 'stubbed', via: 'direct' },
  { id: 'stream-profiles-list', file: 'src/components/StreamProfilesListModal.tsx', label: 'Stream profiles list', status: 'stubbed', via: 'direct' },
  { id: 'type-to-confirm', file: 'src/components/TypeToConfirmDialog.tsx', label: 'Type-to-confirm', status: 'stubbed', via: 'direct' },
  { id: 'vlc-protocol-helper', file: 'src/components/VLCProtocolHelperModal.tsx', label: 'VLC protocol helper', status: 'stubbed', via: 'direct' },
  { id: 'cloud-target-editor', file: 'src/components/settings/CloudTargetEditor.tsx', label: 'Cloud target editor', status: 'stubbed', via: 'direct' },
  { id: 'cp-bulk-rule-settings', file: 'src/components/channelPipeline/BulkRuleSettingsModal.tsx', label: 'Pipeline: bulk rule settings', status: 'stubbed', via: 'direct' },
  { id: 'cp-circuit-breaker-banner', file: 'src/components/channelPipeline/CircuitBreakerBanner.tsx', label: 'Pipeline: circuit-breaker banner (role=dialog)', status: 'stubbed', via: 'direct' },
  { id: 'cp-event-sync-autosync-fix', file: 'src/components/channelPipeline/EventSyncAutoSyncFixDialog.tsx', label: 'Pipeline: event-sync auto-sync fix', status: 'stubbed', via: 'direct' },
  { id: 'cp-event-sync-rule-editor', file: 'src/components/channelPipeline/EventSyncRuleEditor.tsx', label: 'Pipeline: event-sync rule editor', status: 'stubbed', via: 'direct' },
  { id: 'cp-rule-builder', file: 'src/components/channelPipeline/RuleBuilder.tsx', label: 'Pipeline: rule builder', status: 'stubbed', via: 'direct' },
  { id: 'task-editor', file: 'src/components/TaskEditorModal.tsx', label: 'Task editor', status: 'stubbed', via: 'direct' },
  { id: 'task-schedule-add', file: 'src/components/TaskEditorModal.tsx', label: 'Task editor → add schedule', status: 'stubbed', via: 'host' },
  { id: 'task-schedule-edit', file: 'src/components/TaskEditorModal.tsx', label: 'Task editor → edit schedule', status: 'stubbed', via: 'host' },

  // ------------------------------------------------------------ host-driven
  { id: 'linked-accounts-link', file: 'src/components/settings/LinkedAccountsSection.tsx', label: 'Link identity provider', status: 'stubbed', via: 'host' },
  { id: 'lookup-table-editor', file: 'src/components/settings/LookupTableSection.tsx', label: 'Lookup table editor', status: 'stubbed', via: 'host' },
  { id: 'cloud-targets-card-delete', file: 'src/components/settings/CloudTargetsCard.tsx', label: 'Cloud targets → delete confirm', status: 'stubbed', via: 'host' },
  { id: 'dummy-epg-delete-confirm', file: 'src/components/DummyEPGManagerSection.tsx', label: 'Dummy EPG → delete confirm', status: 'stubbed', via: 'host' },
  { id: 'dummy-epg-export', file: 'src/components/DummyEPGManagerSection.tsx', label: 'Dummy EPG → export', status: 'stubbed', via: 'host' },
  { id: 'dummy-epg-import-yaml', file: 'src/components/DummyEPGManagerSection.tsx', label: 'Dummy EPG → import YAML', status: 'stubbed', via: 'host' },
  { id: 'logo-delete-confirm', file: 'src/components/tabs/LogoManagerTab.tsx', label: 'Logo manager → delete confirm', status: 'stubbed', via: 'host' },
  { id: 'epg-source-modal', file: 'src/components/tabs/EPGManagerTab.tsx', label: 'EPG source editor', status: 'stubbed', via: 'host' },
  { id: 'scheduled-tasks-run', file: 'src/components/ScheduledTasksSection.tsx', label: 'Scheduled tasks → run now', status: 'stubbed', via: 'host' },
  { id: 'history-checkpoint-name', file: 'src/components/HistoryToolbar.tsx', label: 'History → name checkpoint', status: 'stubbed', via: 'host' },
  { id: 'tag-import', file: 'src/components/settings/TagEngineSection.tsx', label: 'Tag engine → import', status: 'stubbed', via: 'host' },
  { id: 'tag-create-group', file: 'src/components/settings/TagEngineSection.tsx', label: 'Tag engine → create group', status: 'stubbed', via: 'host' },
  { id: 'norm-rule-editor', file: 'src/components/settings/NormalizationEngineSection.tsx', label: 'Normalization → rule editor', status: 'stubbed', via: 'host' },
  { id: 'norm-group-editor', file: 'src/components/settings/NormalizationEngineSection.tsx', label: 'Normalization → group editor', status: 'stubbed', via: 'host' },
  { id: 'norm-import', file: 'src/components/settings/NormalizationEngineSection.tsx', label: 'Normalization → import', status: 'stubbed', via: 'host' },
  { id: 'norm-apply', file: 'src/components/settings/NormalizationEngineSection.tsx', label: 'Normalization → apply to channels', status: 'stubbed', via: 'host' },
  { id: 'norm-apply-confirm', file: 'src/components/settings/NormalizationEngineSection.tsx', label: 'Normalization → apply confirm (alertdialog)', status: 'stubbed', via: 'host' },
  { id: 'pending-merge-bulk', file: 'src/components/tabs/PendingMergesPage.tsx', label: 'Pending merges → bulk confirm', status: 'stubbed', via: 'host' },
  { id: 'user-profile', file: 'src/components/UserMenu.tsx', label: 'User menu → profile', status: 'stubbed', via: 'host' },
  { id: 'user-password', file: 'src/components/UserMenu.tsx', label: 'User menu → change password', status: 'stubbed', via: 'host' },
  { id: 'settings-plex-token', file: 'src/components/tabs/SettingsTab.tsx', label: 'Settings → Plex token help', status: 'stubbed', via: 'host' },
  { id: 'settings-probe-results', file: 'src/components/tabs/SettingsTab.tsx', label: 'Settings → probe results', status: 'stubbed', via: 'host' },
  { id: 'settings-reorder', file: 'src/components/tabs/SettingsTab.tsx', label: 'Settings → reorder', status: 'stubbed', via: 'host' },
  { id: 'channels-pane-renumber-all', file: 'src/components/ChannelsPane.tsx', label: 'Channels pane → renumber all groups', status: 'stubbed', via: 'host' },
  { id: 'streams-bulk-create', file: 'src/components/StreamsPane.tsx', label: 'Streams pane → bulk create channels', status: 'stubbed', via: 'host' },
  { id: 'streams-bulk-create-conflict', file: 'src/components/StreamsPane.tsx', label: 'Streams pane → bulk create conflict', status: 'stubbed', via: 'host' },
  { id: 'cp-rule-builder-modal', file: 'src/components/channelPipeline/ChannelPipelineTab.tsx', label: 'Pipeline tab → rule builder modal shell', status: 'stubbed', via: 'host' },
  { id: 'cp-delete-confirm', file: 'src/components/channelPipeline/ChannelPipelineTab.tsx', label: 'Pipeline tab → delete rule confirm', status: 'stubbed', via: 'host' },
  { id: 'cp-import-dialog', file: 'src/components/channelPipeline/ChannelPipelineTab.tsx', label: 'Pipeline tab → import rules', status: 'stubbed', via: 'host' },
  { id: 'cp-export-dialog', file: 'src/components/channelPipeline/ChannelPipelineTab.tsx', label: 'Pipeline tab → export rules', status: 'stubbed', via: 'host' },
  { id: 'cp-execution-details', file: 'src/components/channelPipeline/ChannelPipelineTab.tsx', label: 'Pipeline tab → execution details', status: 'stubbed', via: 'host' },
  { id: 'cp-event-sync-run-confirm', file: 'src/components/channelPipeline/ChannelPipelineTab.tsx', label: 'Pipeline tab → event-sync run confirm', status: 'stubbed', via: 'host' },
  { id: 'cp-rollback-confirm', file: 'src/components/channelPipeline/ChannelPipelineTab.tsx', label: 'Pipeline tab → rollback confirm', status: 'stubbed', via: 'host' },
  { id: 'cp-revert-confirm', file: 'src/components/channelPipeline/ChannelPipelineTab.tsx', label: 'Pipeline tab → undo this run confirm', status: 'stubbed', via: 'host' },
  { id: 'cp-revert-result', file: 'src/components/channelPipeline/ChannelPipelineTab.tsx', label: 'Pipeline tab → revert complete summary', status: 'stubbed', via: 'host' },

  // --------------------------------------------------------- not a dialog
  { id: 'modal-overlay-base', file: 'src/components/ModalOverlay.tsx', label: 'ModalOverlay (shared wrapper, renders no dialog of its own)', status: 'gap', reason: 'Not a dialog. It is the shared overlay wrapper every other dialog composes; it contributes the .modal-overlay box and nothing else. Matched the marker scan because it names ModalOverlay. Measured indirectly by every stubbed entry above.', via: 'direct' },
] as const satisfies readonly DialogCatalogEntry[]

export type DialogId = (typeof DIALOG_CATALOG)[number]['id']
export type StubbedDialogId = Extract<(typeof DIALOG_CATALOG)[number], { status: 'stubbed' }>['id']

/** Distinct source files declared above — compared against the source scan. */
export function catalogFiles(): string[] {
  return [...new Set(DIALOG_CATALOG.map((e) => e.file))].sort()
}

export function catalogEntry(id: string): DialogCatalogEntry | undefined {
  return DIALOG_CATALOG.find((e) => e.id === id)
}
