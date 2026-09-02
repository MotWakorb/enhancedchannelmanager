/**
 * Deterministic in-page `/api` stub for the dev-only modal harness
 * (bead enhancedchannelmanager-xhldy.1).
 *
 * WHY STUB THE NETWORK RATHER THAN THE COMPONENT
 * ----------------------------------------------
 * The whole point of the harness is to measure each dialog's REAL markup.
 * Many dialogs fetch on mount and render a spinner until the fetch resolves —
 * a spinner is not a useful measurement. So the harness stubs the *data* at
 * the network boundary (`window.fetch`, which every call funnels through via
 * `services/httpClient.ts`) and leaves the components completely untouched.
 *
 * WHY NOT PROXY TO A REAL BACKEND
 * -------------------------------
 * A baseline is only useful if the "after" run measures the same thing. A
 * live backend makes the rendered row count — and therefore which typography
 * appears at all — a function of whatever is in the database that day. Every
 * response here is a constant, so a before/after diff shows CSS changes and
 * nothing else. `?live=1` opts a manual exploratory session out of this.
 *
 * UNSTUBBED CALLS ARE RECORDED, NOT HIDDEN
 * ----------------------------------------
 * Anything that falls through to the generic default is pushed onto
 * `unstubbedCalls` and surfaced in the harness manifest, so "this dialog
 * rendered its empty state because we never stubbed X" is visible in the
 * measurement output instead of being silently baked into a baseline.
 */

export interface StubRoute {
  /** Matched against the request path + query (e.g. `/api/channels?page=1`). */
  match: RegExp
  /** Restrict to a single method. Omit to match any method. */
  method?: string
  /** Response body, or a factory receiving the regex match. */
  body: unknown | ((m: RegExpMatchArray) => unknown)
  status?: number
}

interface StubState {
  pending: number
  calls: string[]
  unstubbedCalls: string[]
}

const state: StubState = { pending: 0, calls: [], unstubbedCalls: [] }

export function stubState(): Readonly<StubState> {
  return state
}

export function resetStubState(): void {
  state.pending = 0
  state.calls.length = 0
  state.unstubbedCalls.length = 0
}

/** ------------------------------------------------------------------ */
/** Canned domain objects. Values are chosen to be *typographically      */
/** representative*: names long enough to wrap, numbers wide enough to   */
/** show tabular alignment, at least one long free-text field per shape. */
/** ------------------------------------------------------------------ */

const NOW = '2026-07-29T12:00:00Z'

export const stubChannels = [
  {
    id: 1,
    channel_number: 101,
    name: 'BBC One HD (London)',
    channel_group_id: 1,
    tvg_id: 'bbc.one.uk',
    tvc_guide_stationid: '12345',
    epg_data_id: 1,
    streams: [1, 2],
    stream_profile_id: 1,
    uuid: '11111111-1111-4111-8111-111111111111',
    logo_id: 1,
    auto_created: false,
    auto_created_by: null,
    auto_created_by_name: null,
    is_catchup: true,
    catchup_days: 7,
  },
  {
    id: 2,
    channel_number: 102,
    name: 'Sky Sports Main Event Ultra HD',
    channel_group_id: 1,
    tvg_id: 'sky.sports.main.uk',
    tvc_guide_stationid: null,
    epg_data_id: 2,
    streams: [3],
    stream_profile_id: null,
    uuid: '22222222-2222-4222-8222-222222222222',
    logo_id: 2,
    auto_created: true,
    auto_created_by: 7,
    auto_created_by_name: 'Sports auto-creation rule',
    is_catchup: false,
    catchup_days: 0,
  },
  {
    id: 3,
    channel_number: 2001,
    name: 'A Channel With A Deliberately Very Long Name To Force Wrapping',
    channel_group_id: 2,
    tvg_id: null,
    tvc_guide_stationid: null,
    epg_data_id: null,
    streams: [],
    stream_profile_id: null,
    uuid: '33333333-3333-4333-8333-333333333333',
    logo_id: null,
    auto_created: false,
    auto_created_by: null,
    auto_created_by_name: null,
  },
]

export const stubChannelGroups = [
  { id: 1, name: 'United Kingdom — Entertainment' },
  { id: 2, name: 'Sports' },
  { id: 3, name: 'Ungrouped' },
]

export const stubStreams = [
  {
    id: 1,
    name: 'UK| BBC ONE HD',
    url: 'http://provider.example/live/bbc-one-hd.ts',
    m3u_account: 1,
    logo_url: null,
    tvg_id: 'bbc.one.uk',
    group_name: 'UK | ENTERTAINMENT',
    stream_profile_id: null,
    is_custom: false,
    channel_group: 'UK | ENTERTAINMENT',
    current_viewers: 3,
  },
  {
    id: 2,
    name: 'UK| BBC ONE FHD (BACKUP)',
    url: 'http://provider.example/live/bbc-one-fhd.ts',
    m3u_account: 2,
    logo_url: null,
    tvg_id: 'bbc.one.uk',
    group_name: 'UK | ENTERTAINMENT',
    stream_profile_id: null,
    is_custom: false,
    channel_group: 'UK | ENTERTAINMENT',
    current_viewers: 0,
  },
  {
    id: 3,
    name: 'UK| SKY SPORTS MAIN EVENT UHD',
    url: 'http://provider.example/live/sky-sports-main-uhd.ts',
    m3u_account: 1,
    logo_url: null,
    tvg_id: 'sky.sports.main.uk',
    group_name: 'UK | SPORTS',
    stream_profile_id: null,
    is_custom: false,
    channel_group: 'UK | SPORTS',
    current_viewers: 12,
  },
]

