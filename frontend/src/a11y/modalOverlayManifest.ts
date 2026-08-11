/**
 * Closed, reviewable inventory of every production ModalOverlay caller.
 *
 * `role: null` and `name: 'missing'` are explicit current debt, not an
 * accessibility exception. Child beads of hr4ft replace those states as each
 * family is remediated. ModalOverlay itself remains neutral.
 */
export type ModalOverlayOwner = 'overlay' | 'descendant';
export type ModalOverlayRole = 'dialog' | 'alertdialog' | null;
export type ModalOverlayModal = 'true' | 'missing' | 'invalid';
export type ModalOverlayName = 'named' | 'missing';
export type ModalFocusPolicy = 'debt' | 'bespoke' | 'managed-helper';

export interface ModalOverlayManifestEntry {
  identity: string;
  owner: ModalOverlayOwner;
  role: ModalOverlayRole;
  modal: ModalOverlayModal;
  name: ModalOverlayName;
  relation: 'root' | `nested:${string}`;
  family: string;
  focus: ModalFocusPolicy;
}

export const MODAL_OVERLAY_MANIFEST: readonly ModalOverlayManifestEntry[] = [
  { identity: 'components/AutoSyncSettingsModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'standalone-settings', focus: 'debt' },
  { identity: 'components/BackupRestoreModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'standalone-backup', focus: 'debt' },
  { identity: 'components/BulkEPGAssignModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'standalone-channel', focus: 'debt' },
  { identity: 'components/BulkLCNFetchModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'standalone-channel', focus: 'debt' },
  { identity: 'components/CSVImportModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'standalone-import', focus: 'debt' },
  { identity: 'components/ChannelProfilesListModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'standalone-channel', focus: 'debt' },
  { identity: 'components/ChannelStatsDetailModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'standalone-channel', focus: 'debt' },
  { identity: 'components/DbasRestoreModal.tsx#1', owner: 'overlay', role: 'dialog', modal: 'true', name: 'named', relation: 'root', family: 'dbas-restore', focus: 'debt' },
  { identity: 'components/DbasRestoreSavedModal.tsx#1', owner: 'overlay', role: 'dialog', modal: 'true', name: 'named', relation: 'root', family: 'dbas-restore', focus: 'debt' },
  { identity: 'components/DeleteOrphanedGroupsModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'standalone-channel', focus: 'debt' },
  { identity: 'components/DummyEPGChannelPicker.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'dummy-epg', focus: 'debt' },
  { identity: 'components/DummyEPGManagerSection.tsx#1', owner: 'overlay', role: 'dialog', modal: 'true', name: 'missing', relation: 'root', family: 'dummy-epg-manager', focus: 'debt' },
  { identity: 'components/DummyEPGManagerSection.tsx#2', owner: 'overlay', role: 'dialog', modal: 'true', name: 'missing', relation: 'root', family: 'dummy-epg-manager', focus: 'debt' },
  { identity: 'components/DummyEPGManagerSection.tsx#3', owner: 'overlay', role: 'dialog', modal: 'true', name: 'missing', relation: 'root', family: 'dummy-epg-manager', focus: 'debt' },
  { identity: 'components/DummyEPGProfileModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'dummy-epg', focus: 'debt' },
  { identity: 'components/DummyEPGSourceModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'dummy-epg', focus: 'debt' },
  { identity: 'components/EditChannelModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'standalone-channel', focus: 'debt' },
  { identity: 'components/FindDuplicatesModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'standalone-channel', focus: 'debt' },
  { identity: 'components/GracenoteConflictModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'standalone-channel', focus: 'debt' },
  { identity: 'components/GuideMigrationModal.tsx#1', owner: 'overlay', role: 'dialog', modal: 'true', name: 'named', relation: 'root', family: 'guide-migration', focus: 'bespoke' },
  { identity: 'components/HistoryToolbar.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'history', focus: 'debt' },
  { identity: 'components/ImportDummyEPGModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'dummy-epg', focus: 'debt' },
  { identity: 'components/LogoModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'standalone-logo', focus: 'debt' },
  { identity: 'components/M3UAccountModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'm3u', focus: 'debt' },
  { identity: 'components/M3UFiltersModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'm3u', focus: 'debt' },
  { identity: 'components/M3UGroupsModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'm3u', focus: 'debt' },
  { identity: 'components/M3ULinkedAccountsModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'm3u', focus: 'debt' },
  { identity: 'components/M3UProfileModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'm3u', focus: 'debt' },
  { identity: 'components/MergeChannelsModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'standalone-channel', focus: 'debt' },
  { identity: 'components/NormalizeNamesModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'standalone-channel', focus: 'debt' },
  { identity: 'components/PreviewStreamModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'standalone-stream', focus: 'debt' },
  { identity: 'components/PrintGuideModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'standalone-guide', focus: 'debt' },
  { identity: 'components/SecurityFirstRunModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'named', relation: 'root', family: 'standalone-security', focus: 'debt' },
  { identity: 'components/ServerGroupsModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'standalone-server', focus: 'debt' },
  { identity: 'components/SettingsModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'standalone-settings', focus: 'debt' },
  { identity: 'components/StreamDedupModal.tsx#1', owner: 'overlay', role: 'dialog', modal: 'true', name: 'named', relation: 'root', family: 'standalone-stream', focus: 'bespoke' },
  { identity: 'components/StreamProfilesListModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'standalone-stream', focus: 'debt' },
  { identity: 'components/StreamsPane.tsx#1', owner: 'overlay', role: 'dialog', modal: 'true', name: 'named', relation: 'root', family: 'streams-pane', focus: 'debt' },
  { identity: 'components/StreamsPane.tsx#2', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'streams-pane', focus: 'debt' },
  { identity: 'components/TaskEditorModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'task-editor', focus: 'debt' },
  { identity: 'components/TaskEditorModal.tsx#2', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'nested:components/TaskEditorModal.tsx#1', family: 'task-editor', focus: 'debt' },
  { identity: 'components/TaskEditorModal.tsx#3', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'nested:components/TaskEditorModal.tsx#1', family: 'task-editor', focus: 'debt' },
  { identity: 'components/TypeToConfirmDialog.tsx#1', owner: 'overlay', role: 'dialog', modal: 'true', name: 'named', relation: 'root', family: 'type-to-confirm', focus: 'managed-helper' },
  { identity: 'components/UserMenu.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'user-menu', focus: 'debt' },
  { identity: 'components/UserMenu.tsx#2', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'user-menu', focus: 'debt' },
  { identity: 'components/VLCProtocolHelperModal.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'standalone-stream', focus: 'debt' },
  { identity: 'components/channelPipeline/BulkRuleSettingsModal.tsx#1', owner: 'overlay', role: 'dialog', modal: 'true', name: 'named', relation: 'root', family: 'channel-pipeline', focus: 'debt' },
  { identity: 'components/channelPipeline/ChannelPipelineTab.tsx#1', owner: 'overlay', role: 'dialog', modal: 'true', name: 'named', relation: 'root', family: 'channel-pipeline', focus: 'debt' },
  { identity: 'components/channelPipeline/ChannelPipelineTab.tsx#2', owner: 'overlay', role: 'dialog', modal: 'true', name: 'missing', relation: 'root', family: 'channel-pipeline', focus: 'debt' },
  { identity: 'components/channelPipeline/ChannelPipelineTab.tsx#3', owner: 'overlay', role: 'dialog', modal: 'true', name: 'named', relation: 'root', family: 'channel-pipeline', focus: 'debt' },
  { identity: 'components/channelPipeline/ChannelPipelineTab.tsx#4', owner: 'overlay', role: 'dialog', modal: 'true', name: 'missing', relation: 'root', family: 'channel-pipeline', focus: 'debt' },
  { identity: 'components/channelPipeline/ChannelPipelineTab.tsx#5', owner: 'overlay', role: 'dialog', modal: 'true', name: 'named', relation: 'root', family: 'channel-pipeline', focus: 'debt' },
  { identity: 'components/channelPipeline/ChannelPipelineTab.tsx#6', owner: 'overlay', role: 'dialog', modal: 'true', name: 'named', relation: 'root', family: 'channel-pipeline', focus: 'debt' },
  { identity: 'components/channelPipeline/ChannelPipelineTab.tsx#7', owner: 'overlay', role: 'dialog', modal: 'true', name: 'missing', relation: 'root', family: 'channel-pipeline', focus: 'debt' },
  { identity: 'components/channelPipeline/ChannelPipelineTab.tsx#8', owner: 'overlay', role: 'dialog', modal: 'true', name: 'missing', relation: 'root', family: 'channel-pipeline', focus: 'debt' },
  { identity: 'components/channelPipeline/ChannelPipelineTab.tsx#9', owner: 'overlay', role: 'dialog', modal: 'true', name: 'missing', relation: 'root', family: 'channel-pipeline', focus: 'debt' },
  { identity: 'components/channelPipeline/EventSyncAutoSyncFixDialog.tsx#1', owner: 'descendant', role: 'alertdialog', modal: 'true', name: 'missing', relation: 'root', family: 'channel-pipeline-confirm', focus: 'debt' },
  { identity: 'components/channelPipeline/EventSyncRuleEditor.tsx#1', owner: 'descendant', role: 'alertdialog', modal: 'true', name: 'missing', relation: 'root', family: 'channel-pipeline-confirm', focus: 'debt' },
  { identity: 'components/channelPipeline/RuleBuilder.tsx#1', owner: 'descendant', role: 'alertdialog', modal: 'true', name: 'missing', relation: 'root', family: 'channel-pipeline-confirm', focus: 'debt' },
  { identity: 'components/settings/CloudTargetEditor.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'settings-cloud', focus: 'debt' },
  { identity: 'components/settings/CloudTargetsCard.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'settings-cloud', focus: 'debt' },
  { identity: 'components/settings/LinkedAccountsSection.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'settings-linked', focus: 'debt' },
  { identity: 'components/settings/NormalizationEngineSection.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'settings-normalization', focus: 'debt' },
  { identity: 'components/settings/NormalizationEngineSection.tsx#2', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'settings-normalization', focus: 'debt' },
  { identity: 'components/settings/NormalizationEngineSection.tsx#3', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'settings-normalization', focus: 'debt' },
  { identity: 'components/settings/NormalizationEngineSection.tsx#4', owner: 'descendant', role: 'alertdialog', modal: 'true', name: 'named', relation: 'root', family: 'settings-normalization', focus: 'debt' },
  { identity: 'components/settings/NormalizationEngineSection.tsx#5', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'settings-normalization', focus: 'debt' },
  { identity: 'components/settings/TagEngineSection.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'settings-tag', focus: 'debt' },
  { identity: 'components/settings/TagEngineSection.tsx#2', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'settings-tag', focus: 'debt' },
  { identity: 'components/tabs/EPGManagerTab.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'tab-epg', focus: 'debt' },
  { identity: 'components/tabs/LogoManagerTab.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'tab-logo', focus: 'debt' },
  { identity: 'components/tabs/PendingMergesPage.tsx#1', owner: 'overlay', role: 'dialog', modal: 'true', name: 'named', relation: 'root', family: 'tab-pending-merges', focus: 'bespoke' },
  { identity: 'components/tabs/SettingsTab.tsx#1', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'tab-settings', focus: 'debt' },
  { identity: 'components/tabs/SettingsTab.tsx#2', owner: 'overlay', role: null, modal: 'missing', name: 'missing', relation: 'root', family: 'tab-settings', focus: 'debt' },
  { identity: 'components/tabs/SettingsTab.tsx#3', owner: 'descendant', role: 'dialog', modal: 'true', name: 'named', relation: 'root', family: 'tab-settings', focus: 'debt' },
] as const;
