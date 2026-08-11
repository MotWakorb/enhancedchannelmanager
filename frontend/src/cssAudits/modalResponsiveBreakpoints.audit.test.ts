import fs from 'node:fs'
import path from 'node:path'
import postcss from 'postcss'
import { describe, expect, it } from 'vitest'

const SRC = path.resolve(process.cwd(), 'src/components')

type MediaBlock = { file: string; query: string; selectors: string[] }

function filesWithExtension(dir: string, extension: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const target = path.join(dir, entry.name)
    if (entry.isDirectory()) return filesWithExtension(target, extension)
    return entry.isFile() && entry.name.endsWith(extension) ? [target] : []
  })
}

function modalStyleFiles(): string[] {
  const styles = new Set<string>(['ModalBase.css'])
  for (const file of filesWithExtension(SRC, '.tsx')) {
    const source = fs.readFileSync(file, 'utf8')
    if (!source.includes('<ModalOverlay')) continue
    for (const match of source.matchAll(/import\s+['"]([^'"]+\.css)['"]/g)) {
      const imported = path.resolve(path.dirname(file), match[1])
      const relative = path.relative(SRC, imported).split(path.sep).join('/')
      if (relative.startsWith('../')) throw new Error(`ModalOverlay style escapes component root: ${relative}`)
      styles.add(relative)
    }
  }
  return [...styles].sort()
}

function modalMediaBlocks(): MediaBlock[] {
  return modalStyleFiles().flatMap((relative) => {
    const file = path.join(SRC, relative)
    const root = postcss.parse(fs.readFileSync(file, 'utf8'), { from: file })
    const blocks: MediaBlock[] = []
    root.walkAtRules('media', (rule) => {
      const selectors: string[] = []
      rule.walkRules((child) => { selectors.push(child.selector) })
      blocks.push({ file: relative, query: rule.params, selectors })
    })
    return blocks
  })
}

const EXPECTED: MediaBlock[] = [
  { file: 'BulkLCNFetchModal.css', query: '(max-width: 500px)', selectors: ['.lcn-item-content .channel-name'] },
  { file: 'DummyEPGChannelPicker.css', query: '(max-width: 700px)', selectors: ['.channel-picker-body'] },
  { file: 'LogoModal.css', query: '(max-width: 500px)', selectors: ['.modal-overlay .modal-container.logo-modal', '.drop-zone', '.drop-icon', '.file-preview', '.logo-modal .file-info'] },
  { file: 'M3UFiltersModal.css', query: '(max-width: 700px)', selectors: ['.filter-form .form-row', '.filters-header', '.filter-row', '.filter-pattern', '.filter-action', '.filter-order', '.filter-responsive-label'] },
  { file: 'M3UProfileModal.css', query: '(max-width: 700px)', selectors: ['.m3u-profile-modal', '.profile-card', '.profile-actions'] },
  { file: 'ModalBase.css', query: '(max-width: 700px)', selectors: ['.modal-overlay', '.modal-container', '.modal-container.modal-sm,\n  .modal-container.modal-md,\n  .modal-container.modal-lg,\n  .modal-container.modal-xl,\n  .modal-container.modal-xxl', '.modal-header', '.modal-body', '.modal-footer', '.modal-footer .modal-btn'] },
  { file: 'ModalBase.css', query: '(max-width: 700px)', selectors: ['.modal-form-row'] },
  { file: 'ModalBase.css', query: '(max-width: 820px)', selectors: ['.modal-twopane', '.modal-rail', '.modal-stepper'] },
  { file: 'PreviewStreamModal.css', query: '(max-width: 700px)', selectors: ['.preview-stream-info-header', '.preview-stream-status', '.preview-stream-metadata', '.fallback-buttons', '.fallback-buttons .modal-btn'] },
  { file: 'VLCProtocolHelperModal.css', query: '(max-width: 700px)', selectors: ['.vlc-os-tabs', '.vlc-os-tab', '.vlc-code-block', '.vlc-code-block code'] },
  { file: 'channelPipeline/ChannelPipelineTab.css', query: '(max-width: 1280px), (max-height: 800px)', selectors: ['.channel-pipeline-secondary-action', '.channel-pipeline-compact-actions'] },
  { file: 'channelPipeline/ChannelPipelineTab.css', query: '(max-width: 480px)', selectors: ['.channel-pipeline-tab .header-actions', '.channel-pipeline-tab .btn', '.channel-pipeline-tab .btn span.material-icons', '.col-matches,\n  .col-priority'] },
  { file: 'channelPipeline/RuleBuilder.css', query: '(max-width: 700px)', selectors: ['.rule-builder-footer,\n  .rule-builder-footer-nav', '.rule-builder-footer .btn', '.rule-active-window-fields'] },
  { file: 'tabs/EPGManagerTab.css', query: '(max-width: 900px)', selectors: ['.epg-sources-list .list-header', '.epg-source-row', '.source-priority,\n  .source-status', '.source-stats,\n  .source-updated'] },
  { file: 'tabs/LogoManagerTab.css', query: '(max-width: 900px)', selectors: ['.logos-list .list-header', '.logo-row', '.logo-url-cell', '.logo-count'] },
  { file: 'tabs/LogoManagerTab.css', query: '(max-width: 600px)', selectors: ['.header-actions', '.search-box', '.logos-grid'] },
  { file: 'tabs/PendingMergesPage.css', query: '(max-width: 768px)', selectors: ['.pending-merges-bulk-toolbar', '.pending-merges-bulk-buttons', '.pending-merges-row-main', '.pending-merges-candidate,\n  .pending-merges-actions'] },
  { file: 'tabs/SettingsTab.css', query: '(max-width: 1100px)', selectors: ['.settings-content'] },
  { file: 'tabs/SettingsTab.css', query: '(min-width: 1101px)', selectors: ['.settings-content .sticky-section-target'] },
  { file: 'tabs/SettingsTab.css', query: '(max-width: 850px)', selectors: ['.settings-pending-actions', '.settings-pending-actions > span'] },
  { file: 'tabs/SettingsTab.css', query: '(max-width: 600px)', selectors: [':where(.settings-tab) .form-row'] },
  { file: 'tabs/SettingsTab.css', query: '(max-width: 700px)', selectors: ['.theme-selector'] },
  { file: 'tabs/SettingsTab.css', query: '(max-width: 768px)', selectors: ['.settings-sidebar', '.settings-content'] },
  { file: 'tabs/SettingsTab.css', query: '(max-width: 768px)', selectors: [':where(.settings-tab) .form-row'] },
]

