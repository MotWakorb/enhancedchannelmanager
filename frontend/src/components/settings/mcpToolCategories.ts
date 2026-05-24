/**
 * Shared MCP tool-category catalog.
 *
 * Single source of truth for the "what Claude can do" grid rendered by
 * MCPSettingsSection's "Available Tools" panel.
 */
export interface McpToolCategory {
  category: string;
  count: number;
  icon: string;
  desc: string;
}

export const MCP_TOOL_CATEGORIES: McpToolCategory[] = [
  { category: 'Channels', count: 12, icon: 'tv', desc: 'CRUD, streams, merge, bulk numbering' },
  { category: 'Groups', count: 6, icon: 'folder', desc: 'CRUD, hidden, orphaned, auto-created' },
  { category: 'Streams', count: 11, icon: 'stream', desc: 'List, search, probe, health, struck-out' },
  { category: 'M3U', count: 8, icon: 'playlist_play', desc: 'Account CRUD, refresh, group settings' },
  { category: 'EPG', count: 7, icon: 'schedule', desc: 'Source CRUD, grid, refresh, auto-match' },
  { category: 'Auto-Create', count: 9, icon: 'auto_fix_high', desc: 'Rule CRUD, toggle, executions, rollback' },
  { category: 'Export', count: 6, icon: 'file_download', desc: 'Profiles, cloud targets, publish' },
  { category: 'Tasks', count: 7, icon: 'timer', desc: 'Run, cancel, history, schedules' },
  { category: 'Stats', count: 6, icon: 'analytics', desc: 'Top watched, bandwidth, popularity, viewers' },
  { category: 'System', count: 3, icon: 'settings', desc: 'Settings, backup, journal' },
  { category: 'Notifications', count: 3, icon: 'notifications', desc: 'List, mark read, clear' },
  { category: 'Profiles', count: 3, icon: 'tune', desc: 'Channel/stream profiles, bulk assign' },
  { category: 'Normalize', count: 2, icon: 'text_format', desc: 'Test normalization, list rules' },
];

/** Total tool count across all categories (the heading number). */
export const MCP_TOOL_TOTAL = MCP_TOOL_CATEGORIES.reduce((n, t) => n + t.count, 0);
