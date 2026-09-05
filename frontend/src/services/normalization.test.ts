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
  normalizeStreamNamesWithBackend,
  resolveCreateChannelNames,
  NormalizationIncompleteError,
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

      expect(requestBody!.texts).toEqual(['ESPN HD', 'CNN']);
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
      } as Parameters<typeof saveSettings>[0]);

      expect(requestBody!.normalize_on_channel_create).toBe(true);
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

      expect(requestBody!.normalize).toBe(true);
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
      expect(requestBody!.normalize).toBeUndefined();
    });
  });

  /**
   * bead enhancedchannelmanager-e9e5o.
   *
   * Two different situations put an UNNORMALIZED name on a new channel:
   * the operator turned the Create Channels dialog's "Normalization Rules"
   * toggle off, and the backend normalization call failed. Before this bead
   * the service collapsed both into the same value — an identity map — so
   * neither the caller nor the operator could tell them apart. These tests
   * pin the two outcomes as distinguishable at the service boundary.
   */
  describe('normalizeStreamNamesWithBackend', () => {
    it('returns the backend mapping on success', async () => {
      server.use(
        http.post('/api/normalization/normalize', async () =>
          HttpResponse.json({
            results: [
              { original: 'US: CNN HD', normalized: 'CNN', changed: true },
            ],
          })
        )
      );

      const result = await normalizeStreamNamesWithBackend(['US: CNN HD']);

      expect(result.get('US: CNN HD')).toBe('CNN');
    });

    it('rejects when the backend call fails instead of silently returning the originals', async () => {
      server.use(
        http.post('/api/normalization/normalize', async () =>
          HttpResponse.json({ detail: 'boom' }, { status: 500 })
        )
      );

      await expect(
        normalizeStreamNamesWithBackend(['US: CNN HD'])
      ).rejects.toThrow();
    });

    /**
     * Completeness, at the boundary (bead `enhancedchannelmanager-e9e5o`, fix
     * round 4). A 200 was previously accepted whatever it contained, so a
     * response answering about two of the three names asked about produced a
     * partial map that every caller then interpreted for itself — falling back
     * to the raw provider name for the entries that were not there. That is the
     * same swallowed failure the earlier rounds removed, one layer down.
     */
    it('rejects a 200 that answers about only some of the requested names', async () => {
      server.use(
        http.post('/api/normalization/normalize', async () =>
          HttpResponse.json({
            results: [{ original: 'CNN', normalized: 'CNN' }],
          })
        )
      );

      await expect(
        normalizeStreamNamesWithBackend(['CNN', 'MSNBC'])
      ).rejects.toThrow(NormalizationIncompleteError);
    });

    it('names the missing entries on the rejection', async () => {
      server.use(
        http.post('/api/normalization/normalize', async () =>
          HttpResponse.json({
            results: [{ original: 'CNN', normalized: 'CNN' }],
          })
        )
      );

      await normalizeStreamNamesWithBackend(['CNN', 'MSNBC', 'FOX']).then(
        () => {
          throw new Error('expected a rejection');
        },
        (error: unknown) => {
          expect(error).toBeInstanceOf(NormalizationIncompleteError);
          expect((error as NormalizationIncompleteError).missing).toEqual(['MSNBC', 'FOX']);
        }
      );
    });

    it('rejects a response that answers the same name twice', async () => {
      server.use(
        http.post('/api/normalization/normalize', async () =>
          HttpResponse.json({
            results: [
              { original: 'CNN', normalized: 'CNN' },
              { original: 'CNN', normalized: 'CNN News' },
            ],
          })
        )
      );

      await expect(normalizeStreamNamesWithBackend(['CNN'])).rejects.toThrow(
        NormalizationIncompleteError
      );
    });

    it('rejects a response carrying a name that was never requested', async () => {
      server.use(
        http.post('/api/normalization/normalize', async () =>
          HttpResponse.json({
            results: [
              { original: 'CNN', normalized: 'CNN' },
              { original: 'BBC', normalized: 'BBC One' },
            ],
          })
        )
      );

      await expect(normalizeStreamNamesWithBackend(['CNN'])).rejects.toThrow(
        NormalizationIncompleteError
      );
    });

    it('de-duplicates the request so "one result per name" is a real property', async () => {
      let requested: string[] = [];
      server.use(
        http.post('/api/normalization/normalize', async ({ request }) => {
          const body = (await request.json()) as { texts: string[] };
          requested = body.texts;
          return HttpResponse.json({
            results: body.texts.map((original) => ({
              original,
              normalized: original.replace(/^US:\s*/, ''),
            })),
          });
        })
      );

      const result = await normalizeStreamNamesWithBackend([
        'US: CNN', 'US: CNN', 'US: MSNBC',
      ]);

      // Two providers can carry identically-named streams. Sending the name
      // twice would make the backend answer twice, which the duplicate check
      // above would then reject.
      expect(requested).toEqual(['US: CNN', 'US: MSNBC']);
      expect(result.get('US: CNN')).toBe('CNN');
      expect(result.get('US: MSNBC')).toBe('MSNBC');
    });
  });

  describe('resolveCreateChannelNames', () => {
    it('does not accept a partial mapping response as a partial success', async () => {
      server.use(http.post('/api/normalization/mappings/resolve', () => HttpResponse.json({ results: [
        { original: 'Stars.TV', preferred_name: 'Stars TV' },
      ] })));
      const result = await resolveCreateChannelNames(['Stars.TV', 'TVN'], false);
      expect(result.normalizationFailed).toBe(true);
      expect(result.nameFor('Stars.TV')).toBe('Stars.TV');
      expect(result.isMapped('Stars.TV')).toBe(false);
    });
    it.each([false, true])('uses authoritative mapped spelling and grouping with normalization=%s', async normalize => {
      server.use(http.post('/api/normalization/normalize', async ({ request }) => {
        const { texts } = await request.json() as { texts: string[] };
        expect(texts).toEqual(['Stars TV']);
        return HttpResponse.json({ results: texts.map(original => ({ original, normalized: original.replace(/ HD$/, '') })) });
      }));
      server.use(http.post('/api/normalization/mappings/resolve', () => HttpResponse.json({ results: [
        { original: 'Stars.TV', preferred_name: 'Stars TV HD' },
        { original: 'Stars-TV', preferred_name: 'Stars TV HD' },
        { original: 'Stars TV', preferred_name: null },
      ] })));
      const result = await resolveCreateChannelNames(['Stars.TV', 'Stars-TV', 'Stars TV'], normalize);
      expect(result.nameFor('Stars.TV')).toBe('Stars TV HD');
      expect(result.normalizationFailed).toBe(false);
      expect(result.groupingKeyFor('Stars.TV')).toBe(result.groupingKeyFor('Stars-TV'));
      expect(result.groupingKeyFor('Stars.TV')).not.toBe(result.groupingKeyFor('Stars TV'));
    });
    it('does not run regex normalization and keeps unmapped names when normalization is off', async () => {
      let called = false;
      server.use(
        http.post('/api/normalization/normalize', async () => {
          called = true;
          return HttpResponse.json({
            results: [
              { original: 'US: CNN HD', normalized: 'CNN', changed: true },
            ],
          });
        })
      );

      const result = await resolveCreateChannelNames(['US: CNN HD'], false);

      expect(called).toBe(false);
      expect(result.nameFor('US: CNN HD')).toBe('US: CNN HD');
      expect(result.normalizationFailed).toBe(false);
    });

    it('returns the normalized names when normalization is on', async () => {
      server.use(
        http.post('/api/normalization/normalize', async () =>
          HttpResponse.json({
            results: [
              { original: 'US: CNN HD', normalized: 'CNN', changed: true },
            ],
          })
        )
      );

      const result = await resolveCreateChannelNames(['US: CNN HD'], true);

      expect(result.nameFor('US: CNN HD')).toBe('CNN');
      expect(result.normalizationFailed).toBe(false);
    });

    it('reports the failure while still yielding usable names when normalization was requested and broke', async () => {
      server.use(
        http.post('/api/normalization/normalize', async () =>
          HttpResponse.json({ detail: 'boom' }, { status: 500 })
        )
      );

      const result = await resolveCreateChannelNames(['US: CNN HD'], true);

      expect(result.normalizationFailed).toBe(true);
      expect(result.nameFor('US: CNN HD')).toBe('US: CNN HD');
    });

    it('reports no failure for an empty selection', async () => {
      const result = await resolveCreateChannelNames([], true);

      expect(result.size).toBe(0);
      expect(result.normalizationFailed).toBe(false);
    });

    /**
     * The reviewer's reproduction, at the boundary that owns it (bead
     * `enhancedchannelmanager-e9e5o`, fix round 4). Ask about CNN and MSNBC,
     * receive a 200 carrying only CNN. There is no partial success: the
     * resolution is a FAILURE, it covers both names with their raw values, and
     * no consumer is handed a map with a hole in it to interpret.
     */
    it('treats a response covering only some of the names as a failed resolution', async () => {
      server.use(
        http.post('/api/normalization/normalize', async () =>
          HttpResponse.json({
            results: [{ original: 'CNN', normalized: 'CNN News' }],
          })
        )
      );

      const result = await resolveCreateChannelNames(['CNN', 'MSNBC'], true);

      expect(result.normalizationFailed).toBe(true);
      // Both names answer, and both answer RAW — the half-normalized "CNN
      // News" is not kept, because a resolution is all-or-nothing.
      expect(result.nameFor('CNN')).toBe('CNN');
      expect(result.nameFor('MSNBC')).toBe('MSNBC');
      expect(result.coversAll(['CNN', 'MSNBC'])).toBe(true);
    });

    it('answers for every requested name on every branch', async () => {
      server.use(
        http.post('/api/normalization/normalize', async ({ request }) => {
          const body = (await request.json()) as { texts: string[] };
          return HttpResponse.json({
            results: body.texts.map((original) => ({
              original,
              normalized: original.replace(/^US:\s*/, ''),
            })),
          });
        })
      );

      for (const normalize of [true, false]) {
        const result = await resolveCreateChannelNames(['US: CNN', 'US: MSNBC'], normalize);
        expect(result.coversAll(['US: CNN', 'US: MSNBC'])).toBe(true);
      }
    });

    it('refuses to answer for a name it was never asked about', async () => {
      const result = await resolveCreateChannelNames(['CNN'], false);

      expect(result.has('MSNBC')).toBe(false);
      expect(result.coversAll(['CNN', 'MSNBC'])).toBe(false);
      // No defaulting overload: the raw-name fallback IS the defect, so a
      // caller that can be asking about an unresolved name has to find out.
      expect(() => result.nameFor('MSNBC')).toThrow(/No resolved channel name/);
    });
  });
});