export const stubM3UAccounts = [
  {
    id: 1,
    name: 'Primary Provider (EU edge)',
    server_url: 'http://provider.example/get.php',
    max_streams: 5,
    is_active: true,
    priority: 1,
    account_type: 'XC',
    username: 'ecm-primary',
    stale_stream_days: 7,
    refresh_interval: 12,
    server_group: 1,
    locked: false,
  },
  {
    id: 2,
    name: 'Backup Provider',
    server_url: 'http://backup.example/playlist.m3u',
    max_streams: 2,
    is_active: false,
    priority: 2,
    account_type: 'STD',
    username: null,
    stale_stream_days: 14,
    refresh_interval: 24,
    server_group: null,
    locked: false,
  },
]

export const stubEPGSources = [
  {
    id: 1,
    name: 'Schedules Direct — United Kingdom',
    source_type: 'schedules_direct',
    url: 'https://json.schedulesdirect.org/20141201/',
    is_active: true,
    priority: 1,
    refresh_interval: 24,
    status: 'success',
    last_message: 'Refreshed 4,812 programmes across 214 channels.',
    updated_at: NOW,
  },
  {
    id: 2,
    name: 'XMLTV mirror',
    source_type: 'xmltv',
    url: 'https://epg.example/xmltv.xml.gz',
    is_active: true,
    priority: 2,
    refresh_interval: 12,
    status: 'error',
    last_message: 'HTTP 502 from upstream after 3 retries.',
    updated_at: NOW,
  },
]

export const stubEPGData = [
  { id: 1, tvg_id: 'bbc.one.uk', name: 'BBC One HD', icon_url: null, epg_source: 1 },
  { id: 2, tvg_id: 'sky.sports.main.uk', name: 'Sky Sports Main Event', icon_url: null, epg_source: 1 },
  { id: 3, tvg_id: 'itv1.uk', name: 'ITV1 London', icon_url: null, epg_source: 2 },
]

export const stubLogos = [
  { id: 1, name: 'bbc-one.png', url: '/data/logos/bbc-one.png', cache_url: null, channel_count: 1 },
  { id: 2, name: 'sky-sports-main-event.png', url: '/data/logos/sky-sports.png', cache_url: null, channel_count: 1 },
]

export const stubStreamProfiles = [
  { id: 1, name: 'Default (ffmpeg passthrough)', is_active: true, locked: true, command: 'ffmpeg', parameters: '-i {streamUrl}' },
  { id: 2, name: 'Transcode 720p', is_active: true, locked: false, command: 'ffmpeg', parameters: '-i {streamUrl} -s 1280x720' },
]

export const stubChannelProfiles = [
  { id: 1, name: 'All channels', channels: [1, 2, 3], locked: true },
  { id: 2, name: 'Sports only', channels: [2], locked: false },
]

export const stubServerGroups = [
  { id: 1, name: 'EU edge servers' },
  { id: 2, name: 'US east' },
]

const stubTask = (id: number, name: string, status: string, taskId = `task_${id}`) => ({
  id,
  task_id: taskId,
  task_name: name,
  name,
  task_type: 'refresh_m3u',
  status,
  enabled: true,
  last_run: NOW,
  next_run: '2026-07-30T00:00:00Z',
  last_duration_seconds: 42.5,
  last_error: status === 'error' ? 'Provider returned HTTP 502 while refreshing group 4 of 11.' : null,
  schedules: [
    { id: id * 10, cron: '0 */6 * * *', enabled: true, description: 'Every six hours' },
  ],
  description: 'Refreshes the provider playlist and reconciles stream membership.',
})

export const stubTasks = [
  // `black_screen_scan` is deliberate: it is one of the two task ids in
  // TASKS_WITH_GROUP_PICKER (ScheduledTasksSection.tsx:403), and its group
  // picker IS the dialog that file contributes. `stream_probe` is the other,
  // but its Run Now button is hidden, so it can never open the dialog.
  stubTask(1, 'Black screen scan', 'success', 'black_screen_scan'),
  stubTask(2, 'Refresh EPG sources', 'error', 'refresh_epg'),
  stubTask(3, 'Refresh M3U accounts', 'running', 'refresh_m3u'),
]


/** ------------------------------------------------------------------ */
/** Route table.                                                        */
/** ------------------------------------------------------------------ */

