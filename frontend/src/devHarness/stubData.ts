/**
 * Typed stub domain objects for the dev-only modal harness
 * (bead enhancedchannelmanager-xhldy.1).
 *
 * These are the REAL exported types, not `any` — so a shape change in
 * `src/types` breaks the harness build rather than quietly making it render
 * an empty state that gets baked into a baseline.
 *
 * Values are chosen to be typographically representative rather than
 * minimal: at least one long name that must wrap, one four-digit number, one
 * multi-sentence free-text field, and one of each status variant, so a
 * dialog's error / warning / success type styles are all on screen at once.
 */
import type {
  Channel,
  ChannelGroup,
  ChannelProfile,
  EPGSource,
  Logo,
  M3UAccount,
  ServerGroup,
  Stream,
  StreamProfile,
} from '../types'

const NOW = '2026-07-29T12:00:00Z'

export const channelGroups: ChannelGroup[] = [
  { id: 1, name: 'United Kingdom — Entertainment', channel_count: 42, is_auto_sync: true },
  { id: 2, name: 'Sports', channel_count: 18, is_auto_sync: false },
  { id: 3, name: 'A Group Name Long Enough To Wrap Onto A Second Line', channel_count: 0 },
]

export const channels: Channel[] = [
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
    channel_group_id: 2,
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
    channel_group_id: 3,
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

export const streams: Stream[] = [
  {
    id: 1,
    name: 'UK| BBC ONE HD',
    url: 'http://provider.example/live/bbc-one-hd.ts',
    m3u_account: 1,
    logo_url: null,
    tvg_id: 'bbc.one.uk',
    channel_group: 1,
    channel_group_name: 'UK | ENTERTAINMENT',
    is_custom: false,
    is_stale: false,
    last_seen: NOW,
    is_catchup: true,
    catchup_days: 7,
  },
  {
    id: 2,
    name: 'UK| BBC ONE FHD (BACKUP FEED, LONDON REGION)',
    url: 'http://backup.example/live/bbc-one-fhd.ts',
    m3u_account: 2,
    logo_url: null,
    tvg_id: 'bbc.one.uk',
    channel_group: 1,
    channel_group_name: 'UK | ENTERTAINMENT',
    is_custom: false,
    is_stale: true,
    last_seen: NOW,
  },
  {
    id: 3,
    name: 'UK| SKY SPORTS MAIN EVENT UHD',
    url: 'http://provider.example/live/sky-sports-main-uhd.ts',
    m3u_account: 1,
    logo_url: null,
    tvg_id: 'sky.sports.main.uk',
    channel_group: 2,
    channel_group_name: 'UK | SPORTS',
    is_custom: false,
  },
]

export const logos: Logo[] = [
  { id: 1, name: 'bbc-one.png', url: '/data/logos/bbc-one.png', cache_url: '/data/logos/bbc-one.png', channel_count: 1, is_used: true },
  { id: 2, name: 'sky-sports-main-event-with-a-long-filename.png', url: '/data/logos/sky.png', cache_url: '/data/logos/sky.png', channel_count: 1, is_used: true },
  { id: 3, name: 'unused-logo.png', url: '/data/logos/unused.png', cache_url: '/data/logos/unused.png', channel_count: 0, is_used: false },
]

export const streamProfiles: StreamProfile[] = [
  { id: 1, name: 'Default (ffmpeg passthrough)', command: 'ffmpeg', parameters: '-i {streamUrl} -c copy', is_active: true, locked: true },
  { id: 2, name: 'Transcode 720p', command: 'ffmpeg', parameters: '-i {streamUrl} -s 1280x720 -c:v libx264', is_active: true, locked: false },
]

export const channelProfiles: ChannelProfile[] = [
  { id: 1, name: 'All channels', channels: [1, 2, 3] },
  { id: 2, name: 'Sports only', channels: [2] },
]

export const serverGroups: ServerGroup[] = [
  { id: 1, name: 'EU edge servers' },
  { id: 2, name: 'US east' },
]

export const m3uAccounts: M3UAccount[] = [
  {
    id: 1,
    name: 'Primary Provider (EU edge)',
    server_url: 'http://provider.example/get.php?username=ecm&type=m3u_plus',
    file_path: null,
    server_group: 1,
    max_streams: 5,
    is_active: true,
    created_at: NOW,
    updated_at: NOW,
    user_agent: null,
    profiles: [],
    locked: false,
    channel_groups: [],
    refresh_interval: 12,
    custom_properties: null,
    account_type: 'XC' as M3UAccount['account_type'],
    username: 'ecm-primary',
    password: 'hunter2',
    stale_stream_days: 7,
    priority: 1,
    status: 'success' as M3UAccount['status'],
    last_message: 'Refreshed 12,418 streams across 214 groups in 41s.',
    enable_vod: false,
    auto_enable_new_groups_live: true,
    auto_enable_new_groups_vod: false,
    auto_enable_new_groups_series: false,
  },
  {
    id: 2,
    name: 'Backup Provider',
    server_url: 'http://backup.example/playlist.m3u',
    file_path: null,
    server_group: null,
    max_streams: 2,
    is_active: false,
    created_at: NOW,
    updated_at: null,
    user_agent: null,
    profiles: [],
    locked: false,
    channel_groups: [],
    refresh_interval: 24,
    custom_properties: null,
    account_type: 'STD' as M3UAccount['account_type'],
    username: null,
    password: null,
    stale_stream_days: 14,
    priority: 2,
    status: 'error' as M3UAccount['status'],
    last_message: 'HTTP 502 from upstream after 3 retries. Last successful refresh was 4 days ago.',
    enable_vod: false,
    auto_enable_new_groups_live: false,
    auto_enable_new_groups_vod: false,
    auto_enable_new_groups_series: false,
  },
]

export const epgSources: EPGSource[] = [
  {
    id: 1,
    name: 'Schedules Direct — United Kingdom',
    source_type: 'schedules_direct' as EPGSource['source_type'],
    url: 'https://json.schedulesdirect.org/20141201/',
    api_key: null,
    username: 'ecm-sd',
    is_active: true,
    file_path: null,
    refresh_interval: 24,
    priority: 1,
    status: 'success' as EPGSource['status'],
    last_message: 'Refreshed 4,812 programmes across 214 channels.',
    created_at: NOW,
    updated_at: NOW,
    custom_properties: null,
    epg_data_count: '214',
  },
  {
    id: 2,
    name: 'XMLTV mirror',
    source_type: 'xmltv' as EPGSource['source_type'],
    url: 'https://epg.example/xmltv.xml.gz',
    api_key: null,
    username: null,
    is_active: true,
    file_path: null,
    refresh_interval: 12,
    priority: 2,
    status: 'error' as EPGSource['status'],
    last_message: 'HTTP 502 from upstream after 3 retries.',
    created_at: NOW,
    updated_at: NOW,
    custom_properties: null,
    epg_data_count: '58',
  },
]

/** Shape used by EditChannelModal / MergeChannelsModal (narrower than EPGData). */
export const epgEntries = [
  { id: 1, tvg_id: 'bbc.one.uk', name: 'BBC One HD', icon_url: null, epg_source: 1 },
  { id: 2, tvg_id: 'sky.sports.main.uk', name: 'Sky Sports Main Event', icon_url: null, epg_source: 1 },
  { id: 3, tvg_id: 'itv1.uk', name: 'ITV1 London', icon_url: null, epg_source: 2 },
]

export const epgSourceRefs = [
  { id: 1, name: 'Schedules Direct — United Kingdom', source_type: 'schedules_direct', priority: 1 },
  { id: 2, name: 'XMLTV mirror', source_type: 'xmltv', priority: 2 },
]

export const streamProfileRefs = [
  { id: 1, name: 'Default (ffmpeg passthrough)', is_active: true },
  { id: 2, name: 'Transcode 720p', is_active: true },
]

/** No-op callbacks. Named so a stack trace says which one fired. */
export const noop = () => {}
export const asyncNoop = async () => {}
