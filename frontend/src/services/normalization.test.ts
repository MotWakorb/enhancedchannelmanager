/**
 * Unit tests for normalization functionality in API service.
 */
import { describe, it, expect, afterEach, beforeAll, afterAll } from 'vitest';
import { server } from '../test/mocks/server';
import { http, HttpResponse } from 'msw';
import {
  normalizeTexts,
  getSettings,
  saveSettings,
  createChannel,
} from './api';

// Start/stop the mock server for these tests
beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('Normalization API', () => {
  describe('normalizeTexts', () => {
    it('sends batch of texts to normalize endpoint', async () => {
      let requestBody: { texts: string[] } | null = null;
      server.use(
        http.post('/api/normalization/normalize', async ({ request }) => {
          requestBody = await request.json() as { texts: string[] };
          return HttpResponse.json({
            results: [
              { original: 'ESPN HD', normalized: 'ESPN', changed: true },
              { original: 'CNN', normalized: 'CNN', changed: false },
            ],
          });
        })
      );

      const result = await normalizeTexts(['ESPN HD', 'CNN']);

      expect(requestBody?.texts).toEqual(['ESPN HD', 'CNN']);
      expect(result.results).toHaveLength(2);
    });

    it('returns normalized results correctly', async () => {
      server.use(
        http.post('/api/normalization/normalize', () => {
          return HttpResponse.json({
            results: [
              { original: 'FOX Sports 1 HD', normalized: 'FOX Sports 1', changed: true },
            ],
          });
        })
      );

      const result = await normalizeTexts(['FOX Sports 1 HD']);

      expect(result.results[0].original).toBe('FOX Sports 1 HD');
      expect(result.results[0].normalized).toBe('FOX Sports 1');
      expect(result.results[0].changed).toBe(true);
    });

    it('handles empty input array', async () => {
      server.use(
        http.post('/api/normalization/normalize', () => {
          return HttpResponse.json({
            results: [],
          });
        })
      );

      const result = await normalizeTexts([]);

      expect(result.results).toEqual([]);
    });

    it('handles network errors', async () => {
      server.use(
        http.post('/api/normalization/normalize', () => {
          return HttpResponse.error();
        })
      );

      await expect(normalizeTexts(['ESPN'])).rejects.toThrow();
    });
  });

  describe('Settings - normalize_on_channel_create', () => {
    it('getSettings returns normalize_on_channel_create field', async () => {
      server.use(
        http.get('/api/settings', () => {
          return HttpResponse.json({
            url: 'http://localhost:8090',
            username: 'admin',
            configured: true,
            auto_rename_channel_number: false,
            include_channel_number_in_name: false,
            channel_number_separator: '-',
            remove_country_prefix: false,
            include_country_in_name: false,
            country_separator: '|',
            timezone_preference: 'both',
            show_stream_urls: true,
            hide_auto_sync_groups: false,
            hide_ungrouped_streams: true,
            hide_epg_urls: false,
            hide_m3u_urls: false,
            gracenote_conflict_mode: 'ask',
            theme: 'dark',
            default_channel_profile_ids: [],
            linked_m3u_accounts: [],
            epg_auto_match_threshold: 80,
            custom_network_prefixes: [],
            custom_network_suffixes: [],
            stats_poll_interval: 10,
            user_timezone: '',
            backend_log_level: 'INFO',
            frontend_log_level: 'INFO',
            vlc_open_behavior: 'm3u_fallback',
            stream_probe_batch_size: 10,
            stream_probe_timeout: 30,
            stream_probe_schedule_time: '03:00',
            bitrate_sample_duration: 10,
            parallel_probing_enabled: true,
            max_concurrent_probes: 8,
            skip_recently_probed_hours: 0,
            refresh_m3us_before_probe: true,
            auto_reorder_after_probe: false,
            stream_fetch_page_limit: 200,
            stream_sort_priority: ['resolution', 'bitrate'],
            stream_sort_enabled: { resolution: true, bitrate: true },
            m3u_account_priorities: {},
            deprioritize_failed_streams: true,
            normalization_settings: { disabledBuiltinTags: [], customTags: [] },
            normalize_on_channel_create: true,
          });
        })
      );

      const result = await getSettings();

      expect(result.normalize_on_channel_create).toBe(true);
    });

    it('saveSettings accepts normalize_on_channel_create field', async () => {
      let requestBody: Record<string, unknown> | null = null;
      server.use(
        http.post('/api/settings', async ({ request }) => {
          requestBody = await request.json() as Record<string, unknown>;
          return HttpResponse.json({
            status: 'ok',
            configured: true,
          });
        })
      );

      await saveSettings({
        url: 'http://localhost:8090',
        username: 'admin',
        normalize_on_channel_create: true,
      });

      expect(requestBody?.normalize_on_channel_create).toBe(true);
    });
  });

  describe('createChannel with normalize flag', () => {
    it('passes normalize flag to API', async () => {
      let requestBody: Record<string, unknown> | null = null;
      server.use(
        http.post('/api/channels', async ({ request }) => {
          requestBody = await request.json() as Record<string, unknown>;
          return HttpResponse.json({
            id: 1,
            uuid: 'test-uuid',
            name: 'ESPN', // Name after normalization
            channel_number: 100,
            channel_group_id: null,
            streams: [],
          });
        })
      );

      await createChannel({
        name: 'ESPN HD',
        channel_number: 100,
        normalize: true,
      });

      expect(requestBody?.normalize).toBe(true);
    });

    it('works without normalize flag', async () => {
      let requestBody: Record<string, unknown> | null = null;
      server.use(
        http.post('/api/channels', async ({ request }) => {
          requestBody = await request.json() as Record<string, unknown>;
          return HttpResponse.json({
            id: 2,
            uuid: 'test-uuid-2',
            name: 'FOX HD',
            channel_number: 101,
            channel_group_id: null,
            streams: [],
          });
        })
      );

      await createChannel({
        name: 'FOX HD',
        channel_number: 101,
      });

      // normalize should be undefined when not specified
      expect(requestBody?.normalize).toBeUndefined();
    });
  });
});