/**
 * Return a value that satisfies BOTH `[...]` and `{ <key>: [...] }` readers.
 *
 * `services/api.ts` is inconsistent about envelopes on purpose — it mirrors
 * the backend, where `/channel-groups` returns a bare list and `/tasks`
 * returns `{ tasks: [...] }`. Reproducing that mapping endpoint by endpoint
 * in a stub is 200-odd guesses, and every wrong one shows up as a crashed
 * dialog whose real cause is the stub, not the component.
 *
 * A JS array can carry extra own properties, so one value can be the list
 * AND every envelope shape the codebase uses. The response object returned by
 * `fakeResponse` hands this straight to `.json()` without a JSON round-trip,
 * which is what keeps the aliases alive.
 */
function envelope<T>(list: T[]): T[] {
  const value = [...list] as T[] & Record<string, unknown>
  const aliases = [
    'results', 'items', 'data', 'groups', 'rules', 'tasks', 'history',
    'schedules', 'entries', 'targets', 'identities', 'sources', 'profiles',
    'channels', 'streams', 'logos', 'accounts', 'notifications', 'merges',
    'executions', 'candidates', 'tables', 'variables', 'conditions', 'actions',
    'diffs', 'conflicts', 'lineups', 'exclusions', 'reviews', 'snapshots',
    'files', 'backups', 'matches', 'programs', 'duplicate_groups', 'tags',
    'providers', 'users', 'stats',
  ]
  for (const key of aliases) value[key] = list
  value.count = list.length
  value.total = list.length
  value.total_count = list.length
  value.page = 1
  value.page_size = 50
  value.num_pages = 1
  value.next = null
  value.previous = null
  return value
}

