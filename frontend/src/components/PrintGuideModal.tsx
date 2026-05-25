import { useState, useMemo, useCallback, memo } from 'react';
import type { Channel, ChannelGroup } from '../types';
import './ModalBase.css';
import './PrintGuideModal.css';
import { ModalOverlay } from './ModalOverlay';

// Clean channel name by removing channel number prefix
// e.g., "2.1 | ABC News" -> "ABC News", "102 - ESPN" -> "ESPN"
function cleanChannelName(name: string, channelNumber: number | null): string {
  if (!name) return 'Unknown Channel';

  // Remove patterns like "2.1 | ", "102 | ", "2.1 - ", "102 - ", "2.1: ", etc.
  let cleaned = name.replace(/^\d+(\.\d+)?\s*[-|:]\s*/i, '');

  // Also try removing just the channel number at the start if it matches
  if (channelNumber !== null) {
    const numStr = Number.isInteger(channelNumber) ? String(channelNumber) : String(channelNumber);
    // Match the channel number followed by optional separator
    const regex = new RegExp(`^${numStr.replace('.', '\\.')}\\s*[-|:]?\\s*`, 'i');
    cleaned = cleaned.replace(regex, '');
  }

  return cleaned.trim() || name;
}

// Color palette for group headers
const GROUP_COLORS = [
  { header: '#4A90E2', bg: '#E8F2FC' },  // Blue
  { header: '#50C878', bg: '#E8F8F0' },  // Emerald
  { header: '#9B59B6', bg: '#F4ECF7' },  // Purple
  { header: '#E67E22', bg: '#FDF2E9' },  // Orange
  { header: '#16A085', bg: '#E8F6F3' },  // Teal
  { header: '#C0392B', bg: '#FADBD8' },  // Red
  { header: '#F39C12', bg: '#FEF5E7' },  // Yellow
  { header: '#2C3E50', bg: '#EAF2F8' },  // Navy
  { header: '#D35400', bg: '#FBEEE6' },  // Pumpkin
  { header: '#8E44AD', bg: '#F5EEF8' },  // Violet
];

// Exported for unit testing of generatePrintHtml.
export interface GroupPrintSettings {
  groupId: number;
  selected: boolean;
  mode: 'detailed' | 'summary';
  /** Per-group range From (raw string for controlled input) */
  fromRaw: string;
  /** Per-group range To (raw string for controlled input) */
  toRaw: string;
}

/**
 * Per-group numeric range map consumed by generatePrintHtml.
 * Key: group id; value: { from, to } inclusive channel-number bounds.
 * Exported for unit testing.
 */
export type GroupRangeMap = Record<number, { from: number; to: number }>;

// Maximum empty-slot placeholders rendered per group to prevent runaway output
// (e.g. operator sets 1–9999 with only one real channel).
// Exported for unit testing.
export const MAX_EMPTY_SLOTS_PER_GROUP = 500;

interface PrintGuideModalProps {
  isOpen: boolean;
  onClose: () => void;
  channelGroups: ChannelGroup[];
  channels: Channel[];
  title?: string;
}

