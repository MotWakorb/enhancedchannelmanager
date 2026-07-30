/**
 * The single declaration of ECM's Settings destinations.
 *
 * ONE SOURCE, THREE CONSUMERS. Until bead `enhancedchannelmanager-70u0r.7`
 * this module declared the sections while `useHashRoute.ts` separately
 * hand-maintained the `SettingsPage` union *and* a `VALID_SETTINGS_PAGES` set
 * of the same ids. Three lists meant every added or retired destination had to
 * be edited in three places, and the import direction ran the wrong way to fix
 * it (`settingsSections.ts` imported the union from the router). The direction
 * is now reversed: `SECTION_DEFINITIONS` below is the only place a settings id
 * is written down, the union is *derived* from it, and `useHashRoute.ts`
 * imports both. Adding a destination is one entry; forgetting to update the
 * router is no longer possible.
 */

/**
 * The Settings drill-in's sidebar groups, in rendered order (bead
 * `enhancedchannelmanager-70u0r.3`, PO decision D1).
 *
 * Ordered along the operator's path, mirroring the rationale
 * `navigationGroups.ts` applies to the primary sidebar: what ECM talks to,
 * then how a stream becomes a channel, then what ECM reports, then how the
 * instance is kept healthy, then the operator's own surface, then
 * administration.
 *
 * `Upkeep` is deliberate and neither of the obvious alternatives works: the
 * primary nav already uses `System` for the group that *contains* Settings, so
 * reusing it would make one word denote two levels of the same tree, and
 * `Maintenance` is the name of a destination *inside* this group.
 */
export const SETTINGS_GROUP_ORDER = [
  'Connections',
  'Channel Processing',
  'Notifications & Reports',
  'Upkeep',
  'Workspace',
  'Administration',
] as const;

export type SettingsGroup = (typeof SETTINGS_GROUP_ORDER)[number];

/**
 * The shape every entry in `SECTION_DEFINITIONS` must satisfy.
 *
 * `id` is widened to `string` here on purpose: the `SettingsPage` union is
 * derived *from* the declarations, so constraining them by it first would be
 * circular. `SettingsSection` below narrows it back for every consumer.
 */