export const STUB_ROUTES: StubRoute[] = [
  // --- auth / bootstrap -------------------------------------------------
  {
    match: /\/api\/auth\/status/,
    // `require_auth` + `setup_complete` are what AuthProvider gates on before
    // it will even call /auth/me (useAuth.tsx:88); without them `user` stays
    // null and every user-scoped dialog renders nothing at all.
    body: {
      require_auth: true,
      setup_complete: true,
      auth_enabled: true,
      setup_required: false,
      enabled_providers: ['local', 'plex', 'dispatcharr'],
      providers: ['local', 'plex', 'dispatcharr'],
    },
  },
  { match: /\/api\/auth\/setup-required/, body: { setup_required: false } },
  // Deliberately EMPTY: LinkedAccountsSection only offers a "Link <provider>"
  // button for an enabled provider that is not already linked, and that
  // button is the only way to reach its dialog.
  { match: /\/api\/auth\/identities/, body: envelope([]) },
  { match: /\/api\/auth\/admin\/users/, body: envelope([
    { id: 1, username: 'harness', is_admin: true, auth_provider: 'local', created_at: NOW, last_login: NOW },
  ]) },
  {
    match: /\/api\/auth\/me/,
    body: {
      user: {
        id: 1,
        username: 'harness',
        email: 'harness@example.com',
        display_name: 'Harness Operator',
        is_admin: true,
        is_active: true,
        auth_provider: 'local',
        external_id: null,
      },
      access_token_expires_in: 3600,
    },
  },
  { match: /\/api\/settings\/security/, body: { require_auth: true, session_timeout_minutes: 60 } },
  { match: /\/api\/settings\/mcp-status/, body: { enabled: false, tools: [] } },
  { match: /\/api\/settings/, body: harnessSettings() },
  { match: /\/api\/health/, body: { status: 'ok' } },

  // --- core catalogs ----------------------------------------------------
  { match: /\/api\/channel-groups\/orphaned/, body: envelope([
    { id: 11, name: 'UK | ENTERTAINMENT (orphaned)', channel_count: 0, stream_count: 0 },
    { id: 12, name: 'A Very Long Orphaned Group Name That Should Wrap', channel_count: 0, stream_count: 3 },
  ]) },
  { match: /\/api\/channel-groups\/with-streams/, body: envelope(stubChannelGroups) },
  { match: /\/api\/channel-groups\/hidden/, body: envelope([]) },
  { match: /\/api\/channel-groups\/auto-created/, body: envelope([]) },
  { match: /\/api\/channel-groups/, body: envelope(stubChannelGroups) },
  { match: /\/api\/channel-profiles/, body: envelope(stubChannelProfiles) },
  { match: /\/api\/channels\/logos/, body: envelope(stubLogos) },
  { match: /\/api\/channels\/export-csv/, body: 'name,number\nBBC One HD (London),101\n' },
  { match: /\/api\/channels\/csv-template/, body: 'name,number,group\n' },
  {
    match: /\/api\/channels\/find-duplicates/,
    body: {
      duplicate_groups: [
        { normalized_name: 'bbc one hd', channels: stubChannels.slice(0, 2), suggested_keep_id: 1 },
      ],
      groups: [
        { normalized_name: 'bbc one hd', channels: stubChannels.slice(0, 2), suggested_keep_id: 1 },
      ],
      total_groups: 1,
      total_channels: 2,
    },
  },
  { match: /\/api\/channels\/\d+\/streams/, body: envelope(stubStreams) },
  { match: /\/api\/channels/, body: envelope(stubChannels) },
  { match: /\/api\/stream-groups/, body: envelope([
    { name: 'UK | ENTERTAINMENT', count: 210 },
    { name: 'UK | SPORTS', count: 64 },
  ]) },
  { match: /\/api\/stream-profiles/, body: envelope(stubStreamProfiles) },
  { match: /\/api\/streams\/stale-ids/, body: envelope([]) },
  { match: /\/api\/streams/, body: envelope(stubStreams) },
  { match: /\/api\/providers\/group-settings\/by-provider/, body: {} },
  { match: /\/api\/providers\/group-settings/, body: envelope([]) },
  { match: /\/api\/providers\/catchup-status/, body: {} },
  { match: /\/api\/providers/, body: envelope(stubM3UAccounts) },
  { match: /\/api\/m3u\/accounts\/\d+\/groups/, body: envelope([
    { id: 1, name: 'UK | ENTERTAINMENT', enabled: true, channel_group_id: 1, auto_sync: true, custom_properties: null, stream_count: 210, auto_channel_sync: true },
    { id: 2, name: 'UK | SPORTS', enabled: false, channel_group_id: null, auto_sync: false, custom_properties: null, stream_count: 64, auto_channel_sync: false },
  ]) },
  { match: /\/api\/m3u\/accounts\/\d+\/profiles/, body: envelope([
    { id: 1, name: 'Default profile', max_streams: 5, is_active: true, is_default: true, search_pattern: '', replace_pattern: '', locked: true },
  ]) },
  { match: /\/api\/m3u\/accounts\/\d+\/filters/, body: envelope([
    { id: 1, filter_type: 'group', regex_pattern: '^UK \\|', exclude: false, order: 0 },
  ]) },
  { match: /\/api\/m3u\/accounts/, body: envelope(stubM3UAccounts) },
  { match: /\/api\/m3u\/server-groups/, body: envelope(stubServerGroups) },
  { match: /\/api\/m3u\/changes\/summary/, body: { added: 12, removed: 3, changed: 7 } },
  { match: /\/api\/m3u\/changes/, body: envelope([]) },
  { match: /\/api\/m3u\/digest\/settings/, body: { enabled: true, frequency: 'daily', recipients: ['ops@example.com'] } },
  { match: /\/api\/epg\/sources/, body: envelope(stubEPGSources) },
  { match: /\/api\/epg\/data/, body: envelope(stubEPGData) },
  { match: /\/api\/epg\/grid/, body: envelope([]) },
  { match: /\/api\/epg\/migration\/preview/, body: { migrations: [], unmatched: [] } },

  // --- tasks / schedules ------------------------------------------------
  { match: /\/api\/tasks\/[\w%-]+\/history/, body: envelope([
    { id: 1, started_at: NOW, finished_at: NOW, status: 'success', duration_seconds: 42.5, message: 'Refreshed 2 accounts.' },
  ]) },
  { match: /\/api\/tasks\/[\w%-]+\/schedules/, body: envelope(stubTasks[0].schedules) },
  { match: /\/api\/tasks\/[\w%-]+$/, body: stubTasks[0] },
  { match: /\/api\/tasks/, body: envelope(stubTasks) },

  // --- backup / restore / sync targets ----------------------------------
  { match: /\/api\/backup\/saved/, body: envelope([
    { filename: 'ecm-backup-2026-07-28.zip', size_bytes: 4_812_390, created_at: NOW, kind: 'manual', size: 4_812_390, name: 'ecm-backup-2026-07-28.zip' },
    { filename: 'ecm-backup-2026-07-21.zip', size_bytes: 4_508_112, created_at: NOW, kind: 'scheduled', size: 4_508_112, name: 'ecm-backup-2026-07-21.zip' },
  ]) },
  { match: /\/api\/backup\/export-sections/, body: envelope([
    { key: 'channels', label: 'Channels and groups', count: 3 },
    { key: 'm3u', label: 'M3U accounts', count: 2 },
    { key: 'epg', label: 'EPG sources', count: 2 },
  ]) },
  { match: /\/api\/backup/, body: { status: 'idle', last_backup: NOW } },
  { match: /\/api\/(sync-targets|cloud-targets)/, body: envelope([
    { id: 1, name: 'Backblaze B2 (offsite)', kind: 's3', provider: 's3', bucket: 'ecm-backups', prefix: 'nightly/', enabled: true, last_status: 'success', last_run: NOW, config: {} },
  ]) },

  // --- normalization / tags ---------------------------------------------
  {
    // POST dry-run. Its `diffs` drive the whole apply modal; the generic write
    // fallback would leave it undefined and crash the dialog on open.
    match: /\/api\/normalization\/apply-to-channels/,
    body: envelope([
      { channel_id: 1, channel_name: 'BBC One HD (London)', current_name: 'UK| BBC ONE HD', new_name: 'BBC One HD', collision: false, collides_with: null },
      { channel_id: 2, channel_name: 'Sky Sports Main Event Ultra HD', current_name: 'UK| SKY SPORTS MAIN EVENT UHD', new_name: 'Sky Sports Main Event', collision: true, collides_with: 3 },
    ]),
  },
  { match: /\/api\/normalization\/rule-stats/, body: envelope([
    { rule_id: 1, rule_name: 'Strip UK pipe prefix', match_count: 210, sample_before: 'UK| BBC ONE HD', sample_after: 'BBC ONE HD' },
  ]) },
  { match: /\/api\/normalization\/rules/, body: envelope([stubNormalizationGroup()]) },
  { match: /\/api\/normalization\/groups/, body: envelope([stubNormalizationGroup()]) },
  { match: /\/api\/normalization\/export/, body: 'groups: []\n' },
  { match: /\/api\/normalization/, body: envelope([]) },
  { match: /\/api\/tags\/groups/, body: envelope([stubTagGroup()]) },
  { match: /\/api\/tags\/export/, body: 'groups: []\n' },
  { match: /\/api\/tags/, body: envelope([stubTagGroup()]) },
  // --- channel pipeline -------------------------------------------------
  { match: /\/api\/channel-pipeline\/circuit-breaker/, body: {
    state: 'open',
    open: true,
    disabled: true,
    tripped_at: NOW,
    consecutive_failures: 5,
    failure_count: 5,
    threshold: 5,
    cooldown_seconds: 900,
    last_error: 'Rule "Sports auto-create" raised 5 consecutive execution errors.',
  } },
  { match: /\/api\/channel-pipeline\/schema\/conditions/, body: envelope([
    { field: 'group_name', label: 'Stream group name', type: 'string', operators: ['equals', 'contains', 'matches'], description: 'The provider group the stream was listed under.' },
    { field: 'stream_name', label: 'Stream name', type: 'string', operators: ['equals', 'contains', 'matches'], description: 'The raw stream title from the playlist.' },
  ]) },
  { match: /\/api\/channel-pipeline\/schema\/actions/, body: envelope([
    { action_type: 'create_channel', label: 'Create channel', description: 'Creates a channel for each matching stream.', parameters: [
      { name: 'channel_group_id', label: 'Channel group', type: 'group', required: true },
      { name: 'starting_number', label: 'Starting number', type: 'number', required: false },
    ] },
  ]) },
  { match: /\/api\/channel-pipeline\/schema\/template-variables/, body: envelope([
    { name: 'stream_name', label: 'Stream name', description: 'The raw stream title.', example: 'UK| BBC ONE HD' },
  ]) },
  { match: /\/api\/channel-pipeline\/rules/, body: envelope(stubPipelineRules()) },
  {
    // POST restore-snapshot. The result summary modal reads
    // `failed_channels.length`, so the generic write fallback crashes it.
    match: /\/api\/channel-pipeline\/executions\/\d+\/restore-snapshot/,
    body: { success: true, removed_channels: 12, restored_channels: 3, failed_channels: [] },
  },
  {
    // TWO rows on purpose. The executions table shows Rollback only on a
    // has_snapshot=false completed execute run and "Undo this run" only on a
    // has_snapshot=true one (ChannelPipelineTab.tsx:1379/1401), so one row
    // can never reach both confirm dialogs.
    match: /\/api\/channel-pipeline\/executions/,
    body: envelope([stubExecution(1, false), stubExecution(2, true)]),
  },
  { match: /\/api\/channel-pipeline\/export\/yaml/, body: 'rules: []\n' },
  { match: /\/api\/channel-pipeline/, body: envelope(stubPipelineRules()) },
  {
    // Full EventSyncExclusionRecord including `evidence` — the panel reads
    // `row.evidence.stream_name` unconditionally, so a partial row crashes
    // the whole Channel Pipeline tab, not just the panel.
    match: /\/api\/event-sync-exclusions/,
    body: envelope([
      {
        id: 1,
        rule_id: 2,
        provider_id: 1,
        stream_name_hash: 'a1b2c3d4e5f6a7b8c9d0',
        event_key: 'football:2026-07-29:man-utd-v-arsenal',
        created_at: 1_785_320_000,
        note: 'Pay-per-view events are handled manually.',
        evidence: {
          rule_name: 'Event sync — Premier League',
          stream_name: 'UK| PPV 04: MAN UTD V ARSENAL',
          provider: 'Primary Provider (EU edge)',
          stream_id: 3,
          master_channel_name: 'Sky Sports Main Event Ultra HD',
          master_channel_id: 2,
          score: 0.71,
          time_delta_minutes: 15,
          ambiguous_reason: null,
        },
      },
    ]),
  },
  {
    match: /^\/api\/event-sync-reviews(?:\?|$)/,
    method: 'GET',
    body: envelope([
      {
        id: 17,
        rule_id: 2,
        provider_id: 1,
        stream_name_hash: 'a1b2c3d4e5f6a7b8c9d0',
        event_key: 'football:2026-07-29:man-utd-v-arsenal',
        status: 'pending',
        created_at: 1_785_320_000_000,
        last_seen_at: 1_785_320_000_000,
        resolved_at: null,
        resolution_source: null,
        evidence: {
          rule_name: 'Event sync — Premier League',
          stream_name: 'UK| PPV 04: MAN UTD V ARSENAL',
          provider: 'Primary Provider (EU edge)',
          stream_id: 3,
          stream_parsed_title: 'Manchester United v Arsenal',
          stream_parsed_start: '2026-07-29T20:00:00Z',
          master_channel_name: 'Sky Sports Main Event Ultra HD',
          master_channel_id: 2,
          master_parsed_title: 'Manchester United v Arsenal',
          master_parsed_start: '2026-07-29T20:15:00Z',
          score: 0.71,
          band: 'review',
          team_verdict: 'agree',
          time_delta_minutes: 15,
          ambiguous_reason: 'contested_top_candidates',
        },
      },
    ]),
  },
  { match: /\/api\/event-sync\/team-aliases/, body: { aliases: { 'Man Utd': ['Manchester United'] } } },
  { match: /\/api\/channel-merges\/snapshot/, body: { pending: 2, merged: 8, dismissed: 1, total: 11 } },
  { match: /\/api\/channel-merges/, body: envelope([stubPendingMerge()]) },

  // --- dummy EPG --------------------------------------------------------
  { match: /\/api\/dummy-epg\/profiles\/export\/yaml/, body: 'profiles: []\n' },
  {
    // DummyEPGChannelPicker sorts on `channel_name`, not `name` — the plain
    // channel shape crashes it.
    match: /\/api\/dummy-epg\/profiles\/\d+\/channels/,
    body: envelope(
      stubChannels.map((c) => ({
        id: c.id,
        channel_id: c.id,
        channel_name: c.name,
        channel_number: c.channel_number,
        profile_id: 1,
      }))
    ),
  },
  { match: /\/api\/dummy-epg\/profiles/, body: envelope([
    { id: 1, name: '24/7 filler — movies', description: 'Generates rolling 4-hour blocks for movie channels.', channel_count: 18, channels: [1, 2], updated_at: NOW, created_at: NOW, is_default: false },
  ]) },
  { match: /\/api\/dummy-epg/, body: envelope([]) },

  // --- stats / probe ----------------------------------------------------
  {
    // Full ChannelPopularityScore — the modal calls .toFixed() on `score` and
    // `trend_percent`, so a partial shape crashes rather than degrades.
    match: /\/api\/stats\/popularity\/channel\//,
    body: {
      id: 1,
      channel_id: '11111111-1111-4111-8111-111111111111',
      channel_name: 'BBC One HD (London)',
      score: 87.4213,
      rank: 3,
      watch_count_7d: 310,
      watch_time_7d: 128_400,
      unique_viewers_7d: 42,
      bandwidth_7d: 981_234_567,
      trend: 'up',
      trend_percent: 12.5,
      previous_score: 77.7,
      previous_rank: 5,
      calculated_at: NOW,
      created_at: NOW,
      updated_at: NOW,
    },
  },
  { match: /\/api\/stats\/channels\/\d+/, body: {
    channel_id: 1,
    channel_name: 'BBC One HD (London)',
    total_watch_time_seconds: 128_400,
    unique_viewers: 42,
    sessions: 310,
    average_bitrate_kbps: 6400,
    last_watched: NOW,
  } },
  { match: /\/api\/stats\/popularity\/rankings/, body: envelope([]) },
  { match: /\/api\/stats/, body: envelope([]) },
  { match: /\/api\/stream-stats\/probe\/progress/, body: { running: false, completed: 0, total: 0, progress: 0 } },
  {
    // A full ProbeHistoryEntry with `reordered_channels` — the Settings probe
    // history rows are what open BOTH the probe-results and reorder modals.
    match: /\/api\/stream-stats\/probe\/history/,
    body: envelope([
      {
        timestamp: NOW,
        end_timestamp: NOW,
        duration_seconds: 184,
        total: 3,
        success_count: 2,
        failed_count: 1,
        skipped_count: 0,
        status: 'completed',
        success_streams: [
          { id: 1, name: 'UK| BBC ONE HD', url: 'http://provider.example/live/bbc-one-hd.ts' },
          { id: 2, name: 'UK| BBC ONE FHD (BACKUP FEED, LONDON REGION)', url: 'http://backup.example/live/bbc-one-fhd.ts' },
        ],
        failed_streams: [
          { id: 3, name: 'UK| SKY SPORTS MAIN EVENT UHD', url: 'http://provider.example/live/sky-sports-main-uhd.ts', error: 'Connection timed out after 15s.' },
        ],
        skipped_streams: [],
        black_screen_count: 0,
        black_screen_streams: [],
        low_fps_count: 0,
        low_fps_streams: [],
        reordered_channels: [
          {
            channel_id: 1,
            channel_name: 'BBC One HD (London)',
            stream_count: 2,
            streams_before: [
              { id: 2, name: 'UK| BBC ONE FHD (BACKUP FEED, LONDON REGION)', position: 0, status: 'failed', resolution: '1920x1080', bitrate: 4200 },
              { id: 1, name: 'UK| BBC ONE HD', position: 1, status: 'ok', resolution: '1920x1080', bitrate: 6400 },
            ],
            streams_after: [
              { id: 1, name: 'UK| BBC ONE HD', position: 0, status: 'ok', resolution: '1920x1080', bitrate: 6400 },
              { id: 2, name: 'UK| BBC ONE FHD (BACKUP FEED, LONDON REGION)', position: 1, status: 'failed', resolution: '1920x1080', bitrate: 4200 },
            ],
          },
        ],
        sort_config: { priority: ['resolution', 'bitrate'], enabled: { resolution: true, bitrate: true } },
      },
    ]),
  },
  { match: /\/api\/stream-stats/, body: envelope([]) },

  // --- misc -------------------------------------------------------------
  {
    match: /\/api\/profile-conflict-reviews\/\d+\/accept$/,
    method: 'POST',
    body: {
      status: 'accepted',
      applied: false,
      updated_account_ids: [1],
      failed_account_ids: [2],
      retry_error: 'account 2: harness partial failure',
    },
  },
  {
    match: /\/api\/profile-conflict-reviews$/,
    body: {
      reviews: [{
        id: 901,
        fingerprint: 'harness-profile-conflict',
        effective_group_id: 665,
        status: 'pending',
        accepted_choice_key: null,
        accepted_profile_ids: null,
        created_at: 1,
        last_seen_at: 1,
        resolved_at: null,
        applied_at: null,
        retry_error: null,
        evidence: {
          fingerprint_version: 1,
          target: { effective_group_id: 665, name: 'NBA Events' },
          choices: [
            {
              choice_key: 'harness-choice-a',
              profile_ids: [6, 7],
              profile_names: ['Sports', 'Family'],
              sources: [{ source_group_id: 823, source_group_name: 'NBA US', m3u_account_id: 1, m3u_account_name: 'Primary provider' }],
            },
            {
              choice_key: 'harness-choice-b',
              profile_ids: [14],
              profile_names: ['Strong only'],
              sources: [{ source_group_id: 2866, source_group_name: 'NBA Backup', m3u_account_id: 2, m3u_account_name: 'Backup provider' }],
            },
          ],
        },
      }],
      total: 1,
    },
  },
  { match: /\/api\/notifications/, body: envelope([]) },
  { match: /\/api\/journal\/stats/, body: { total: 0, by_kind: {} } },
  { match: /\/api\/journal/, body: envelope([]) },
  { match: /\/api\/alert-methods/, body: envelope([]) },
  { match: /\/api\/tls/, body: { enabled: false, certificate: null, status: 'disabled' } },
]