// Body runs only while open (outer wrapper unmounts on close) so per-open
// state (groupSettings) is seeded via useState initializer from the sorted
// groups — no effect/useMemo-as-effect needed.
function PrintGuideModalInner({
  onClose,
  channelGroups,
  channels,
  title = 'TV Channel Guide',
}: Omit<PrintGuideModalProps, 'isOpen'>) {
  // Empty-slot placeholder toggle — on by default so the printed guide shows
  // the full intended channel-number layout (bd-w532y).
  const [showEmptySlots, setShowEmptySlots] = useState(true);

  // Sort groups by first channel number in each group
  const sortedGroups = useMemo(() => {
    const groupFirstChannel = new Map<number, number>();
    channels.forEach(ch => {
      if (ch.channel_number !== null && ch.channel_group_id !== null) {
        const existing = groupFirstChannel.get(ch.channel_group_id);
        if (existing === undefined || ch.channel_number < existing) {
          groupFirstChannel.set(ch.channel_group_id, ch.channel_number);
        }
      }
    });

    return [...channelGroups]
      .filter(g => groupFirstChannel.has(g.id))
      .sort((a, b) => {
        const aFirst = groupFirstChannel.get(a.id) ?? Infinity;
        const bFirst = groupFirstChannel.get(b.id) ?? Infinity;
        return aFirst - bFirst;
      });
  }, [channelGroups, channels]);

  // Compute per-group min/max from the channels data.
  // Returns { min, max } for each group id that has numbered channels.
  const groupMinMax = useMemo(() => {
    const result = new Map<number, { min: number; max: number }>();
    channels.forEach(ch => {
      if (ch.channel_number !== null && ch.channel_group_id !== null) {
        const existing = result.get(ch.channel_group_id);
        if (existing === undefined) {
          result.set(ch.channel_group_id, { min: ch.channel_number, max: ch.channel_number });
        } else {
          if (ch.channel_number < existing.min) existing.min = ch.channel_number;
          if (ch.channel_number > existing.max) existing.max = ch.channel_number;
        }
      }
    });
    return result;
  }, [channels]);

  // Initialize group settings — all selected, detailed mode, per-group From/To
  // defaulting to each group's min/max used channel number. Since this component
  // remounts on each open (via outer wrapper), we can seed directly from the
  // first sorted list with a useState initializer.
  const [groupSettings, setGroupSettings] = useState<GroupPrintSettings[]>(() =>
    sortedGroups.map(g => {
      const mm = groupMinMax.get(g.id);
      const groupMin = mm?.min ?? 1;
      const groupMax = mm?.max ?? 9999;
      return {
        groupId: g.id,
        selected: true,
        mode: 'detailed',
        fromRaw: String(groupMin),
        toRaw: String(groupMax),
      };
    })
  );

  // Get settings for a specific group
  const getGroupSettings = (groupId: number): GroupPrintSettings => {
    const mm = groupMinMax.get(groupId);
    return groupSettings.find(s => s.groupId === groupId) ?? {
      groupId,
      selected: true,
      mode: 'detailed',
      fromRaw: String(mm?.min ?? 1),
      toRaw: String(mm?.max ?? 9999),
    };
  };

  // Toggle group selection
  const toggleGroup = useCallback((groupId: number) => {
    setGroupSettings(prev =>
      prev.map(s =>
        s.groupId === groupId ? { ...s, selected: !s.selected } : s
      )
    );
  }, []);

  // Set mode for a group
  const setGroupMode = useCallback((groupId: number, mode: 'detailed' | 'summary') => {
    setGroupSettings(prev =>
      prev.map(s =>
        s.groupId === groupId ? { ...s, mode } : s
      )
    );
  }, []);

  // Update per-group From raw value
  const setGroupFrom = useCallback((groupId: number, value: string) => {
    setGroupSettings(prev =>
      prev.map(s => s.groupId === groupId ? { ...s, fromRaw: value } : s)
    );
  }, []);

  // Update per-group To raw value
  const setGroupTo = useCallback((groupId: number, value: string) => {
    setGroupSettings(prev =>
      prev.map(s => s.groupId === groupId ? { ...s, toRaw: value } : s)
    );
  }, []);

  // Select/deselect all (operates on all groups)
  const toggleAll = useCallback(() => {
    const allSelected = groupSettings.every(s => s.selected);
    setGroupSettings(prev =>
      prev.map(s => ({ ...s, selected: !allSelected }))
    );
  }, [groupSettings]);

  const allSelected = useMemo(
    () => groupSettings.every(s => s.selected),
    [groupSettings]
  );

  // Get channel count and range for a group within its current per-group range
  const getGroupInfo = useCallback((groupId: number) => {
    const settings = getGroupSettings(groupId);
    const fromNum = parseInt(settings.fromRaw, 10);
    const toNum = parseInt(settings.toRaw, 10);
    const rangeValid = !Number.isNaN(fromNum) && !Number.isNaN(toNum) && fromNum <= toNum;

    if (!rangeValid) return { count: 0, first: null, last: null, rangeError: true };

    const groupChannels = channels
      .filter(
        ch =>
          ch.channel_group_id === groupId &&
          ch.channel_number !== null &&
          ch.channel_number >= fromNum &&
          ch.channel_number <= toNum
      )
      .sort((a, b) => (a.channel_number ?? 0) - (b.channel_number ?? 0));

    if (groupChannels.length === 0) {
      return { count: 0, first: null, last: null, rangeError: false };
    }

    return {
      count: groupChannels.length,
      first: groupChannels[0].channel_number,
      last: groupChannels[groupChannels.length - 1].channel_number,
      rangeError: false,
    };
  }, [channels, groupSettings]); // eslint-disable-line react-hooks/exhaustive-deps

  // Format channel number (remove .0 for whole numbers)
  const formatChannelNumber = (num: number | null): string => {
    if (num === null) return 'N/A';
    return Number.isInteger(num) ? String(num) : String(num);
  };

  // Build the GroupRangeMap for generatePrintHtml — use valid per-group ranges,
  // fall back to group min/max for invalid inputs.
  const buildRangeMap = useCallback((): GroupRangeMap => {
    const map: GroupRangeMap = {};
    groupSettings.forEach(s => {
      const fromNum = parseInt(s.fromRaw, 10);
      const toNum = parseInt(s.toRaw, 10);
      const mm = groupMinMax.get(s.groupId);
      if (!Number.isNaN(fromNum) && !Number.isNaN(toNum) && fromNum <= toNum) {
        map[s.groupId] = { from: fromNum, to: toNum };
      } else {
        // Invalid input: fall back to group min/max so the print still works
        map[s.groupId] = { from: mm?.min ?? 1, to: mm?.max ?? 9999 };
      }
    });
    return map;
  }, [groupSettings, groupMinMax]);

  // Whether any selected group has a valid range (for Print button)
  const anySelectedGroupPrintable = useMemo(() => {
    return groupSettings.some(s => {
      if (!s.selected) return false;
      const fromNum = parseInt(s.fromRaw, 10);
      const toNum = parseInt(s.toRaw, 10);
      if (Number.isNaN(fromNum) || Number.isNaN(toNum) || fromNum > toNum) return false;
      // Check at least one channel exists for this group (even without range filter)
      return channels.some(ch => ch.channel_group_id === s.groupId && ch.channel_number !== null);
    });
  }, [groupSettings, channels]);

  // Generate and open print window
  const handlePrint = useCallback(() => {
    const selectedGroupIds = groupSettings
      .filter(s => s.selected)
      .map(s => s.groupId);

    if (selectedGroupIds.length === 0) {
      alert('Please select at least one channel group to print.');
      return;
    }

    const rangeMap = buildRangeMap();

    // Build HTML content for the print window
    const printHtml = generatePrintHtml(
      channels,
      sortedGroups,
      groupSettings,
      title,
      rangeMap,
      showEmptySlots,
    );

    // Open new window and write content
    const printWindow = window.open('', '_blank');
    if (printWindow) {
      printWindow.document.write(printHtml);
      printWindow.document.close();
    }

    onClose();
  }, [channels, sortedGroups, groupSettings, title, onClose, buildRangeMap, showEmptySlots]);

  // isOpen gating handled by outer wrapper.

  return (
    <ModalOverlay onClose={onClose}>
      <div className="modal-container modal-md print-guide-modal">
        <div className="modal-header">
          <h2>Print Channel Guide</h2>
          <button className="modal-close-btn" onClick={onClose}>
            <span className="material-icons">close</span>
          </button>
        </div>

        <div className="modal-body">
          <p className="print-guide-description">
            Select channel groups to include and choose detailed (all channels) or summary (channel range) for each. Set each group's From/To to control its printed range.
          </p>

          {/* Empty-slot placeholder toggle (bd-w532y) */}
          <div className="print-guide-empty-slots-row">
            <label className="print-guide-empty-slots-label">
              <input
                type="checkbox"
                checked={showEmptySlots}
                onChange={e => setShowEmptySlots(e.target.checked)}
                aria-label="Show empty slots"
              />
              <span>Show empty slots</span>
            </label>
            <span className="print-guide-empty-slots-hint">
              Fills missing channel numbers in each group's range with placeholder rows
            </span>
          </div>

          {/* Empty state when no groups */}
          {sortedGroups.length === 0 && (
            <div className="print-guide-empty-range">
              <span className="material-icons">search_off</span>
              <p>No channel groups with numbered channels</p>
            </div>
          )}

          <div className="group-list">
            {sortedGroups.map((group, index) => {
              const settings = getGroupSettings(group.id);
              const info = getGroupInfo(group.id);
              const color = GROUP_COLORS[index % GROUP_COLORS.length];
              const fromNum = parseInt(settings.fromRaw, 10);
              const toNum = parseInt(settings.toRaw, 10);
              const rangeError =
                !Number.isNaN(fromNum) && !Number.isNaN(toNum) && fromNum > toNum
                  ? '"From" must be ≤ "To"'
                  : null;
              const mm = groupMinMax.get(group.id);

              return (
                <div
                  key={group.id}
                  className={`group-item ${settings.selected ? 'selected' : ''}`}
                >
                  <div className="group-checkbox-area" onClick={() => toggleGroup(group.id)}>
                    <input
                      type="checkbox"
                      checked={settings.selected}
                      onChange={() => toggleGroup(group.id)}
                      onClick={(e) => e.stopPropagation()}
                    />
                    <div
                      className="group-color-swatch"
                      style={{ backgroundColor: color.header }}
                    />
                    <div className="group-info">
                      <span className="group-name">{group.name}</span>
                      <span className="group-details">
                        {info.rangeError
                          ? 'Invalid range'
                          : info.count > 0
                            ? `${formatChannelNumber(info.first)}-${formatChannelNumber(info.last)} (${info.count} channels)`
                            : 'No channels in range'}
                      </span>
                    </div>
                  </div>

                  <div className="group-item-controls">
                    {/* Per-group From/To range inputs */}
                    <div className="print-guide-group-range">
                      <div className="print-guide-group-range-field">
                        <label htmlFor={`pgm-from-${group.id}`}>From</label>
                        <input
                          id={`pgm-from-${group.id}`}
                          type="number"
                          min={mm?.min}
                          max={mm?.max}
                          value={settings.fromRaw}
                          aria-label={`From channel for ${group.name}`}
                          onChange={e => setGroupFrom(group.id, e.target.value)}
                          onClick={e => e.stopPropagation()}
                        />
                      </div>
                      <div className="print-guide-group-range-field">
                        <label htmlFor={`pgm-to-${group.id}`}>To</label>
                        <input
                          id={`pgm-to-${group.id}`}
                          type="number"
                          min={mm?.min}
                          max={mm?.max}
                          value={settings.toRaw}
                          aria-label={`To channel for ${group.name}`}
                          onChange={e => setGroupTo(group.id, e.target.value)}
                          onClick={e => e.stopPropagation()}
                        />
                      </div>
                    </div>

                    {rangeError && (
                      <div className="field-error print-guide-group-range-error" role="alert">
                        {rangeError}
                      </div>
                    )}

                    <div className="mode-toggle">
                      <button
                        className={`mode-btn ${settings.mode === 'detailed' ? 'active' : ''}`}
                        onClick={() => setGroupMode(group.id, 'detailed')}
                        title="Show all channels with name"
                      >
                        Detailed
                      </button>
                      <button
                        className={`mode-btn summary ${settings.mode === 'summary' ? 'active' : ''}`}
                        onClick={() => setGroupMode(group.id, 'summary')}
                        title="Show only channel range"
                      >
                        Summary
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="modal-footer">
          <button className="modal-btn modal-btn-secondary" onClick={toggleAll}>
            {allSelected ? 'Deselect All' : 'Select All'}
          </button>
          <button
            className="modal-btn modal-btn-primary"
            onClick={handlePrint}
            disabled={!anySelectedGroupPrintable}
          >
            <span className="material-icons">print</span>
            Print Selected
          </button>
        </div>
      </div>
    </ModalOverlay>
  );
}

export const PrintGuideModal = memo(function PrintGuideModal(
  props: PrintGuideModalProps,
) {
  if (!props.isOpen) return null;
  return (
    <PrintGuideModalInner
      onClose={props.onClose}
      channelGroups={props.channelGroups}
      channels={props.channels}
      title={props.title}
    />
  );
});

// Generate the complete HTML for the print window.
// Exported for unit testing; not part of the public component API.
// eslint-disable-next-line react-refresh/only-export-components -- pure functions exported for unit testing; HMR handles the component export fine
export function generatePrintHtml(
  channels: Channel[],
  sortedGroups: ChannelGroup[],
  groupSettings: GroupPrintSettings[],
  title: string,
  groupRangeMap: GroupRangeMap,
  showEmptySlots: boolean = false,
): string {
  const selectedGroupIds = new Set(
    groupSettings.filter(s => s.selected).map(s => s.groupId)
  );

  const settingsMap = new Map(groupSettings.map(s => [s.groupId, s]));

  // Build groups HTML
  let groupsHtml = '';
  let colorIndex = 0;

  for (const group of sortedGroups) {
    if (!selectedGroupIds.has(group.id)) continue;

    const settings = settingsMap.get(group.id);
    const mode = settings?.mode ?? 'detailed';
    const color = GROUP_COLORS[colorIndex % GROUP_COLORS.length];
    colorIndex++;

    // Resolve per-group range
    const groupRange = groupRangeMap[group.id];
    const rangeFrom = groupRange?.from ?? 1;
    const rangeTo = groupRange?.to ?? 9999;

    // Apply per-group channel-number range filter before rendering
    const groupChannels = channels
      .filter(
        ch =>
          ch.channel_group_id === group.id &&
          ch.channel_number !== null &&
          ch.channel_number >= rangeFrom &&
          ch.channel_number <= rangeTo
      )
      .sort((a, b) => (a.channel_number ?? 0) - (b.channel_number ?? 0));

    if (groupChannels.length === 0) continue;

    if (mode === 'summary') {
      // Summary mode: show only range
      const first = groupChannels[0].channel_number;
      const last = groupChannels[groupChannels.length - 1].channel_number;
      const formatNum = (n: number | null) => n === null ? 'N/A' : (Number.isInteger(n) ? String(n) : String(n));
      const range = first === last ? formatNum(first) : `${formatNum(first)} - ${formatNum(last)}`;

      groupsHtml += `
        <div class="channel-group summary-mode" style="background: ${color.bg};">
          <div class="group-title" style="background: ${color.header}; color: #fff;">${escapeHtml(group.name)}</div>
          <div class="channel-list">
            <div class="channel-line"><span class="ch-num">${range}</span> (${groupChannels.length} channels)</div>
          </div>
        </div>
      `;
    } else {
      // Detailed mode: show all channels, with optional empty-slot placeholders.
      //
      // When showEmptySlots is true we walk every integer channel number from
      // the group's rangeFrom to rangeTo (inclusive) and emit a placeholder row
      // for any number that has no real channel.
      //
      // Performance cap: if the slot count exceeds MAX_EMPTY_SLOTS_PER_GROUP we
      // truncate and append a warning row rather than silently producing massive
      // output.
      let channelsHtml = '';

      if (showEmptySlots && groupChannels.length > 0) {
        const slotStart = rangeFrom;
        const slotEnd = rangeTo;
        const realByNum = new Map(
          groupChannels
            .filter(ch => ch.channel_number !== null)
            .map(ch => [ch.channel_number!, ch])
        );

        let emptyCount = 0;
        let truncated = false;

        for (let n = slotStart; n <= slotEnd; n++) {
          const ch = realByNum.get(n);
          if (ch) {
            const num = Number.isInteger(ch.channel_number!) ? String(ch.channel_number!) : String(ch.channel_number!);
            const displayName = cleanChannelName(ch.name, ch.channel_number);
            channelsHtml += `<div class="channel-line"><span class="ch-num">${num}</span> ${escapeHtml(displayName)}</div>\n`;
          } else {
            if (emptyCount >= MAX_EMPTY_SLOTS_PER_GROUP) {
              truncated = true;
              break;
            }
            channelsHtml += `<div class="channel-line channel-line-empty"><span class="ch-num ch-num-empty">${n}</span> <span class="ch-slot-label">—</span></div>\n`;
            emptyCount++;
          }
        }

        if (truncated) {
          channelsHtml += `<div class="channel-line channel-line-truncated">… (empty-slot limit reached — narrow the range)</div>\n`;
        }
      } else {
        // No empty slots — render only real channels
        for (const ch of groupChannels) {
          const num = ch.channel_number === null ? 'N/A' : (Number.isInteger(ch.channel_number) ? String(ch.channel_number) : String(ch.channel_number));
          const displayName = cleanChannelName(ch.name, ch.channel_number);
          channelsHtml += `<div class="channel-line"><span class="ch-num">${num}</span> ${escapeHtml(displayName)}</div>\n`;
        }
      }

      groupsHtml += `
        <div class="channel-group" style="background: ${color.bg};">
          <div class="group-title" style="background: ${color.header}; color: #fff;">${escapeHtml(group.name)}</div>
          <div class="channel-list">
            ${channelsHtml}
          </div>
        </div>
      `;
    }
  }

  // Count total channels — respects both group selection and per-group range filter
  const totalChannels = channels.filter(ch => {
    if (!selectedGroupIds.has(ch.channel_group_id ?? -1)) return false;
    if (ch.channel_number === null) return false;
    const groupRange = groupRangeMap[ch.channel_group_id!];
    if (!groupRange) return false;
    return ch.channel_number >= groupRange.from && ch.channel_number <= groupRange.to;
  }).length;

  // Generate complete HTML - using same approach as Guidearr
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>${escapeHtml(title)}</title>
  <style>
    @page {
      size: 11in 8.5in;
      margin: 0.3in 0.4in;
    }

    * {
      margin: 0;
      padding: 0;
      box-sizing: border-box;
      -webkit-print-color-adjust: exact !important;
      print-color-adjust: exact !important;
    }

    html, body {
      height: 100%;
    }

    body {
      font-family: Arial, sans-serif;
      background: #e0e0e0;
      padding: 20px;
    }

    .page {
      width: 10.2in;
      height: 7.9in;
      margin: 0 auto 20px auto;
      padding: 0.3in 0.4in;
      background: #fff;
      box-shadow: 0 2px 10px rgba(0,0,0,0.2);
      font-size: 6pt;
      line-height: 1.15;
      color: #000;
      overflow: hidden;
      position: relative;
    }

    .header {
      column-span: all;
      text-align: center;
      border-bottom: 1.5px solid #000;
      padding-bottom: 3px;
      margin-bottom: 6px;
    }

    .header h1 {
      font-size: 14pt;
      font-weight: bold;
      margin: 0 0 2px 0;
      letter-spacing: 0.5px;
    }

    .header .subtitle {
      font-size: 7pt;
      margin: 0;
      color: #333;
    }

    .channel-group {
      break-inside: auto;
      page-break-inside: auto;
      border: 1px solid #999;
      border-radius: 2px;
      padding: 3px 4px;
      margin-bottom: 4px;
    }

    .group-title {
      font-size: 7pt;
      font-weight: bold;
      padding: 2px 4px;
      margin: -3px -4px 2px -4px;
      border-radius: 1px 1px 0 0;
    }

    .channel-line {
      margin: 0;
      padding: 0.5px 0;
      line-height: 1.2;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .ch-num {
      font-weight: bold;
      display: inline-block;
      min-width: 28px;
      color: #000;
    }

    .summary-mode .channel-line {
      font-style: italic;
    }

    .channel-line-empty {
      opacity: 0.45;
    }

    .ch-num-empty {
      color: #999;
      font-weight: normal;
    }

    .ch-slot-label {
      color: #aaa;
    }

    .channel-line-truncated {
      font-style: italic;
      color: #c00;
      font-size: 5.5pt;
      margin-top: 1px;
    }

    .content {
      column-count: 5;
      column-gap: 10px;
      column-fill: auto;
      height: calc(100% - 45px);
      overflow: hidden;
    }

    .print-hint {
      text-align: center;
      padding: 10px;
      background: #fff3cd;
      border: 1px solid #ffc107;
      border-radius: 4px;
      margin-bottom: 20px;
      font-size: 10pt;
    }

    .page-footer {
      position: absolute;
      bottom: 0;
      left: 0;
      right: 0;
      height: 15px;
      text-align: right;
      padding-right: 0.1in;
      font-size: 7pt;
      color: #666;
    }

    @media print {
      body {
        background: #fff;
        padding: 0;
      }

      .page {
        width: auto;
        height: 7.9in;
        margin: 0;
        padding: 0.3in 0.4in;
        box-shadow: none;
        page-break-after: always;
        position: relative;
        overflow: hidden;
      }

      .page:last-child {
        page-break-after: auto;
      }

      .content {
        height: calc(100% - 45px);
      }

      .print-hint {
        display: none;
      }
    }
  </style>
</head>
<body>
  <div class="print-hint">
    Print dialog will open automatically. If it doesn't, press Ctrl+P (Cmd+P on Mac).
  </div>

  <div id="pages-container">
    <div class="page" id="page-1">
      <div class="header">
        <h1>${escapeHtml(title)}</h1>
        <div class="subtitle">${totalChannels} channels</div>
      </div>
      <div class="content">
        ${groupsHtml}
      </div>
    </div>
  </div>

  <script>
    window.addEventListener('load', function() {
      setTimeout(function() {
        handlePagination();
        setTimeout(function() {
          window.print();
        }, 300);
      }, 200);
    });

    function handlePagination() {
      const container = document.getElementById('pages-container');
      const firstPage = document.getElementById('page-1');
      const content = firstPage.querySelector('.content');
      const groups = Array.from(content.querySelectorAll('.channel-group'));

      if (groups.length === 0) {
        addPageNumber(firstPage, 1);
        return;
      }

      // Calculate the maximum allowed right edge for 5 columns
      const contentRect = content.getBoundingClientRect();
      const maxRightEdge = contentRect.left + contentRect.width;

      // Paginate the content ensuring no more than 5 columns per page
      paginateContent(container, firstPage, groups, maxRightEdge);
    }

    function paginateContent(container, page, groups, maxRightEdge) {
      const content = page.querySelector('.content');

      // Temporarily allow overflow to measure true content extent
      content.style.overflow = 'visible';

      // Categorize groups:
      // 1. Groups that fit entirely within columns 1-5 (right <= maxRightEdge)
      // 2. Groups that SPAN the boundary (left < maxRightEdge, right > maxRightEdge)
      // 3. Groups that START past the boundary (left >= maxRightEdge)

      let spanningGroup = null;
      let spanningGroupIdx = -1;
      let overflowStartIdx = -1;

      for (let i = 0; i < groups.length; i++) {
        const rect = groups[i].getBoundingClientRect();

        if (rect.left >= maxRightEdge) {
          // Group starts past boundary - this and all following go to next page
          overflowStartIdx = i;
          break;
        } else if (rect.right > maxRightEdge + 2) {
          // Group spans the boundary - needs to be split/continued
          spanningGroup = groups[i];
          spanningGroupIdx = i;
          // Continue to find where pure overflow starts
        }
      }

      // Restore overflow hidden
      content.style.overflow = 'hidden';

      // If no overflow at all, we're done
      if (overflowStartIdx === -1 && !spanningGroup) {
        addPageNumber(page, getPageNumber(page));
        updatePageNumbers(container);
        return;
      }

      // Create the next page
      const newPageNum = container.querySelectorAll('.page').length + 1;
      const newPage = document.createElement('div');
      newPage.className = 'page';
      newPage.id = 'page-' + newPageNum;
      newPage.innerHTML = '<div class="header" style="border-bottom: 1px solid #999;"><h1 style="font-size: 10pt;">${escapeHtml(title)} (continued)</h1></div><div class="content"></div>';
      container.appendChild(newPage);
      const newContent = newPage.querySelector('.content');

      // Handle spanning group - clone it to continue on next page
      if (spanningGroup) {
        const clone = spanningGroup.cloneNode(true);
        // Add "(continued)" to the group title
        const titleEl = clone.querySelector('.group-title');
        if (titleEl) {
          titleEl.textContent = titleEl.textContent + ' (continued)';
        }
        newContent.appendChild(clone);
      }

      // Move groups that start past the boundary to the new page
      if (overflowStartIdx !== -1) {
        const overflowGroups = groups.slice(overflowStartIdx);
        overflowGroups.forEach(function(g) {
          newContent.appendChild(g);
        });
      }

      addPageNumber(page, getPageNumber(page));

      // Recursively handle the new page
      setTimeout(function() {
        const newGroups = Array.from(newContent.querySelectorAll('.channel-group'));
        paginateContent(container, newPage, newGroups, maxRightEdge);
      }, 50);
    }

    function getPageNumber(page) {
      const match = page.id.match(/page-(\\d+)/);
      return match ? parseInt(match[1], 10) : 1;
    }

    function updatePageNumbers(container) {
      const allPages = container.querySelectorAll('.page');
      const total = allPages.length;
      allPages.forEach(function(page, idx) {
        const pageFooter = page.querySelector('.page-footer');
        if (pageFooter) {
          pageFooter.textContent = 'Page ' + (idx + 1) + ' of ' + total;
        }
      });
    }

    function addPageNumber(page, num) {
      if (page.querySelector('.page-footer')) return; // Already has footer
      const pageFooter = document.createElement('div');
      pageFooter.className = 'page-footer';
      pageFooter.textContent = 'Page ' + num;
      page.appendChild(pageFooter);
    }
  </script>
</body>
</html>`;
}

// Escape HTML special characters
function escapeHtml(text: string): string {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

export default PrintGuideModal;