const EXPECTED_MODAL_STYLE_FILES = [
  'AutoSyncSettingsModal.css', 'BackupRestoreModal.css', 'BulkEPGAssignModal.css',
  'BulkLCNFetchModal.css', 'CSVImportModal.css', 'ChannelProfilesListModal.css',
  'ChannelStatsDetailModal.css', 'DbasRestoreModal.css', 'DeleteOrphanedGroupsModal.css',
  'DummyEPGChannelPicker.css', 'DummyEPGManagerSection.css', 'DummyEPGProfileModal.css',
  'DummyEPGSourceModal.css', 'FindDuplicatesModal.css', 'GracenoteConflictModal.css',
  'HistoryToolbar.css', 'ImportDummyEPGModal.css', 'LogoModal.css', 'M3UAccountModal.css',
  'M3UFiltersModal.css', 'M3UGroupsModal.css', 'M3ULinkedAccountsModal.css',
  'M3UProfileModal.css', 'MergeChannelsModal.css', 'ModalBase.css', 'NormalizeNamesModal.css',
  'PreviewStreamModal.css', 'PrintGuideModal.css', 'SecurityFirstRunModal.css',
  'ServerGroupsModal.css', 'SettingsModal.css', 'StreamDedupModal.css',
  'StreamProfilesListModal.css', 'StreamsPane.css', 'TaskEditorModal.css',
  'TypeToConfirmDialog.css', 'UserMenu.css', 'VLCProtocolHelperModal.css',
  'channelPipeline/BulkRuleSettingsModal.css', 'channelPipeline/ChannelPipelineTab.css',
  'channelPipeline/EventSyncAutoSyncFixDialog.css', 'channelPipeline/EventSyncRuleEditor.css',
  'channelPipeline/RuleBuilder.css', 'settings/CloudTargetsCard.css',
  'settings/LinkedAccountsSection.css', 'settings/NormalizationEngineSection.css',
  'settings/TagEngineSection.css', 'tabs/EPGManagerTab.css', 'tabs/LogoManagerTab.css',
  'tabs/PendingMergesPage.css', 'tabs/SettingsTab.css',
]

describe('modal responsive breakpoint contract', () => {
  it('bidirectionally pins every direct stylesheet imported by a ModalOverlay caller', () => {
    expect(modalStyleFiles()).toEqual(EXPECTED_MODAL_STYLE_FILES)
  })
  it('has exactly the reviewed 820 complex, 700 zoom/reflow, and 500 compact blocks', () => {
    expect(modalMediaBlocks()).toEqual(EXPECTED)
  })

  it('keeps the zoom tier above the 640 CSS-pixel viewport produced by 200% zoom', () => {
    const componentTierQueries = modalMediaBlocks().filter(({ file }) =>
      file === 'ModalBase.css' || /Modal\.css$/.test(file) || file === 'DummyEPGChannelPicker.css' || file === 'channelPipeline/RuleBuilder.css',
    ).map(({ query }) => query)
    expect(componentTierQueries).not.toContain('(max-width: 600px)')
    expect(componentTierQueries.filter((query) => query === '(max-width: 700px)')).toHaveLength(8)
  })

  it('pins the reviewed chrome and information-preserving declarations', () => {
    const modalBase = fs.readFileSync(path.join(SRC, 'ModalBase.css'), 'utf8')
    const filters = fs.readFileSync(path.join(SRC, 'M3UFiltersModal.css'), 'utf8')
    const filterComponent = fs.readFileSync(path.join(SRC, 'M3UFiltersModal.tsx'), 'utf8')
    const bulk = fs.readFileSync(path.join(SRC, 'BulkLCNFetchModal.css'), 'utf8')
    expect(modalBase).toContain('padding: 0.5rem 1rem;')
    expect(modalBase).toContain('padding: 0.875rem 1rem;')
    expect(modalBase).toContain('max-height: min(var(--modal-body-max-height), calc(95vh - 8rem));')
    expect(modalBase).toContain('min-height: 0;')
    expect(filters).toContain('grid-column: 1 / -1;')
    expect(filters).toContain('.filter-responsive-label {')
    expect(filters).not.toMatch(/\.filter-action,?\s*\n\s*\.filter-order\s*\{\s*display:\s*none/)
    expect(filterComponent).not.toMatch(/filter-responsive-label[^>]*aria-hidden/)
    expect(filterComponent).toContain('className="filters-list" role="table"')
    expect(bulk).toContain('max-width: min(200px, 30vw);')
  })
})