function stubPipelineRules() {
  return [
    {
      id: 1,
      name: 'Sports auto-create',
      description: 'Creates channels for every stream in the provider "UK | SPORTS" group.',
      rule_type: 'auto_create',
      enabled: true,
      priority: 10,
      conditions: [{ id: 1, field: 'group_name', operator: 'equals', value: 'UK | SPORTS', case_sensitive: false }],
      actions: [{ id: 1, action_type: 'create_channel', parameters: { channel_group_id: 2, starting_number: 2000 } }],
      condition_logic: 'AND',
      last_run: NOW,
      last_result: 'success',
      match_count: 64,
      created_at: NOW,
      updated_at: NOW,
    },
    {
      id: 2,
      name: 'Event sync — Premier League',
      description: 'Keeps event channels in sync with the fixture list.',
      rule_type: 'event_sync',
      // Present-and-truthy is what routes the row's Run icon through the
      // live-run confirm instead of running immediately (ChannelPipelineTab
      // .tsx:1213); refresh_providers_before_run does the same for Test.
      event_sync_config: {
        sport: 'football',
        league: 'Premier League',
        refresh_providers_before_run: true,
        title_pattern: '{home} vs {away}',
      },
      enabled: false,
      priority: 20,
      conditions: [],
      actions: [],
      condition_logic: 'AND',
      last_run: null,
      last_result: null,
      match_count: 0,
      created_at: NOW,
      updated_at: NOW,
    },
  ]
}