export interface SettingsSectionDeclaration {
  id: string;
  label: string;
  icon: string;
  /**
   * Which sidebar group the destination is listed under. Drives the drill-in's
   * grouping directly; `Administration` must line up with `adminOnly` so the
   * group disappears wholesale for a non-admin rather than rendering a heading
   * over an empty list.
   */
  group: SettingsGroup;
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

// Grouped and ordered exactly as the drill-in renders them, so the file reads
// as the navigation it produces. Labels and per-section copy are carried over
// verbatim from the in-page settings list this replaced, so existing deep links
// and operator documentation stay accurate.
//
// `as const satisfies` is what makes this the single source: `as const` keeps
// the ids as literal types for `SettingsPage` to be derived from, while
// `satisfies` still type-checks every entry against the declaration shape.
const SECTION_DEFINITIONS = [
  // Connections — what other systems does ECM talk to?
  {
    id: 'general', label: 'General', icon: 'settings',
    group: 'Connections',
    title: 'General Settings',
    description: 'Configure your Dispatcharr connection.',
  },
  {
    id: 'integrations', label: 'Integrations', icon: 'extension',
    group: 'Connections',
    description: 'Configure third-party server integrations for cross-referenced data (Stats user attribution, etc.).',
  },

  // Channel Processing — the path a stream takes to become a channel.
  {
    id: 'channel-defaults', label: 'Channel Defaults', icon: 'tv',
    group: 'Channel Processing',
    description: 'Configure default options for bulk channel creation.',
  },
  {
    id: 'normalization', label: 'Channel Normalization', icon: 'auto_fix_high',
    group: 'Channel Processing',
    description: 'Configure tag-based patterns for cleaning up channel names during bulk channel creation.',
  },
  {
    id: 'tag-engine', label: 'Tags', icon: 'label',
    group: 'Channel Processing',
    description: 'Manage tag vocabularies used by normalization rules for pattern matching.',
  },
  {
    // Grouped here provisionally. Lookup Tables serves the dummy EPG template
    // engine, not channel processing, and has no coherent home in the approved
    // grouping — which is the finding, not a gap: bead
    // `enhancedchannelmanager-70u0r.1` retires the destination entirely (PO
    // decision D2), at which point Channel Processing drops back to four.
    id: 'lookup-tables', label: 'Lookup Tables', icon: 'table_view',
    group: 'Channel Processing',
    description: "Named key → value tables used by the dummy EPG template engine's {key|lookup:<name>} pipe.",
  },
  {
    id: 'channel-pipeline', label: 'Channel Pipeline', icon: 'auto_awesome',
    group: 'Channel Processing',
    description: 'Configure global exclusion filters for the channel pipeline. Streams matching these filters will be excluded before any rules are evaluated.',
  },

  // Notifications & Reports — delivery transport, and the reports that ride it.
  {
    id: 'email', label: 'Notification Settings', icon: 'notifications',
    group: 'Notifications & Reports',
    description: 'Configure notification channels (Email, Discord, Telegram) for alerts and reports.',
  },
  {
    id: 'm3u-digest', label: 'M3U Digest', icon: 'mail',
    group: 'Notifications & Reports',
    title: 'M3U Change Digest',
    description: 'Configure email notifications for M3U playlist changes.',
  },

  // Upkeep — how do I keep this instance healthy? Recurring work, diagnostic
  // and cleanup tools, and the recovery path.
  {
    id: 'scheduled-tasks', label: 'Scheduled Tasks', icon: 'schedule',
    group: 'Upkeep',
    description: 'Manage automated tasks like EPG refresh, M3U refresh, and database cleanup.',
  },
  {
    id: 'maintenance', label: 'Maintenance', icon: 'build',
    group: 'Upkeep',
    description: 'Stream probing and database cleanup tools.',
  },
  {
    id: 'backup-restore', label: 'Backup & Restore', icon: 'backup',
    group: 'Upkeep',
    description: 'Export, schedule, and restore ECM configuration and database backups, and control where backups may be sent.',
  },

  // Workspace — the operator-facing surface rather than instance behaviour.
  {
    id: 'appearance', label: 'Appearance', icon: 'palette',
    group: 'Workspace',
    description: 'Customize how the app displays information.',
  },
  {
    id: 'linked-accounts', label: 'Linked Accounts', icon: 'link',
    group: 'Workspace',
    description: 'Link external service accounts for single sign-on and synchronization.',
  },

  // Administration — admin only. Every entry here must carry `adminOnly`.
  {
    id: 'auth-settings', label: 'Authentication', icon: 'security', adminOnly: true,
    group: 'Administration',
    description: 'Configure authentication providers and security settings.',
  },
  {
    id: 'user-management', label: 'User Management', icon: 'people', adminOnly: true,
    group: 'Administration',
    title: 'Users',
    description: 'Manage user accounts and permissions.',
  },
  {
    id: 'tls-settings', label: 'TLS Certificates', icon: 'https', adminOnly: true,
    group: 'Administration',
    title: 'TLS/SSL Certificate Management',
    description: "Configure HTTPS with Let's Encrypt automatic certificates or manual certificate upload.",
  },
  {
    id: 'mcp-settings', label: 'MCP Integration', icon: 'smart_toy', adminOnly: true,
    group: 'Administration',
    description: 'Connect Claude to ECM via the Model Context Protocol. Claude can list channels, manage streams, refresh M3U accounts, probe stream health, and more — all through natural language.',
  },
] as const satisfies readonly SettingsSectionDeclaration[];

/**
 * Every settings sub-page id, derived from the declarations above. `#settings/…`
 * routing, the breadcrumb lookup and the sidebar all narrow to this union, so
 * an id that is not declared above cannot be routed to.
 */
export type SettingsPage = (typeof SECTION_DEFINITIONS)[number]['id'];

export interface SettingsSection extends SettingsSectionDeclaration {
  id: SettingsPage;
}

// Single source for the Settings destinations shown in the primary sidebar's
// drill-in view.
export const SETTINGS_SECTIONS: readonly SettingsSection[] = SECTION_DEFINITIONS;

/** The declared ids, in navigation order. */
export const SETTINGS_PAGE_IDS: readonly SettingsPage[] = SECTION_DEFINITIONS.map((section) => section.id);

const SETTINGS_PAGE_ID_SET: ReadonlySet<string> = new Set<string>(SETTINGS_PAGE_IDS);

/**
 * Whether an arbitrary hash fragment names a live settings destination. The
 * router's only admission check — replaces the hand-maintained
 * `VALID_SETTINGS_PAGES` set it used to keep in parallel with this file.
 */
export function isSettingsPage(value: string): value is SettingsPage {
  return SETTINGS_PAGE_ID_SET.has(value);
}

export function settingsSectionHeading(page: SettingsPage): { title: string; description?: string } {
  const section = SETTINGS_SECTIONS.find((candidate) => candidate.id === page);
  if (!section) return { title: 'Settings' };
  return { title: section.title ?? section.label, description: section.description };
}

export function visibleSettingsSections(isAdmin: boolean): SettingsSection[] {
  return SETTINGS_SECTIONS.filter((section) => isAdmin || !section.adminOnly);
}

/** A drill-in sidebar group with the sections visible to the current viewer. */
export interface SettingsSectionGroup {
  label: SettingsGroup;
  sections: SettingsSection[];
}

/**
 * The drill-in's groups for one viewer, in `SETTINGS_GROUP_ORDER`.
 *
 * Empty groups are dropped, and that — not an `adminOnly` branch — is what
 * hides Administration from a non-admin: all four of its sections are
 * `adminOnly`, so `visibleSettingsSections(false)` leaves the group with
 * nothing in it.
 */
export function settingsSectionGroups(isAdmin: boolean): SettingsSectionGroup[] {
  const sections = visibleSettingsSections(isAdmin);
  return SETTINGS_GROUP_ORDER
    .map((label) => ({ label, sections: sections.filter((section) => section.group === label) }))
    .filter((group) => group.sections.length > 0);
}
