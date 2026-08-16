/**
 * Force-render recipes for every catalogued dialog
 * (bead enhancedchannelmanager-xhldy.1).
 *
 * TWO SHAPES, ONE RULE
 * --------------------
 * `render` returns the REAL component. Nothing here reimplements a dialog's
 * markup, substitutes a simplified body, or injects styles — if a dialog
 * cannot be brought on screen without editing it, it is recorded as a gap in
 * `dialogCatalog.ts` instead of being approximated here.
 *
 *  - `via: 'direct'` — the exported component IS the dialog. Give it props.
 *  - `via: 'host'`   — the dialog is inline JSX inside a bigger component.
 *    Mount the host and drive its real controls with `open` steps. No state
 *    is reached into; the harness clicks what an operator would click.
 *
 * The `satisfies Record<StubbedDialogId, DialogRenderer>` at the bottom is
 * load-bearing: `tsc --noEmit` fails if a catalogue entry marked 'stubbed'
 * has no recipe here, or a recipe exists for an id that is not catalogued.
 */
import { AutoSyncSettingsModal } from '../components/AutoSyncSettingsModal'
import { BackupRestoreModal } from '../components/BackupRestoreModal'
import { BulkEPGAssignModal } from '../components/BulkEPGAssignModal'
import { BulkLCNFetchModal } from '../components/BulkLCNFetchModal'
import { ChannelProfilesListModal } from '../components/ChannelProfilesListModal'
import { ChannelStatsDetailModal } from '../components/ChannelStatsDetailModal'
import { CSVImportModal } from '../components/CSVImportModal'
import { DbasRestoreModal } from '../components/DbasRestoreModal'
import { DbasRestoreSavedModal } from '../components/DbasRestoreSavedModal'
import { DeleteOrphanedGroupsModal } from '../components/DeleteOrphanedGroupsModal'
import { DummyEPGChannelPicker } from '../components/DummyEPGChannelPicker'
import { DummyEPGManagerSection } from '../components/DummyEPGManagerSection'
import { DummyEPGProfileModal } from '../components/DummyEPGProfileModal'
import { DummyEPGSourceModal } from '../components/DummyEPGSourceModal'
import { EditChannelModal } from '../components/EditChannelModal'
import { EditModeRestoreDialog } from '../components/EditModeRestoreDialog'
import { FindDuplicatesModal } from '../components/FindDuplicatesModal'
import { GracenoteConflictModal } from '../components/GracenoteConflictModal'
import { GroupMultiSelectDropdown } from '../components/GroupMultiSelectDropdown'
import { GuideMigrationModal } from '../components/GuideMigrationModal'
import { HistoryToolbar } from '../components/HistoryToolbar'
import { ImportDummyEPGModal } from '../components/ImportDummyEPGModal'
import { LogoModal } from '../components/LogoModal'
import { M3UAccountModal } from '../components/M3UAccountModal'
import { M3UFiltersModal } from '../components/M3UFiltersModal'
import { M3UGroupsModal } from '../components/M3UGroupsModal'
import { M3ULinkedAccountsModal } from '../components/M3ULinkedAccountsModal'
import { M3UProfileModal } from '../components/M3UProfileModal'
import { MergeChannelsModal } from '../components/MergeChannelsModal'
import { NormalizeNamesModal } from '../components/NormalizeNamesModal'
import { PreviewStreamModal } from '../components/PreviewStreamModal'
import { PrintGuideModal } from '../components/PrintGuideModal'
import { ScheduledTasksSection } from '../components/ScheduledTasksSection'
import { SecurityFirstRunModal } from '../components/SecurityFirstRunModal'
import { SelectionActionBar } from '../components/SelectionActionBar'
import { ServerGroupsModal } from '../components/ServerGroupsModal'
import { SettingsModal } from '../components/SettingsModal'
import { StreamCreateMenu } from '../components/StreamCreateMenu'
import { StreamDedupModal } from '../components/StreamDedupModal'
import { StreamProfilesListModal } from '../components/StreamProfilesListModal'
import { StreamsPane } from '../components/StreamsPane'
import { TaskEditorModal } from '../components/TaskEditorModal'
import { TypeToConfirmDialog } from '../components/TypeToConfirmDialog'
import { UserMenu } from '../components/UserMenu'
import { VLCProtocolHelperModal } from '../components/VLCProtocolHelperModal'
import { BulkRuleSettingsModal } from '../components/channelPipeline/BulkRuleSettingsModal'
import { ChannelPipelineTab } from '../components/channelPipeline/ChannelPipelineTab'
import { ChannelsPane } from '../components/ChannelsPane'
import { SettingsTab } from '../components/tabs/SettingsTab'
import { CircuitBreakerBanner } from '../components/channelPipeline/CircuitBreakerBanner'
import { EventSyncAutoSyncFixDialog } from '../components/channelPipeline/EventSyncAutoSyncFixDialog'
import { EventSyncRuleEditor } from '../components/channelPipeline/EventSyncRuleEditor'
import { RuleBuilder } from '../components/channelPipeline/RuleBuilder'
import { CloudTargetEditor } from '../components/settings/CloudTargetEditor'
import { CloudTargetsCard } from '../components/settings/CloudTargetsCard'
import { LinkedAccountsSection } from '../components/settings/LinkedAccountsSection'
import { NormalizationEngineSection } from '../components/settings/NormalizationEngineSection'
import { TagEngineSection } from '../components/settings/TagEngineSection'
import { EPGManagerTab } from '../components/tabs/EPGManagerTab'
import { LogoManagerTab } from '../components/tabs/LogoManagerTab'
import { PendingMergesPage } from '../components/tabs/PendingMergesPage'