function stubExecution(id: number, hasSnapshot: boolean) {
  return {
    id,
    rule_id: 1,
    rule_name: 'Sports auto-create',
    mode: 'execute',
    triggered_by: 'manual',
    started_at: NOW,
    completed_at: NOW,
    duration_seconds: 4.2,
    status: 'completed',
    streams_evaluated: 12_418,
    streams_matched: 64,
    channels_created: 12,
    channels_updated: 3,
    groups_created: 1,
    streams_merged: 2,
    streams_skipped: 5,
    streams_excluded: 0,
    created_entities: [{ type: 'channel', id: 2, name: 'Sky Sports Main Event Ultra HD' }],
    modified_entities: [{ type: 'channel', id: 1, name: 'BBC One HD (London)', previous: {} }],
    // ExecutionLogEntry is per-STREAM, not a log line — it carries
    // stream_name / rules_evaluated / actions_executed. A log-line shape here
    // crashed the details modal.
    execution_log: [
      {
        stream_id: 3,
        stream_name: 'UK| SKY SPORTS MAIN EVENT UHD',
        m3u_account_id: 1,
        rules_evaluated: [
          { rule_id: 1, rule_name: 'Sports auto-create', matched: true, conditions: [] },
        ],
        actions_executed: [
          { action_type: 'create_channel', success: true, message: 'Created channel 2001.' },
        ],
      },
    ],
    warnings: [],
    has_snapshot: hasSnapshot,
    has_non_reversible_profile_changes: false,
  }
}

