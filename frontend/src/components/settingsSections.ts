import type { SettingsPage } from '../hooks/useHashRoute';

export interface SettingsSection {
  id: SettingsPage;
  label: string;
  icon: string;
  /** Rendered only for administrators, matching the former in-page list. */
  adminOnly?: boolean;
  /**
   * Page heading shown as the third breadcrumb crumb. Carried verbatim from the
   * in-pane `.settings-page-header` this replaced, so it can differ from the
   * shorter sidebar `label` (for example "General" vs "General Settings").
   * Falls back to `label` where a section never had its own heading.
   */
  title?: string;
  /** Descriptive line beneath the breadcrumb; omitted where none existed. */
  description?: string;
}

// Single source for the Settings destinations shown in the primary sidebar's
// drill-in view. Order and labels are carried over verbatim from the in-page
// settings list this replaced, so existing deep links and operator
// documentation stay accurate.
export const SETTINGS_SECTIONS: SettingsSection[] = [
  {
    id: 'general', label: 'General', icon: 'settings',
    title: 'General Settings',
    description: 'Configure your Dispatcharr connection.',
  },
  {
    id: 'channel-defaults', label: 'Channel Defaults', icon: 'tv',
    description: 'Configure default options for bulk channel creation.',
  },
  {
    id: 'normalization', label: 'Channel Normalization', icon: 'auto_fix_high',
    description: 'Configure tag-based patterns for cleaning up channel names during bulk channel creation.',
  },
  {
    id: 'tag-engine', label: 'Tags', icon: 'label',
    description: 'Manage tag vocabularies used by normalization rules for pattern matching.',
  },
  {
    id: 'lookup-tables', label: 'Lookup Tables', icon: 'table_view',
    description: "Named key → value tables used by the dummy EPG template engine's {key|lookup:<name>} pipe.",
  },
  {
    id: 'appearance', label: 'Appearance', icon: 'palette',
    description: 'Customize how the app displays information.',
  },
  {
    id: 'email', label: 'Notification Settings', icon: 'notifications',
    description: 'Configure notification channels (Email, Discord, Telegram) for alerts and reports.',
  },
  {
    id: 'integrations', label: 'Integrations', icon: 'extension',
    description: 'Configure third-party server integrations for cross-referenced data (Stats user attribution, etc.).',
  },
  {
    id: 'scheduled-tasks', label: 'Scheduled Tasks', icon: 'schedule',
    description: 'Manage automated tasks like EPG refresh, M3U refresh, and database cleanup.',
  },
  {
    id: 'channel-pipeline', label: 'Channel Pipeline', icon: 'auto_awesome',
    description: 'Configure global exclusion filters for the channel pipeline. Streams matching these filters will be excluded before any rules are evaluated.',
  },
  {
    id: 'm3u-digest', label: 'M3U Digest', icon: 'mail',
    title: 'M3U Change Digest',
    description: 'Configure email notifications for M3U playlist changes.',
  },
  {
    id: 'maintenance', label: 'Maintenance', icon: 'build',
    description: 'Stream probing and database cleanup tools.',
  },
  {
    id: 'linked-accounts', label: 'Linked Accounts', icon: 'link',
    description: 'Link external service accounts for single sign-on and synchronization.',
  },
  {
    id: 'backup-restore', label: 'Backup & Restore', icon: 'backup',
    description: 'Export, schedule, and restore ECM configuration and database backups, and control where backups may be sent.',
  },
  {
    id: 'auth-settings', label: 'Authentication', icon: 'security', adminOnly: true,
    description: 'Configure authentication providers and security settings.',
  },
  {
    id: 'user-management', label: 'User Management', icon: 'people', adminOnly: true,
    title: 'Users',
    description: 'Manage user accounts and permissions.',
  },
  {
    id: 'tls-settings', label: 'TLS Certificates', icon: 'https', adminOnly: true,
    title: 'TLS/SSL Certificate Management',
    description: "Configure HTTPS with Let's Encrypt automatic certificates or manual certificate upload.",
  },
  {
    id: 'mcp-settings', label: 'MCP Integration', icon: 'smart_toy', adminOnly: true,
    description: 'Connect Claude to ECM via the Model Context Protocol. Claude can list channels, manage streams, refresh M3U accounts, probe stream health, and more — all through natural language.',
  },
];

export function settingsSectionHeading(page: SettingsPage): { title: string; description?: string } {
  const section = SETTINGS_SECTIONS.find((candidate) => candidate.id === page);
  if (!section) return { title: 'Settings' };
  return { title: section.title ?? section.label, description: section.description };
}

export function visibleSettingsSections(isAdmin: boolean): SettingsSection[] {
  return SETTINGS_SECTIONS.filter((section) => isAdmin || !section.adminOnly);
}