import type { StubbedDialogId } from './dialogCatalog'
import type { DialogRenderer } from './harnessTypes'
import * as stub from './stubData'

/* eslint-disable @typescript-eslint/no-explicit-any --
 * A handful of dialogs take prop shapes that are declared inline (not
 * exported) or come from generated API types with 30+ required fields whose
 * values are irrelevant to layout. Widening those single props is the
 * smallest possible concession; every dialog is still the real component
 * with real data, and the alternative (exporting internal types from
 * production components) would mean editing components to suit the harness,
 * which the brief explicitly forbids. */

const click = (text: string, extra: { selector?: string; nth?: number } = {}) =>
  ({ kind: 'click', text, ...extra }) as const

const RENDERERS = {
  // ================================================================ direct
  'auto-sync-settings': {
    render: () => (
      <AutoSyncSettingsModal
        isOpen
        onClose={stub.noop}
        onSave={stub.noop}
        groupName="UK | SPORTS"
        customProperties={null}
        epgSources={stub.epgSources}
        channelGroups={stub.channelGroups}
        channelProfiles={stub.channelProfiles}
        streamProfiles={stub.streamProfiles}
      />
    ),
  },

  'backup-restore': { render: () => <BackupRestoreModal onClose={stub.noop} /> },

  'bulk-epg-assign': {
    render: () => (
      <BulkEPGAssignModal
        isOpen
        selectedChannels={stub.channels}
        epgData={stub.epgEntries as any}
        epgSources={stub.epgSources}
        onClose={stub.noop}
        onAssign={stub.noop}
      />
    ),
  },

  'bulk-lcn-fetch': {
    render: () => (
      <BulkLCNFetchModal
        isOpen
        selectedChannels={stub.channels}
        epgData={stub.epgEntries as any}
        onClose={stub.noop}
        onAssign={stub.noop}
      />
    ),
  },

  'channel-profiles-list': {
    render: () => (
      <ChannelProfilesListModal
        isOpen
        onClose={stub.noop}
        onSaved={stub.noop}
        channels={stub.channels}
        channelGroups={stub.channelGroups}
      />
    ),
  },

  'channel-stats-detail': {
    render: () => (
      <ChannelStatsDetailModal
        channelId={1}
        uuid="11111111-1111-4111-8111-111111111111"
        name="BBC One HD (London)"
        onClose={stub.noop}
      />
    ),
  },

  'csv-import': { render: () => <CSVImportModal isOpen onClose={stub.noop} onSuccess={stub.noop} /> },

  'dbas-restore': { render: () => <DbasRestoreModal onClose={stub.noop} /> },

  'dbas-restore-saved': {
    render: () => <DbasRestoreSavedModal filename="ecm-backup-2026-07-28.zip" onClose={stub.noop} />,
  },

  'delete-orphaned-groups': {
    render: () => (
      <DeleteOrphanedGroupsModal
        isOpen
        onClose={stub.noop}
        onConfirm={stub.noop}
        groups={
          [
            { id: 11, name: 'UK | ENTERTAINMENT (orphaned)', channel_count: 0, stream_count: 0 },
            { id: 12, name: 'A Very Long Orphaned Group Name That Should Wrap', channel_count: 0, stream_count: 3 },
          ] as any
        }
      />
    ),
  },

  'dummy-epg-channel-picker': {
    render: () => (
      <DummyEPGChannelPicker
        isOpen
        profileId={1}
        profileName="24/7 filler — movies"
        onClose={stub.noop}
        onChanged={stub.noop}
      />
    ),
  },

  'dummy-epg-profile': {
    render: () => (
      <DummyEPGProfileModal isOpen profile={null} onClose={stub.noop} onSave={stub.noop} />
    ),
  },

  'dummy-epg-source': {
    render: () => (
      <DummyEPGSourceModal isOpen source={null} onClose={stub.noop} onSave={stub.asyncNoop} />
    ),
  },

  'edit-channel': {
    render: () => (
      <EditChannelModal
        channel={stub.channels[0]}
        logos={stub.logos}
        epgData={stub.epgEntries}
        epgSources={stub.epgSourceRefs}
        streamProfiles={stub.streamProfileRefs}
        onClose={stub.noop}
        onSave={stub.asyncNoop}
        onLogoCreate={async () => stub.logos[0]}
        onLogoUpload={async () => stub.logos[0]}
      />
    ),
  },

  // The offer an operator gets when a dead session left staged Edit Mode work
  // behind. Rendered with BOTH halves populated, because the half that is easy
  // to get wrong is the account of what could not be restored
  // (epic enhancedchannelmanager-r93hq).
  'edit-mode-restore': {
    render: () => (
      <EditModeRestoreDialog
        isOpen
        savedAt={Date.now() - 45 * 60 * 1000}
        restorable={[
          {
            id: 'op-1',
            timestamp: Date.now(),
            description: 'Rename "BBC One HD"',
            apiCall: { type: 'updateChannel', channelId: 1, data: { name: 'BBC One HD (London)' } },
            beforeSnapshot: [],
            afterSnapshot: [],
          },
          {
            id: 'op-2',
            timestamp: Date.now(),
            description: 'Create group "Drill Locals"',
            apiCall: { type: 'createGroup', name: 'Drill Locals', tempGroupId: -1000 },
            beforeSnapshot: [],
            afterSnapshot: [],
          },
        ]}
        dropped={[
          {
            id: 'op-3',
            type: 'updateChannel',
            description: 'Renumber "Sky Sports Main Event"',
            reason: 'channel-missing',
            detail: 'Channel "Sky Sports Main Event" (id 412) no longer exists.',
          },
          {
            id: 'op-4',
            type: 'reorderChannelStreams',
            description: 'Reorder streams on "ITV1 HD"',
            reason: 'stream-detached',
            detail: 'The streams on channel "ITV1 HD" (id 88) changed, so this reordering would drop or invent one.',
          },
        ]}
        withdrawnAcknowledgements={[
          {
            id: 'op-5',
            description: 'Changed channel number from 106 to 105',
            detail: 'The channels using number 105 changed while you were away, so your confirmation of that duplicate no longer applies. The number will be checked again before anything is applied.',
          },
        ]}
        onRestore={stub.noop}
        onDiscard={stub.noop}
      />
    ),
  },

  'find-duplicates': {
    render: () => <FindDuplicatesModal onClose={stub.noop} onMerged={stub.noop} />,
  },

  'gracenote-conflict': {
    render: () => (
      <GracenoteConflictModal
        isOpen
        conflicts={
          [
            {
              channel_id: 1,
              channel_name: 'BBC One HD (London)',
              existing_stationid: '12345',
              new_stationid: '67890',
              epg_name: 'BBC One HD',
            },
          ] as any
        }
        onResolve={stub.noop}
        onCancel={stub.noop}
      />
    ),
  },

  'group-multi-select-dropdown': {
    render: () => (
      <GroupMultiSelectDropdown
        options={stub.channelGroups.map((g) => ({ id: g.id, name: g.name, count: g.channel_count }))}
        selectedIds={[1]}
        onChange={stub.noop}
        label="Channel groups"
      />
    ),
    // The listbox is a portal that only exists once the collapsed button is
    // activated — the closed control renders no dialog at all.
    open: [click('', { selector: '.filter-dropdown-button' })],
    expect: '.filter-dropdown-menu, [role="listbox"], .group-multiselect-dropdown-menu',
  },

  'guide-migration': {
    render: () => (
      <GuideMigrationModal isOpen sources={stub.epgSources} onClose={stub.noop} onApplied={stub.noop} />
    ),
  },

  'import-dummy-epg': {
    render: () => <ImportDummyEPGModal isOpen onClose={stub.noop} onImport={stub.noop} />,
  },

  'logo-editor': {
    render: () => <LogoModal isOpen onClose={stub.noop} onSaved={stub.noop} logo={stub.logos[0]} />,
  },

  'm3u-account': {
    render: () => (
      <M3UAccountModal
        isOpen
        onClose={stub.noop}
        onSaved={stub.noop}
        account={stub.m3uAccounts[0]}
        serverGroups={stub.serverGroups}
      />
    ),
  },

  'm3u-filters': {
    render: () => (
      <M3UFiltersModal isOpen onClose={stub.noop} onSaved={stub.noop} account={stub.m3uAccounts[0]} />
    ),
  },

  'm3u-groups': {
    render: () => (
      <M3UGroupsModal
        isOpen
        onClose={stub.noop}
        onSaved={stub.noop}
        account={stub.m3uAccounts[0]}
        allAccounts={stub.m3uAccounts}
        epgSources={stub.epgSources}
        channelGroups={stub.channelGroups}
        channelProfiles={stub.channelProfiles}
        streamProfiles={stub.streamProfiles}
      />
    ),
  },

  'm3u-linked-accounts': {
    render: () => (
      <M3ULinkedAccountsModal
        isOpen
        onClose={stub.noop}
        onSave={stub.noop}
        accounts={stub.m3uAccounts}
        linkGroups={[[1, 2]]}
      />
    ),
  },

  'm3u-profile': {
    render: () => (
      <M3UProfileModal isOpen onClose={stub.noop} onSaved={stub.noop} account={stub.m3uAccounts[0]} />
    ),
  },

  'merge-channels': {
    render: () => (
      <MergeChannelsModal
        channels={stub.channels.slice(0, 2)}
        logos={stub.logos}
        epgData={stub.epgEntries}
        epgSources={stub.epgSourceRefs}
        channelGroups={stub.channelGroups}
        streamProfiles={stub.streamProfileRefs}
        streams={stub.streams}
        onClose={stub.noop}
        onMerged={stub.noop}
      />
    ),
  },

  'normalize-names': {
    render: () => (
      <NormalizeNamesModal channels={stub.channels} onConfirm={stub.noop} onCancel={stub.noop} />
    ),
  },

  'preview-stream': {
    render: () => (
      <PreviewStreamModal
        isOpen
        onClose={stub.noop}
        stream={stub.streams[0]}
        channel={stub.channels[0]}
        channelName="BBC One HD (London)"
        providerName="Primary Provider (EU edge)"
      />
    ),
  },

  'print-guide': {
    render: () => (
      <PrintGuideModal
        isOpen
        onClose={stub.noop}
        channelGroups={stub.channelGroups}
        channels={stub.channels}
      />
    ),
  },

  'security-first-run': { render: () => <SecurityFirstRunModal onClose={stub.noop} /> },

  'selection-action-bar': {
    render: () => (
      <SelectionActionBar
        selectedCount={3}
        onDelete={stub.noop}
        onProbe={stub.noop}
        onFindDuplicates={stub.noop}
        onRenumber={stub.noop}
        onAssignEPG={stub.noop}
        onMerge={stub.noop}
        onClear={stub.noop}
        groups={stub.channelGroups.map((g) => ({ id: g.id, name: g.name }))}
        onMoveToGroup={stub.noop}
        onNewGroup={stub.noop}
        onNormalize={stub.noop}
        onSetLogoFromM3U={stub.noop}
        onSetLogoFromEPG={stub.noop}
        onSortStreams={stub.noop}
        onFetchGracenote={stub.noop}
        profiles={stub.channelProfiles.map((p) => ({ id: p.id, name: p.name }))}
        onSetProfileVisibility={stub.noop}
      />
    ),
    // The file's only role="dialog" is the Move submenu inside the '⋮ More'
    // overflow; the bar itself is role="toolbar".
    open: [click('More selection actions'), click('Move to group')],
    expect: '.selection-bar-submenu, .selection-action-bar',
  },

  'server-groups': { render: () => <ServerGroupsModal onClose={stub.noop} onChanged={stub.noop} /> },

  'settings-modal': { render: () => <SettingsModal isOpen onClose={stub.noop} onSaved={stub.noop} /> },

  'stream-create-menu': {
    render: () => (
      <StreamCreateMenu
        groups={stub.channelGroups.map((g) => ({ id: g.id, name: g.name }))}
        onCreateInGroup={stub.noop}
        onCreateInNewGroup={stub.noop}
      />
    ),
    open: [click('', { selector: '.stream-create-menu-trigger' })],
    expect: '.stream-create-menu-panel',
  },

  'stream-dedup': {
    render: () => (
      <StreamDedupModal
        isOpen
        streamName="UK| BBC ONE FHD (BACKUP FEED, LONDON REGION)"
        candidate={
          {
            channel_id: '1',
            channel_name: 'BBC One HD (London)',
            score: 0.94,
            reason: 'Normalized names match after stripping the provider prefix and quality suffix.',
          } as any
        }
        trigger={'drop' as any}
        onMerge={stub.asyncNoop}
        onCreateNew={stub.asyncNoop}
        onCancel={stub.noop}
      />
    ),
  },

  'stream-profiles-list': {
    render: () => (
      <StreamProfilesListModal
        streamProfiles={stub.streamProfiles}
        onClose={stub.noop}
        onChanged={stub.noop}
      />
    ),
  },

  'type-to-confirm': {
    render: () => (
      <TypeToConfirmDialog
        title="Restore Backup"
        message="Restoring replaces every channel, group, and provider in this instance. There is no undo."
        confirmText="RESTORE"
        confirmLabel="Restore now"
        onCancel={stub.noop}
        onConfirm={stub.noop}
      />
    ),
  },

  'vlc-protocol-helper': {
    render: () => (
      <VLCProtocolHelperModal
        isOpen
        onClose={stub.noop}
        onDownloadM3U={stub.noop}
        streamName="BBC One HD (London)"
      />
    ),
  },

  'cloud-target-editor': {
    render: () => <CloudTargetEditor target={null} onClose={stub.noop} onSaved={stub.noop} />,
  },

  'cp-bulk-rule-settings': {
    render: () => (
      <BulkRuleSettingsModal
        isOpen
        onClose={stub.noop}
        selectedRuleIds={[1, 2]}
        rules={[] as any}
        onApply={stub.asyncNoop}
      />
    ),
  },

  'cp-circuit-breaker-banner': {
    render: () => (
      <CircuitBreakerBanner
        state={
          {
            state: 'open',
            // `disabled` gates the whole banner (it returns null otherwise) and
            // `reason: 'abandoned_run'` is what exposes the Reset control that
            // opens the role="dialog" confirm this entry is here to measure.
            disabled: true,
            reason: 'abandoned_run',
            open: true,
            tripped_at: '2026-07-29T12:00:00Z',
            consecutive_failures: 5,
            failure_count: 5,
            threshold: 5,
            cooldown_seconds: 900,
            last_error: 'Rule "Sports auto-create" raised 5 consecutive execution errors.',
          } as any
        }
        isAdmin
        onReset={stub.noop}
      />
    ),
    open: [click('reset')],
    expect: '[role="dialog"], .modal-container',
  },

  'cp-event-sync-autosync-fix': {
    render: () => (
      <EventSyncAutoSyncFixDialog
        target={
          {
            group_id: 2,
            group_name: 'Sports',
            rule_id: 2,
            rule_name: 'Event sync — Premier League',
          } as any
        }
        onCancel={stub.noop}
        onConfirm={stub.noop}
      />
    ),
  },

  'cp-event-sync-rule-editor': {
    render: () => <EventSyncRuleEditor onSave={stub.asyncNoop} onCancel={stub.noop} />,
    expect: '.event-sync-editor, .rule-builder, form, .modal-container, .form-group',
  },

  'cp-rule-builder': {
    render: () => <RuleBuilder onSave={stub.asyncNoop} onCancel={stub.noop} />,
    expect: '.rule-builder, form, .modal-container, .form-group',
  },

  'task-editor': {
    render: () => <TaskEditorModal task={stubTask()} onClose={stub.noop} onSaved={stub.noop} />,
  },

  // ============================================================ host-driven
  'task-schedule-add': {
    render: () => (
      <TaskEditorModal task={stubTask()} onClose={stub.noop} onSaved={stub.noop} openAddSchedule />
    ),
    expect: '.schedule-editor-modal .modal-container',
  },

  'task-schedule-edit': {
    render: () => <TaskEditorModal task={stubTask()} onClose={stub.noop} onSaved={stub.noop} />,
    open: [click('Edit')],
    expect: '.schedule-editor-modal .modal-container',
  },

  'linked-accounts-link': {
    render: () => <LinkedAccountsSection />,
    open: [click('', { selector: '.link-provider-button' })],
  },

  'cloud-targets-card-delete': {
    render: () => <CloudTargetsCard />,
    open: [click('', { selector: 'button[aria-label="Delete cloud target"]' })],
  },

  'dummy-epg-delete-confirm': {
    render: () => <DummyEPGManagerSection />,
    open: [click('Delete')],
  },

  'dummy-epg-export': {
    render: () => <DummyEPGManagerSection />,
    open: [click('Export')],
  },

  'dummy-epg-import-yaml': {
    render: () => <DummyEPGManagerSection />,
    open: [click('Import')],
  },

  'logo-delete-confirm': {
    render: () => <LogoManagerTab />,
    open: [click('Delete', { selector: '.logo-actions button, .action-btn, button' })],
    expect: '.delete-confirm-modal',
  },

  'epg-source-modal': {
    render: () => <EPGManagerTab />,
    open: [click('Add Standard EPG')],
  },

  'scheduled-tasks-run': {
    render: () => <ScheduledTasksSection userTimezone="Europe/London" />,
    open: [click('Run Now')],
  },

  'history-checkpoint-name': {
    render: () => (
      <HistoryToolbar
        canUndo
        canRedo
        undoCount={3}
        redoCount={1}
        lastChange={null}
        savePoints={[]}
        hasUnsavedChanges
        isOperationPending={false}
        onUndo={stub.noop}
        onRedo={stub.noop}
        onCreateSavePoint={stub.noop}
        onRevertToSavePoint={stub.noop}
        onDeleteSavePoint={stub.noop}
        isEditMode
      />
    ),
    open: [click('Create checkpoint')],
    expect: '.checkpoint-name-modal-overlay',
  },

  'tag-import': {
    render: () => <TagEngineSection />,
    open: [click('Import')],
  },

  'tag-create-group': {
    render: () => <TagEngineSection />,
    open: [click('New Group')],
    expect: '.tag-engine-group-modal',
  },

  'norm-rule-editor': {
    render: () => <NormalizationEngineSection />,
    // The Add Rule control lives inside a collapsed group card, so expand the
    // group first — the same two clicks an operator makes.
    open: [
      click('', { selector: '.norm-engine-group-header' }),
      click('', { selector: '.norm-engine-add-rule' }),
    ],
  },

  'norm-group-editor': {
    render: () => <NormalizationEngineSection />,
    open: [click('New Group')],
    expect: '.modal-container',
  },

  'norm-import': {
    render: () => <NormalizationEngineSection />,
    open: [click('Import')],
  },

  'norm-apply': {
    render: () => <NormalizationEngineSection />,
    open: [click('Apply to')],
  },

  'norm-apply-confirm': {
    render: () => <NormalizationEngineSection />,
    open: [click('Apply to'), click('Execute')],
    expect: '.norm-engine-apply-confirm, [role="alertdialog"]',
  },

  'pending-merge-bulk': {
    render: () => <PendingMergesPage />,
    // The bulk confirm is only reachable with rows selected, so tick a row
    // checkbox first — exactly the sequence an operator performs.
    open: [
      click('', { selector: '.pending-merges-list input[type="checkbox"]' }),
      click('Merge selected', { selector: '.pending-merges-bulk-toolbar button' }),
    ],
    expect: '#pending-merges-bulk-confirm-title, .modal-container',
  },

  'user-profile': {
    render: () => <UserMenu />,
    open: [click('', { selector: '.user-menu-trigger' }), click('Profile', { selector: '.user-menu-item' })],
    expect: '.user-modal-overlay',
  },

  'user-password': {
    render: () => <UserMenu />,
    open: [click('', { selector: '.user-menu-trigger' }), click('Password', { selector: '.user-menu-item' })],
    expect: '.user-modal-overlay',
  },

  'streams-bulk-create': {
    render: () => (
      <StreamsPane
        streams={stub.streams}
        providers={stub.m3uAccounts}
        streamGroups={[
          { name: 'UK | ENTERTAINMENT', count: 210 },
          { name: 'UK | SPORTS', count: 64 },
        ] as any}
        searchTerm=""
        onSearchChange={stub.noop}
        providerFilter={null}
        onProviderFilterChange={stub.noop}
        groupFilter={null}
        onGroupFilterChange={stub.noop}
        loading={false}
        channels={stub.channels}
        channelGroups={stub.channelGroups}
        channelProfiles={stub.channelProfiles}
        isEditMode
        externalTriggerManualEntry
        onBulkCreateFromGroup={stub.asyncNoop}
        onCreateChannel={stub.asyncNoop}
        onExternalTriggerHandled={stub.noop}
      />
    ),
    expect: '[aria-labelledby="bulk-create-modal-title"], #bulk-create-modal-title',
  },

  'cp-rule-builder-modal': {
    render: () => <ChannelPipelineTab />,
    open: [click('Create rule')],
    expect: '.rule-builder-modal, .modal-container',
  },

  'cp-delete-confirm': {
    render: () => <ChannelPipelineTab />,
    // Scoped to the rules table: the event-sync exclusions panel above it also
    // renders a Delete control, and an unscoped match hit that one instead.
    open: [click('Delete', { selector: '.rules-section button' })],
  },

  'cp-import-dialog': {
    render: () => <ChannelPipelineTab />,
    open: [click('Import')],
  },

  'cp-export-dialog': {
    render: () => <ChannelPipelineTab />,
    open: [click('Export')],
  },

  'cp-execution-details': {
    render: () => <ChannelPipelineTab />,
    open: [click('View details')],
  },

  'cp-event-sync-run-confirm': {
    render: () => <ChannelPipelineTab />,
    open: [click('Run Event sync')],
    expect: '[data-testid="event-sync-run-confirm"]',
  },

  'cp-rollback-confirm': {
    render: () => <ChannelPipelineTab />,
    // Rollback is only offered on the execution WITHOUT a pre-run snapshot.
    open: [click('Rollback')],
  },

  'cp-revert-confirm': {
    render: () => <ChannelPipelineTab />,
    // "Undo this run" is only offered on the execution WITH a snapshot.
    open: [click('Undo this run')],
  },

  'settings-probe-results': {
    render: () => <SettingsTab onSaved={stub.noop} initialSettingsPage="maintenance" />,
    open: [click('', { selector: '.probe-history-btn.failed' })],
    expect: '.probe-results-modal',
  },

  'settings-reorder': {
    render: () => <SettingsTab onSaved={stub.noop} initialSettingsPage="maintenance" />,
    open: [click('reorder')],
    expect: '.reorder-modal',
  },

  'settings-plex-token': {
    render: () => <SettingsTab onSaved={stub.noop} initialSettingsPage="integrations" />,
    open: [click('', { selector: '[data-testid="plex-token-help-link"]' })],
    expect: '.plex-token-modal',
  },

  'channels-pane-renumber-all': {
    render: () => (
      <ChannelsPane
        channelGroups={stub.channelGroups}
        channels={stub.channels}
        streams={stub.streams}
        providers={stub.m3uAccounts}
        selectedChannelId={null}
        onChannelSelect={stub.noop}
        onChannelUpdate={stub.noop}
        onChannelDrop={stub.noop}
        onBulkStreamDrop={stub.noop}
        onChannelReorder={stub.noop}
        onCreateChannel={async () => stub.channels[0]}
        onDeleteChannel={stub.asyncNoop}
        searchTerm=""
        onSearchChange={stub.noop}
        selectedGroups={[1, 2, 3]}
        onSelectedGroupsChange={stub.noop}
        loading={false}
        autoRenameChannelNumber={false}
        // The Renumber All Groups menu item is edit-mode-only
        // (ChannelsPane.tsx:492 gate), so the pane is mounted in edit mode.
        isEditMode
        logos={stub.logos}
        epgData={stub.epgEntries as any}
        epgSources={stub.epgSources}
        streamProfiles={stub.streamProfiles}
        channelProfiles={stub.channelProfiles}
      />
    ),
    open: [click('', { selector: '.pane-toolbar-menu-btn' }), click('Renumber')],
  },

  'streams-bulk-create-conflict': {
    render: () => (
      <StreamsPane
        streams={stub.streams}
        providers={stub.m3uAccounts}
        streamGroups={[{ name: 'UK | ENTERTAINMENT', count: 210 }] as any}
        searchTerm=""
        onSearchChange={stub.noop}
        providerFilter={null}
        onProviderFilterChange={stub.noop}
        groupFilter={null}
        onGroupFilterChange={stub.noop}
        loading={false}
        channels={stub.channels}
        channelGroups={stub.channelGroups}
        channelProfiles={stub.channelProfiles}
        isEditMode
        // NOT manual-entry mode: handleBulkCreate short-circuits past the
        // conflict check for manual entry (StreamsPane.tsx:1546), so the
        // conflict dialog is only reachable on the streams path.
        externalTriggerStreamIds={[1, 2, 3]}
        externalTriggerTargetGroupId={1}
        externalTriggerStartingNumber={101}
        onBulkCreateFromGroup={stub.asyncNoop}
        onCreateChannel={stub.asyncNoop}
        onExternalTriggerHandled={stub.noop}
        // The conflict confirm only appears when the parent reports a
        // collision, so this stub reports one unconditionally.
        onCheckConflicts={() => 4}
        // The push-down blast radius is always at least the conflict count and
        // usually larger, so the stub reports a larger figure.
        onCountPushDownShift={() => 37}
        onGetHighestChannelNumber={() => 2001}
      />
    ),
    // Label is 'Create N Channels' on the streams path.
    open: [click('Channels')],
    expect: '.conflict-dialog',
  },

  'cp-revert-result': {
    render: () => <ChannelPipelineTab />,
    // Two steps: open the confirm, then confirm it — the result summary only
    // exists once the restore POST has resolved.
    open: [click('Undo this run'), click('Confirm revert')],
    expect: '[aria-labelledby="revert-result-title"], #revert-result-title',
  },
} satisfies Record<StubbedDialogId, DialogRenderer>

function stubTask() {
  return {
    id: 1,
    task_name: 'Refresh M3U accounts',
    name: 'Refresh M3U accounts',
    task_type: 'refresh_m3u',
    status: 'success',
    enabled: true,
    last_run: '2026-07-29T12:00:00Z',
    next_run: '2026-07-30T00:00:00Z',
    last_duration_seconds: 42.5,
    last_error: null,
    description: 'Refreshes the provider playlist and reconciles stream membership.',
    schedules: [
      { id: 10, cron: '0 */6 * * *', enabled: true, description: 'Every six hours' },
    ],
  } as any
}

export const DIALOG_RENDERERS: Record<StubbedDialogId, DialogRenderer> = RENDERERS