function stubNormalizationGroup() {
  return {
    id: 1,
    name: 'Country prefixes',
    description: 'Strips `UK|` style provider prefixes from stream names.',
    enabled: true,
    order: 0,
    rule_count: 1,
    rules: [
      {
        id: 1,
        group_id: 1,
        name: 'Strip UK pipe prefix',
        rule_type: 'regex',
        pattern: '^UK\\s*\\|\\s*',
        replacement: '',
        enabled: true,
        order: 0,
        case_sensitive: false,
        match_count: 210,
      },
    ],
  }
}

function stubTagGroup() {
  return {
    id: 1,
    name: 'Quality markers',
    description: 'HD / FHD / UHD markers pulled off the end of provider stream names.',
    enabled: true,
    tags: [
      { id: 1, group_id: 1, name: 'HD', pattern: '\\bHD\\b', match_count: 120, enabled: true },
      { id: 2, group_id: 1, name: 'UHD', pattern: '\\bUHD\\b', match_count: 14, enabled: true },
    ],
  }
}

function stubPendingMerge() {
  // Field-for-field PendingMergeRecord. An approximate shape rendered
  // "Channel no longer exists (id )" and "NaN% match", which would have been
  // baked into the baseline as if it were the real empty/derived state.
  return {
    id: 1,
    stream_name: 'UK| BBC ONE FHD (BACKUP FEED, LONDON REGION)',
    group_id: 1,
    candidate_channel_id: '1',
    candidate_channel_name: 'BBC One HD (London)',
    candidate_channel_number: 101,
    candidate_channel_group_name: 'United Kingdom — Entertainment',
    confidence: 0.94,
    status: 'pending',
    created_at: 1_785_320_000,
    resolved_at: null,
    resolution_source: null,
    trigger_context: 'auto_create',
  }
}

