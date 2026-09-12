// Static task labels/descriptions from the 0.18.2-0033 acceptance fixture.
// These are private rendered-UI data, never tasks submitted to a scheduler.
const descriptions = [
  ['black_screen_scan', 'Black Screen Scan', 'Scan probed streams for black screens without re-probing'],
  ['auto_creation', 'Auto-Create Channels', 'Automatically create channels from streams based on rules'],
  ['cleanup', 'Database Cleanup', 'Clean up old probe history, task execution history, journal entries, and configured retained data'],
  ['dbas_backup', 'DBAS Backup', 'Build a redacted, sealed DBAS backup artifact (ZIP + SHA-256 sidecar) in /config/backups/. Scheduled or manual.'],
  ['dbas_restore', 'DBAS Restore', 'Restore a new-format DBAS backup artifact (validate -> decode -> dry-run by default; apply only when confirmed). Emits per-stage progress.'],
  ['dummy_epg_refresh', 'Dummy EPG Refresh', 'Regenerate ECM dummy EPG data and refresh in Dispatcharr'],
  ['epg_event_probe', 'EPG Event Probe', 'Probe every stream on a channel when a matching EPG event starts'],
  ['epg_refresh', 'EPG Refresh', 'Refresh EPG (Electronic Program Guide) data from sources'],
  ['failed_stream_reprobe', 'Re-probe Failed Streams', 'Re-probe only streams that previously failed or timed out'],
  ['journal_noise_purge', 'Journal Noise Purge', 'Purge automated-noise journal entries (watch start/stop events, automated Channel Pipeline rule create/delete pairs, run-on-refresh suppression notices, and scheduled-task start/complete rows) older than the retention window. Operator-initiated entries, task cancel/fail/error rows, and all other journal categories are untouched.'],
  ['m3u_change_monitor', 'M3U Change Monitor', 'Monitor M3U playlists for external changes'],
  ['m3u_digest', 'M3U Change Digest', 'Send email digest of M3U playlist changes'],
  ['m3u_refresh', 'M3U Refresh', 'Refresh M3U playlists from providers'],
  ['popularity_calculation', 'Popularity Calculation', 'Calculate channel popularity rankings from watch history'],
  ['stats_v2_rollup', 'Stats v2 Rollup & Prune', "Aggregate yesterday's session_telemetry into the per-user and per-provider daily rollup tables, then prune raw rows older than the retention window (ADR-007)."],
  ['stream_probe', 'Stream Probe', 'Probe streams to collect metadata (resolution, bitrate, codecs)'],
  ['struck_stream_cleanup', 'Struck Stream Cleanup', 'Remove struck-out streams from channels'],
  ['yaml_backup', 'YAML Backup', 'Export ECM configuration as YAML and save to /config/backups/'],
];

export const settingsTasks = descriptions.map(([task_id, task_name, task_description]) => ({
  task_id, task_name, task_description, enabled: true, effective_enabled: true,
  status: 'idle', progress: { total: 0, current: 0, status: 'idle', success_count: 0, failed_count: 0, skipped_count: 0 },
  schedule: { schedule_type: 'manual' }, schedules: [], last_run: null, next_run: null, config: {}, show_notifications: true,
}));
