import { useState, useMemo, useCallback } from 'react';
import type { Channel, ChannelGroup } from '../types';
import './PrintGuideModal.css';

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

interface GroupPrintSettings {
  groupId: number;
  selected: boolean;
  mode: 'detailed' | 'summary';
}

interface PrintGuideModalProps {
  isOpen: boolean;
  onClose: () => void;
  channelGroups: ChannelGroup[];
  channels: Channel[];
  title?: string;
}

export function PrintGuideModal({
  isOpen,
  onClose,
  channelGroups,
  channels,
  title = 'TV Channel Guide',
}: PrintGuideModalProps) {
  // Initialize group settings - all selected, detailed mode by default
  const [groupSettings, setGroupSettings] = useState<GroupPrintSettings[]>(() =>
    channelGroups.map(g => ({
      groupId: g.id,
      selected: true,
      mode: 'detailed',
    }))
  );

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

  // Reset settings when modal opens with new groups
  useMemo(() => {
    if (isOpen) {
      setGroupSettings(
        sortedGroups.map(g => ({
          groupId: g.id,
          selected: true,
          mode: 'detailed',
        }))
      );
    }
  }, [isOpen, sortedGroups]);

  // Get settings for a specific group
  const getGroupSettings = (groupId: number): GroupPrintSettings => {
    return groupSettings.find(s => s.groupId === groupId) ?? {
      groupId,
      selected: true,
      mode: 'detailed',
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

  // Select/deselect all
  const toggleAll = useCallback(() => {
    const allSelected = groupSettings.every(s => s.selected);
    setGroupSettings(prev =>
      prev.map(s => ({ ...s, selected: !allSelected }))
    );
  }, [groupSettings]);

  // Get channel count and range for a group
  const getGroupInfo = useCallback((groupId: number) => {
    const groupChannels = channels
      .filter(ch => ch.channel_group_id === groupId && ch.channel_number !== null)
      .sort((a, b) => (a.channel_number ?? 0) - (b.channel_number ?? 0));

    if (groupChannels.length === 0) {
      return { count: 0, first: null, last: null };
    }

    return {
      count: groupChannels.length,
      first: groupChannels[0].channel_number,
      last: groupChannels[groupChannels.length - 1].channel_number,
    };
  }, [channels]);

  // Format channel number (remove .0 for whole numbers)
  const formatChannelNumber = (num: number | null): string => {
    if (num === null) return 'N/A';
    return Number.isInteger(num) ? String(num) : String(num);
  };

  // Generate and open print window
  const handlePrint = useCallback(() => {
    const selectedGroupIds = groupSettings
      .filter(s => s.selected)
      .map(s => s.groupId);

    if (selectedGroupIds.length === 0) {
      alert('Please select at least one channel group to print.');
      return;
    }

    // Build HTML content for the print window
    const printHtml = generatePrintHtml(
      channels,
      sortedGroups,
      groupSettings,
      title
    );

    // Open new window and write content
    const printWindow = window.open('', '_blank');
    if (printWindow) {
      printWindow.document.write(printHtml);
      printWindow.document.close();
    }

    onClose();
  }, [channels, sortedGroups, groupSettings, title, onClose]);

  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content print-guide-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Print Channel Guide</h2>
          <button className="close-btn" onClick={onClose}>
            &times;
          </button>
        </div>

        <div className="modal-body">
          <p className="print-guide-description">
            Select channel groups to include and choose detailed (all channels) or summary (channel range) for each.
          </p>

          <div className="group-list">
            {sortedGroups.map((group, index) => {
              const settings = getGroupSettings(group.id);
              const info = getGroupInfo(group.id);
              const color = GROUP_COLORS[index % GROUP_COLORS.length];

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
                        {info.count > 0
                          ? `${formatChannelNumber(info.first)}-${formatChannelNumber(info.last)} (${info.count} channels)`
                          : 'No channels'}
                      </span>
                    </div>
                  </div>

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
              );
            })}
          </div>
        </div>

        <div className="modal-footer">
          <button className="btn-secondary" onClick={toggleAll}>
            {groupSettings.every(s => s.selected) ? 'Deselect All' : 'Select All'}
          </button>
          <button className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn-primary"
            onClick={handlePrint}
            disabled={!groupSettings.some(s => s.selected)}
          >
            <span className="material-icons" style={{ fontSize: '1rem', marginRight: '0.25rem' }}>print</span>
            Print Selected
          </button>
        </div>
      </div>
    </div>
  );
}

// Generate the complete HTML for the print window
function generatePrintHtml(
  channels: Channel[],
  sortedGroups: ChannelGroup[],
  groupSettings: GroupPrintSettings[],
  title: string
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

    const groupChannels = channels
      .filter(ch => ch.channel_group_id === group.id && ch.channel_number !== null)
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
      // Detailed mode: show all channels
      let channelsHtml = '';
      for (const ch of groupChannels) {
        const num = ch.channel_number === null ? 'N/A' : (Number.isInteger(ch.channel_number) ? String(ch.channel_number) : String(ch.channel_number));
        channelsHtml += `<div class="channel-line"><span class="ch-num">${num}</span> ${escapeHtml(ch.name)}</div>\n`;
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

  // Count total channels
  const totalChannels = channels.filter(ch =>
    selectedGroupIds.has(ch.channel_group_id ?? -1) && ch.channel_number !== null
  ).length;

  // Generate complete HTML
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

    body {
      font-family: Arial, sans-serif;
      font-size: 6pt;
      line-height: 1.15;
      color: #000;
      background: #fff;
      column-count: 5;
      column-gap: 10px;
      column-fill: auto;
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
      border-bottom: none;
      padding: 2px 4px;
      margin: -3px -4px 2px -4px;
      break-after: avoid;
    }

    .channel-list {
      /* Simple list container */
    }

    .channel-line {
      margin: 0;
      padding: 0.5px 0;
      line-height: 1.2;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      break-inside: avoid;
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

    @media screen {
      body {
        max-width: 11in;
        margin: 0 auto;
        padding: 20px;
        background: #f5f5f5;
      }

      .print-hint {
        column-span: all;
        text-align: center;
        padding: 10px;
        background: #fff3cd;
        border: 1px solid #ffc107;
        border-radius: 4px;
        margin-bottom: 10px;
        font-size: 10pt;
      }
    }

    @media print {
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

  <div class="header">
    <h1>${escapeHtml(title)}</h1>
    <div class="subtitle">${totalChannels} channels</div>
  </div>

  ${groupsHtml}

  <script>
    window.addEventListener('load', function() {
      setTimeout(function() {
        window.print();
      }, 500);
    });
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