function harnessSettings() {
  return {
    theme: 'dark',
    dispatcharr_url: 'http://dispatcharr.example:9191',
    date_format_locale: 'en-GB',
    show_stream_urls: true,
    hide_epg_urls: false,
    strike_threshold: 3,
    epg_auto_match_threshold: 80,
    gracenote_conflict_mode: 'prompt',
    allow_multi_provider_auto_sync: false,
    default_normalize_on_create: true,
    auto_rename_channel_number: false,
    user_timezone: 'Europe/London',
  }
}

/** ------------------------------------------------------------------ */
/** Installation                                                        */
/** ------------------------------------------------------------------ */

function resolveBody(route: StubRoute, m: RegExpMatchArray): unknown {
  return typeof route.body === 'function'
    ? (route.body as (mm: RegExpMatchArray) => unknown)(m)
    : route.body
}

/**
 * A `Response`-shaped object that hands the stub value to `.json()` WITHOUT a
 * JSON round-trip, so the `envelope()` aliases survive. `services/httpClient.ts`
 * only touches `ok`, `status`, `json()` and `text()`.
 */
function fakeResponse(body: unknown, status: number): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: '',
    headers: new Headers({ 'Content-Type': 'application/json' }),
    json: async () => body,
    text: async () => (typeof body === 'string' ? body : JSON.stringify(body)),
    clone() {
      return fakeResponse(body, status)
    },
  } as unknown as Response
}

/**
 * Install the stub over `window.fetch`.
 *
 * `live` short-circuits the whole thing (manual exploration only) — the
 * measurement script never sets it, so captured baselines are always the
 * deterministic path.
 */
export function installApiStub(options: { live?: boolean } = {}): void {
  if (options.live) return

  const realFetch = window.fetch.bind(window)

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url =
      typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
    const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase()

    if (!url.includes('/api/')) {
      // Fonts, images, source maps: leave alone.
      return realFetch(input as RequestInfo, init)
    }

    state.pending += 1
    state.calls.push(`${method} ${url}`)
    try {
      const route = STUB_ROUTES.find(
        (r) => (!r.method || r.method === method) && r.match.test(url)
      )
      if (route) {
        const m = url.match(route.match) as RegExpMatchArray
        return fakeResponse(resolveBody(route, m), route.status ?? 200)
      }

      state.unstubbedCalls.push(`${method} ${url}`)
      // Writes never reach a backend. They resolve as an empty success in the
      // same permissive shape as reads, so a dialog that renders its own POST
      // result degrades to an empty state instead of crashing on a missing key.
      const empty = envelope([]) as unknown as Record<string, unknown>
      empty.success = true
      empty.detail = 'stubbed by the modal harness'
      return fakeResponse(empty, 200)
    } finally {
      state.pending -= 1
    }
  }
}
