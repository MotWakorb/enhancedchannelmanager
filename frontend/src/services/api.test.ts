/**
 * Unit tests for API service.
 */
import { describe, it, expect, beforeEach, afterEach, beforeAll, afterAll, vi } from 'vitest';
import { server } from '../test/mocks/server';
import { http, HttpResponse } from 'msw';
import {
  getChannels,
  updateChannel,
  addStreamToChannel,
  removeStreamFromChannel,
  reorderChannelStreams,
  deleteChannel,
  bulkCommit,
  applyGuideMigration,
  getChannelMergeCandidates,
  getPendingMergesSnapshot,
  // Compute sort
  computeSort,
  // Stale streams (enhancedchannelmanager-n4g7g)
  getStaleStreams,
  // Provider-stale stream ids (enhancedchannelmanager-po78p / GH #696)
  getStaleStreamIds,
  // Enhanced stats (v0.11.0)
  getBandwidthStats,
  getUniqueViewersSummary,
  getChannelBandwidthStats,
  getUniqueViewersByChannel,
  getPopularityRankings,
  getTrendingChannels,
  calculatePopularity,
  // Dummy EPG preview
  previewDummyEPG,
  // Alert methods
  listAlertMethods,
  createAlertMethod,
  updateAlertMethod,
  // Logos (bd-nh50y debug-logging contract)
  getAllLogos,
  // Logos sort/filter query params (bead enhancedchannelmanager-09x38.13)
  getLogos,
  // Integration test-connection wire-format guards (bd-8zi93)
  testEmbyConnection,
  testPlexConnection,
  testJellyfinConnection,
} from './api';
import * as api from './api';
import { logger } from '../utils/logger';

// Start/stop the mock server for these tests
beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('API Service', () => {
  describe('getChannels', () => {
    it('fetches channels successfully', async () => {
      const result = await getChannels();

      expect(result).toHaveProperty('results');
      expect(result).toHaveProperty('count');
      expect(result).toHaveProperty('page');
    });

    it('passes pagination parameters', async () => {
      let requestUrl = '';
      server.use(
        http.get('/api/channels', ({ request }) => {
          requestUrl = request.url;
          return HttpResponse.json({
            results: [],
            count: 0,
            page: 2,
            page_size: 10,
            total_pages: 1,
          });
        })
      );

      await getChannels({ page: 2, pageSize: 10 });

      expect(requestUrl).toContain('page=2');
      expect(requestUrl).toContain('page_size=10');
    });

    it('passes search parameter', async () => {
      let requestUrl = '';
      server.use(
        http.get('/api/channels', ({ request }) => {
          requestUrl = request.url;
          return HttpResponse.json({
            results: [],
            count: 0,
            page: 1,
            page_size: 50,
            total_pages: 1,
          });
        })
      );

      await getChannels({ search: 'ESPN' });

      expect(requestUrl).toContain('search=ESPN');
    });

    it('handles network errors', async () => {
      server.use(
        http.get('/api/channels', () => {
          return HttpResponse.error();
        })
      );

      await expect(getChannels()).rejects.toThrow();
    });
  });

  describe('updateChannel', () => {
    it('updates channel name', async () => {
      server.use(
        http.patch('/api/channels/1', async ({ request }) => {
          const body = await request.json() as Record<string, unknown>;
          return HttpResponse.json({
            id: 1,
            uuid: 'uuid-1',
            name: body.name,
            channel_number: 100,
            channel_group_id: null,
            streams: [],
          });
        })
      );

      const result = await updateChannel(1, { name: 'New Name' });

      expect(result.name).toBe('New Name');
    });

    it('updates channel number', async () => {
      server.use(
        http.patch('/api/channels/1', async ({ request }) => {
          const body = await request.json() as Record<string, unknown>;
          return HttpResponse.json({
            id: 1,
            uuid: 'uuid-1',
            name: 'Test Channel',
            channel_number: body.channel_number,
            channel_group_id: null,
            streams: [],
          });
        })
      );

      const result = await updateChannel(1, { channel_number: 200 });

      expect(result.channel_number).toBe(200);
    });
  });

  describe('getChannelMergeCandidates (BD-D / ADR-008 §D1)', () => {
    it('returns the BD-D response envelope verbatim and forwards stream_name + group_id query params', async () => {
      let requestUrl = '';
      server.use(
        http.get('/api/channel-merges/candidates', ({ request }) => {
          requestUrl = request.url;
          return HttpResponse.json({
            stream_name: 'ESPN HD',
            candidates: [
              { channel_id: 'channel-uuid-abc', channel_name: 'ESPN HD', confidence: 0.92 },
            ],
            total: 1,
            page: 1,
            page_size: 50,
            total_pages: 1,
          });
        })
      );

      const result = await getChannelMergeCandidates('ESPN HD', 7);

      expect(requestUrl).toContain('stream_name=ESPN+HD');
      expect(requestUrl).toContain('group_id=7');
      expect(result.candidates).toHaveLength(1);
      expect(result.candidates[0].channel_id).toBe('channel-uuid-abc');
      expect(result.candidates[0].confidence).toBe(0.92);
    });

    it('omits group_id when null (search all groups)', async () => {
      let requestUrl = '';
      server.use(
        http.get('/api/channel-merges/candidates', ({ request }) => {
          requestUrl = request.url;
          return HttpResponse.json({
            stream_name: 'CNN',
            candidates: [],
            total: 0,
            page: 1,
            page_size: 50,
            total_pages: 0,
          });
        })
      );

      await getChannelMergeCandidates('CNN', null);

      expect(requestUrl).toContain('stream_name=CNN');
      expect(requestUrl).not.toContain('group_id=');
    });
  });

  it('gets the complete pending-merges snapshot from the static snapshot route', async () => {
    server.use(
      http.get('/api/channel-merges/snapshot', () =>
        HttpResponse.json({ merges: [], total: 0 }),
      ),
    );
    await expect(getPendingMergesSnapshot()).resolves.toEqual({
      merges: [],
      total: 0,
    });
  });

  it('scopes a pending-merges snapshot to the requested group', async () => {
    let requestUrl = '';
    server.use(
      http.get('/api/channel-merges/snapshot', ({ request }) => {
        requestUrl = request.url;
        return HttpResponse.json({ merges: [], total: 0 });
      }),
    );

    await getPendingMergesSnapshot({ groupId: 17 });

    expect(requestUrl).toContain('group_id=17');
  });

  describe('addStreamToChannel', () => {
    it('adds stream to channel', async () => {
      server.use(
        http.post('/api/channels/1/add-stream', () => {
          return HttpResponse.json({
            id: 1,
            uuid: 'uuid-1',
            name: 'Test Channel',
            channel_number: 100,
            channel_group_id: null,
            streams: [10, 20],
          });
        })
      );

      const result = await addStreamToChannel(1, 20);

      expect(result.streams).toContain(20);
    });
  });

  describe('removeStreamFromChannel', () => {
    it('removes stream from channel', async () => {
      server.use(
        http.post('/api/channels/1/remove-stream', () => {
          return HttpResponse.json({
            id: 1,
            uuid: 'uuid-1',
            name: 'Test Channel',
            channel_number: 100,
            channel_group_id: null,
            streams: [10],
          });
        })
      );

      const result = await removeStreamFromChannel(1, 20);

      expect(result.streams).not.toContain(20);
    });
  });

  describe('reorderChannelStreams', () => {
    it('reorders streams', async () => {
      server.use(
        http.post('/api/channels/1/reorder-streams', async ({ request }) => {
          const body = await request.json() as { stream_ids: number[] };
          return HttpResponse.json({
            id: 1,
            uuid: 'uuid-1',
            name: 'Test Channel',
            channel_number: 100,
            channel_group_id: null,
            streams: body.stream_ids,
          });
        })
      );

      const result = await reorderChannelStreams(1, [30, 20, 10]);

      expect(result.streams).toEqual([30, 20, 10]);
    });
  });

  describe('deleteChannel', () => {
    it('deletes channel', async () => {
      server.use(
        http.delete('/api/channels/1', () => {
          return HttpResponse.json({});
        })
      );

      await expect(deleteChannel(1)).resolves.not.toThrow();
    });

    it('throws on 404', async () => {
      server.use(
        http.delete('/api/channels/999', () => {
          return HttpResponse.json(
            { detail: 'Channel not found' },
            { status: 404 }
          );
        })
      );

      await expect(deleteChannel(999)).rejects.toThrow('Channel not found');
    });
  });

  describe('bulkCommit (bd-ggxks 202+poll)', () => {
    // Speed-up: the api client sleeps 750ms between polls. Stubbing
    // setTimeout makes each await synchronous so the tests still cover the
    // running→completed transition without taking seconds per case.
    beforeEach(() => {
      vi.stubGlobal(
        'setTimeout',
        ((fn: () => void) => {
          fn();
          return 0;
        }) as unknown as typeof setTimeout
      );
    });

    afterEach(() => {
      vi.unstubAllGlobals();
    });

    it('validateOnly path: synchronous POST returns the response body untouched', async () => {
      let getCallCount = 0;
      server.use(
        http.post('/api/channels/bulk-commit', async ({ request }) => {
          const body = (await request.json()) as { validateOnly?: boolean };
          expect(body.validateOnly).toBe(true);
          return HttpResponse.json(
            {
              success: true,
              operationsApplied: 0,
              operationsFailed: 0,
              errors: [],
              tempIdMap: {},
              groupIdMap: {},
              validationPassed: true,
              validationIssues: [],
            },
            { status: 200 }
          );
        }),
        http.get('/api/channels/bulk-commit/:jobId', () => {
          getCallCount++;
          return HttpResponse.json({ status: 'running' });
        })
      );

      const result = await bulkCommit({ operations: [], validateOnly: true });

      expect(result.success).toBe(true);
      expect(result.validationPassed).toBe(true);
      // Sync path must never poll — that's the whole point of keeping it sync.
      expect(getCallCount).toBe(0);
    });

    it('async path: 202 enqueue then polls GET until completed, returns result', async () => {
      let postCallCount = 0;
      const pollResponses = [
        { status: 'running' },
        { status: 'running' },
        {
          status: 'completed',
          result: {
            success: true,
            operationsApplied: 3,
            operationsFailed: 0,
            errors: [],
            tempIdMap: { '-1': 100 },
            groupIdMap: {},
          },
        },
      ];
      let pollIdx = 0;

      server.use(
        http.post('/api/channels/bulk-commit', () => {
          postCallCount++;
          return HttpResponse.json(
            { job_id: 'abc123', status: 'running' },
            { status: 202 }
          );
        }),
        http.get('/api/channels/bulk-commit/:jobId', ({ params }) => {
          expect(params.jobId).toBe('abc123');
          const body = pollResponses[Math.min(pollIdx, pollResponses.length - 1)];
          pollIdx += 1;
          return HttpResponse.json({ job_id: 'abc123', ...body });
        })
      );

      const result = await bulkCommit({
        operations: [
          {
            type: 'createChannel',
            tempId: -1,
            name: 'SiriusXM Hits 1',
          },
        ],
      });

      expect(postCallCount).toBe(1);
      // Loop ran until "completed" — three polls drained from the array.
      expect(pollIdx).toBe(3);
      expect(result.success).toBe(true);
      expect(result.operationsApplied).toBe(3);
      expect(result.tempIdMap).toEqual({ '-1': 100 });
    });

    it('async path: failed status raises with the backend error message', async () => {
      server.use(
        http.post('/api/channels/bulk-commit', () =>
          HttpResponse.json({ job_id: 'fail', status: 'running' }, { status: 202 })
        ),
        http.get('/api/channels/bulk-commit/:jobId', () =>
          HttpResponse.json({
            job_id: 'fail',
            status: 'failed',
            error: 'dispatcharr unreachable',
          })
        )
      );

      await expect(
        bulkCommit({
          operations: [{ type: 'createChannel', tempId: -1, name: 'X' }],
        })
      ).rejects.toThrow(/dispatcharr unreachable/);
    });
  });

  describe('applyGuideMigration (202+poll)', () => {
    const oneReadyPreview = {
      target_source_id: 2,
      target_source_name: 'SD',
      preview_token: 'signed',
      counts: {
        ready: 1,
        already_target: 0,
        unassigned: 0,
        missing_lcn: 0,
        missing_target: 0,
        ambiguous_target: 0,
        unsupported_origin: 0,
      },
      rows: [
        {
          channel_id: 7,
          channel_name: 'A',
          current_epg_data_id: 11,
          current_source_id: 1,
          current_source_name: 'XML',
          lcn: '1',
          target_epg_data_id: 21,
          target_name: 'A',
          current_tvg_id: 'a',
          target_tvg_id: '1',
          status: 'ready' as const,
        },
      ],
    };

    beforeEach(() => {
      vi.stubGlobal(
        'setTimeout',
        ((fn: () => void) => {
          fn();
          return 0;
        }) as unknown as typeof setTimeout
      );
    });

    afterEach(() => {
      vi.unstubAllGlobals();
      vi.restoreAllMocks();
    });

    it('reports partial progress and returns the terminal result', async () => {
      let poll = 0;
      const result = {
        mutated: 2,
        updated: 1,
        audit_failed: 0,
        skipped: 0,
        failed: 1,
        results: [
          { channel_id: 7, status: 'updated' as const },
          { channel_id: 8, status: 'failed' as const },
        ],
        batch_id: '0123456789abcdef0123456789abcdef',
      };
      server.use(
        http.post('/api/epg/migration/apply', () =>
          HttpResponse.json(
            { batch_id: result.batch_id, status: 'running' },
            { status: 202 }
          )
        ),
        http.get('/api/epg/migration/apply/:batchId', () => {
          poll += 1;
          if (poll === 1) {
            return HttpResponse.json({
              batch_id: result.batch_id,
              status: 'running',
              processed: 1,
              total: 2,
              result: { ...result, results: result.results.slice(0, 1), failed: 0 },
            });
          }
          return HttpResponse.json({
            batch_id: result.batch_id,
            status: 'completed',
            processed: 2,
            total: 2,
            result,
          });
        })
      );
      const progress = vi.fn();
      const resolved = await applyGuideMigration(
        {
          target_source_id: 2,
          target_source_name: 'SD',
          preview_token: 'signed',
          counts: {
            ready: 2,
            already_target: 0,
            unassigned: 0,
            missing_lcn: 0,
            missing_target: 0,
            ambiguous_target: 0,
            unsupported_origin: 0,
          },
          rows: [
            {
              channel_id: 7,
              channel_name: 'A',
              current_epg_data_id: 11,
              current_source_id: 1,
              current_source_name: 'XML',
              lcn: '1',
              target_epg_data_id: 21,
              target_name: 'A',
              current_tvg_id: 'a',
              target_tvg_id: '1',
              status: 'ready',
            },
            {
              channel_id: 8,
              channel_name: 'B',
              current_epg_data_id: 12,
              current_source_id: 1,
              current_source_name: 'XML',
              lcn: '2',
              target_epg_data_id: 22,
              target_name: 'B',
              current_tvg_id: 'b',
              target_tvg_id: '2',
              status: 'ready',
            },
          ],
        },
        progress
      );
      expect(progress).toHaveBeenCalledTimes(3);
      expect(progress.mock.calls[0][0].processed).toBe(0);
      expect(progress.mock.calls[1][0].processed).toBe(1);
      expect(resolved).toEqual(result);
    });

    it('continues beyond the former one-hour wall-clock cutoff', async () => {
      const batchId = '11111111111111111111111111111111';
      let polls = 0;
      vi.spyOn(Date, 'now').mockReturnValueOnce(0).mockReturnValue(7_200_000);
      server.use(
        http.post('/api/epg/migration/apply', () =>
          HttpResponse.json({ batch_id: batchId, status: 'running' }, { status: 202 })
        ),
        http.get('/api/epg/migration/apply/:batchId', () => {
          polls += 1;
          const result = {
            mutated: 1,
            updated: 1,
            audit_failed: 0,
            skipped: 0,
            failed: 0,
            results: [{ channel_id: 7, status: 'updated' as const }],
            batch_id: batchId,
          };
          return HttpResponse.json({
            batch_id: batchId,
            status: polls < 3 ? 'running' : 'completed',
            processed: polls < 3 ? 0 : 1,
            total: 1,
            result,
          });
        })
      );
      const result = await applyGuideMigration(oneReadyPreview);
      expect(polls).toBe(3);
      expect(result.updated).toBe(1);
    });

    it('surfaces terminal failure with the known batch and reconciliation guidance', async () => {
      const batchId = '22222222222222222222222222222222';
      server.use(
        http.post('/api/epg/migration/apply', () =>
          HttpResponse.json({ batch_id: batchId, status: 'running' }, { status: 202 })
        ),
        http.get('/api/epg/migration/apply/:batchId', () =>
          HttpResponse.json({
            batch_id: batchId,
            status: 'failed',
            processed: 0,
            total: 1,
            result: {
              mutated: 0,
              updated: 0,
              audit_failed: 0,
              skipped: 0,
              failed: 0,
              results: [],
              batch_id: batchId,
            },
            error: 'Guide migration failed.',
          })
        )
      );
      await expect(applyGuideMigration(oneReadyPreview)).rejects.toThrow(
        new RegExp(`${batchId}.*fresh preview.*Dispatcharr`, 'i')
      );
    });

    it('retains the batch and partial progress across a transient poll error', async () => {
      const batchId = '33333333333333333333333333333333';
      let polls = 0;
      const progress = vi.fn();
      server.use(
        http.post('/api/epg/migration/apply', () =>
          HttpResponse.json({ batch_id: batchId, status: 'running' }, { status: 202 })
        ),
        http.get('/api/epg/migration/apply/:batchId', () => {
          polls += 1;
          if (polls === 2) {
            return HttpResponse.json({ detail: 'temporary outage' }, { status: 503 });
          }
          const completed = polls === 3;
          return HttpResponse.json({
            batch_id: batchId,
            status: completed ? 'completed' : 'running',
            processed: completed ? 1 : 0,
            total: 1,
            result: {
              mutated: completed ? 1 : 0,
              updated: completed ? 1 : 0,
              audit_failed: 0,
              skipped: 0,
              failed: 0,
              results: completed ? [{ channel_id: 7, status: 'updated' }] : [],
              batch_id: batchId,
            },
          });
        })
      );
      const result = await applyGuideMigration(oneReadyPreview, progress);
      expect(result.batch_id).toBe(batchId);
      expect(progress.mock.calls.some(([value]) => value.batch_id === batchId)).toBe(true);
      expect(polls).toBe(3);
    });

    it('turns restart 404 into actionable guidance without losing the batch', async () => {
      const batchId = '44444444444444444444444444444444';
      const progress = vi.fn();
      server.use(
        http.post('/api/epg/migration/apply', () =>
          HttpResponse.json({ batch_id: batchId, status: 'running' }, { status: 202 })
        ),
        http.get('/api/epg/migration/apply/:batchId', () =>
          HttpResponse.json({ detail: 'Guide migration job not found.' }, { status: 404 })
        )
      );
      await expect(applyGuideMigration(oneReadyPreview, progress)).rejects.toThrow(
        new RegExp(`${batchId}.*restarted.*Dispatcharr`, 'i')
      );
      expect(progress).toHaveBeenCalledWith(
        expect.objectContaining({ batch_id: batchId, processed: 0 })
      );
    });

    it('surfaces a non-transient poll error with the accepted batch', async () => {
      const batchId = '66666666666666666666666666666666';
      server.use(
        http.post('/api/epg/migration/apply', () =>
          HttpResponse.json({ batch_id: batchId, status: 'running' }, { status: 202 })
        ),
        http.get('/api/epg/migration/apply/:batchId', () =>
          HttpResponse.json({ detail: 'Permission denied' }, { status: 403 })
        )
      );
      await expect(applyGuideMigration(oneReadyPreview)).rejects.toThrow(
        new RegExp(`${batchId}.*Permission denied.*Dispatcharr`, 'i')
      );
    });

    it('aborts accepted polling without waiting for a timer', async () => {
      const batchId = '55555555555555555555555555555555';
      const controller = new AbortController();
      server.use(
        http.post('/api/epg/migration/apply', () =>
          HttpResponse.json({ batch_id: batchId, status: 'running' }, { status: 202 })
        )
      );
      await expect(
        applyGuideMigration(
          oneReadyPreview,
          () => controller.abort(),
          controller.signal
        )
      ).rejects.toMatchObject({ name: 'AbortError' });
    });
  });

  describe('error handling', () => {
    it('extracts error detail from response', async () => {
      server.use(
        http.get('/api/channels', () => {
          return HttpResponse.json(
            { detail: 'Custom error message' },
            { status: 500 }
          );
        })
      );

      await expect(getChannels()).rejects.toThrow('Custom error message');
    });

    it('falls back to status text when no detail', async () => {
      server.use(
        http.get('/api/channels', () => {
          return new HttpResponse('Server Error', {
            status: 500,
            statusText: 'Internal Server Error',
          });
        })
      );

      await expect(getChannels()).rejects.toThrow();
    });
  });

  // ===========================================================================
  // Alert Methods API Tests
  // ===========================================================================

  describe('alert methods', () => {
    it('lists alert methods', async () => {
      server.use(
        http.get('/api/alert-methods', () => {
          return HttpResponse.json([
            {
              id: 1,
              name: 'Email',
              method_type: 'smtp',
              enabled: true,
              config: { to_emails: 'a@example.com' },
              notify_info: false,
              notify_success: true,
              notify_warning: true,
              notify_error: true,
            },
          ]);
        }),
      );

      const result = await listAlertMethods();
      expect(Array.isArray(result)).toBe(true);
      expect(result[0]?.method_type).toBe('smtp');
    });

    it('creates an alert method', async () => {
      let requestBody: unknown;
      server.use(
        http.post('/api/alert-methods', async ({ request }) => {
          requestBody = await request.json();
          return HttpResponse.json(
            { id: 10, name: 'Email', method_type: 'smtp', enabled: true },
            { status: 201 },
          );
        }),
      );

      const created = await createAlertMethod({
        name: 'Email',
        method_type: 'smtp',
        enabled: true,
        config: { to_emails: 'a@example.com' },
        notify_info: false,
        notify_success: true,
        notify_warning: true,
        notify_error: true,
      });

      expect(requestBody).toMatchObject({
        name: 'Email',
        method_type: 'smtp',
        enabled: true,
      });
      expect(created.id).toBe(10);
    });

    it('updates an alert method', async () => {
      let requestBody: unknown;
      server.use(
        http.patch('/api/alert-methods/10', async ({ request }) => {
          requestBody = await request.json();
          return HttpResponse.json({ success: true });
        }),
      );

      const result = await updateAlertMethod(10, { enabled: true, config: { to_emails: 'b@example.com' } });
      expect(result.success).toBe(true);
      expect(requestBody).toMatchObject({ enabled: true });
    });
  });

  // ===========================================================================
  // Enhanced Stats API Tests (v0.11.0)
  // ===========================================================================

  describe('getBandwidthStats', () => {
    it('fetches bandwidth summary', async () => {
      const result = await getBandwidthStats();

      expect(result).toHaveProperty('today');
      expect(result).toHaveProperty('this_week');
      expect(result).toHaveProperty('this_month');
      expect(result).toHaveProperty('this_year');
      expect(result.today).toBe(1000000000);
    });

    it('handles errors', async () => {
      server.use(
        http.get('/api/stats/bandwidth', () => {
          return HttpResponse.json(
            { detail: 'Stats unavailable' },
            { status: 500 }
          );
        })
      );

      await expect(getBandwidthStats()).rejects.toThrow('Stats unavailable');
    });
  });

  describe('getUniqueViewersSummary', () => {
    it('fetches unique viewers with default days', async () => {
      const result = await getUniqueViewersSummary();

      expect(result.period_days).toBe(7);
      expect(result.total_unique_viewers).toBe(150);
      expect(result.today_unique_viewers).toBe(25);
      expect(result.total_connections).toBe(500);
    });

    it('passes custom days parameter', async () => {
      let requestUrl = '';
      server.use(
        http.get('/api/stats/unique-viewers', ({ request }) => {
          requestUrl = request.url;
          return HttpResponse.json({
            period_days: 30,
            total_unique_viewers: 500,
            today_unique_viewers: 20,
            total_connections: 2000,
            avg_watch_seconds: 2400,
            top_viewer_ip: '192.168.1.1',
          });
        })
      );

      await getUniqueViewersSummary(30);

      expect(requestUrl).toContain('days=30');
    });
  });

  describe('getChannelBandwidthStats', () => {
    it('fetches per-channel bandwidth stats', async () => {
      server.use(
        http.get('/api/stats/channel-bandwidth', () => {
          return HttpResponse.json([
            {
              channel_id: 'uuid-1',
              channel_name: 'Channel 1',
              total_bytes: 1000000000,
              total_connections: 100,
              total_watch_seconds: 36000,
              avg_bytes_per_connection: 10000000,
            },
          ]);
        })
      );

      const result = await getChannelBandwidthStats();

      expect(Array.isArray(result)).toBe(true);
      expect(result[0]).toHaveProperty('channel_id');
      expect(result[0]).toHaveProperty('total_bytes');
    });

    it('passes parameters correctly', async () => {
      let requestUrl = '';
      server.use(
        http.get('/api/stats/channel-bandwidth', ({ request }) => {
          requestUrl = request.url;
          return HttpResponse.json([]);
        })
      );

      await getChannelBandwidthStats(14, 50, 'connections');

      expect(requestUrl).toContain('days=14');
      expect(requestUrl).toContain('limit=50');
      expect(requestUrl).toContain('sort_by=connections');
    });
  });

  describe('getUniqueViewersByChannel', () => {
    it('fetches unique viewers by channel', async () => {
      server.use(
        http.get('/api/stats/unique-viewers-by-channel', () => {
          return HttpResponse.json([
            {
              channel_id: 'uuid-1',
              channel_name: 'Channel 1',
              unique_viewers: 50,
              total_connections: 100,
              total_watch_seconds: 36000,
            },
          ]);
        })
      );

      const result = await getUniqueViewersByChannel();

      expect(Array.isArray(result)).toBe(true);
      expect(result[0]).toHaveProperty('unique_viewers');
    });
  });

  describe('getPopularityRankings', () => {
    it('fetches popularity rankings', async () => {
      server.use(
        http.get('/api/stats/popularity/rankings', () => {
          return HttpResponse.json({
            total: 2,
            rankings: [
              {
                id: 1,
                channel_id: 'uuid-1',
                channel_name: 'Top Channel',
                score: 95.5,
                rank: 1,
                trend: 'up',
                trend_percent: 15.2,
                calculated_at: new Date().toISOString(),
                created_at: new Date().toISOString(),
              },
              {
                id: 2,
                channel_id: 'uuid-2',
                channel_name: 'Second Channel',
                score: 85.0,
                rank: 2,
                trend: 'stable',
                trend_percent: 0.5,
                calculated_at: new Date().toISOString(),
                created_at: new Date().toISOString(),
              },
            ],
          });
        })
      );

      const result = await getPopularityRankings();

      expect(result.total).toBe(2);
      expect(result.rankings).toHaveLength(2);
      expect(result.rankings[0].rank).toBe(1);
      expect(result.rankings[0].score).toBe(95.5);
    });

    it('passes pagination parameters', async () => {
      let requestUrl = '';
      server.use(
        http.get('/api/stats/popularity/rankings', ({ request }) => {
          requestUrl = request.url;
          return HttpResponse.json({ total: 0, rankings: [] });
        })
      );

      await getPopularityRankings(25, 50);

      expect(requestUrl).toContain('limit=25');
      expect(requestUrl).toContain('offset=50');
    });
  });

  describe('getTrendingChannels', () => {
    it('fetches trending up channels by default', async () => {
      server.use(
        http.get('/api/stats/popularity/trending', ({ request }) => {
          const url = new URL(request.url);
          const direction = url.searchParams.get('direction');
          return HttpResponse.json([
            {
              id: 1,
              channel_id: 'uuid-1',
              channel_name: 'Rising Star',
              score: 80.0,
              rank: 3,
              trend: direction,
              trend_percent: 25.0,
              calculated_at: new Date().toISOString(),
              created_at: new Date().toISOString(),
            },
          ]);
        })
      );

      const result = await getTrendingChannels('up');

      expect(Array.isArray(result)).toBe(true);
      expect(result[0].trend).toBe('up');
    });

    it('fetches trending down channels', async () => {
      let requestUrl = '';
      server.use(
        http.get('/api/stats/popularity/trending', ({ request }) => {
          requestUrl = request.url;
          return HttpResponse.json([]);
        })
      );

      await getTrendingChannels('down', 5);

      expect(requestUrl).toContain('direction=down');
      expect(requestUrl).toContain('limit=5');
    });
  });

  describe('calculatePopularity', () => {
    it('triggers popularity calculation', async () => {
      server.use(
        http.post('/api/stats/popularity/calculate', () => {
          return HttpResponse.json({
            channels_scored: 50,
            channels_updated: 35,
            channels_created: 15,
            top_channels: [
              { channel_id: 'uuid-1', channel_name: 'Top 1', score: 98.0 },
            ],
          });
        })
      );

      const result = await calculatePopularity();

      expect(result.channels_scored).toBe(50);
      expect(result.channels_updated).toBe(35);
      expect(result.channels_created).toBe(15);
      expect(result.top_channels).toHaveLength(1);
    });

    it('passes period_days parameter', async () => {
      let requestUrl = '';
      server.use(
        http.post('/api/stats/popularity/calculate', ({ request }) => {
          requestUrl = request.url;
          return HttpResponse.json({
            channels_scored: 0,
            channels_updated: 0,
            channels_created: 0,
            top_channels: [],
          });
        })
      );

      await calculatePopularity(30);

      expect(requestUrl).toContain('period_days=30');
    });

    it('handles calculation errors', async () => {
      server.use(
        http.post('/api/stats/popularity/calculate', () => {
          return HttpResponse.json(
            { detail: 'Calculation failed' },
            { status: 500 }
          );
        })
      );

      await expect(calculatePopularity()).rejects.toThrow('Calculation failed');
    });
  });

  // ===========================================================================
  // CSV Import/Export API Tests (v0.11.1)
  // ===========================================================================

  describe('exportChannelsToCSV', () => {
    it('exports channels as CSV blob', async () => {
      server.use(
        http.get('/api/channels/export-csv', () => {
          return new HttpResponse(
            'channel_number,name,group_name\n101,ESPN HD,Sports',
            {
              headers: {
                'Content-Type': 'text/csv',
                'Content-Disposition': 'attachment; filename=channels.csv',
              },
            }
          );
        })
      );

      const { exportChannelsToCSV } = await import('./api');
      const result = await exportChannelsToCSV();

      // Check for Blob-like properties (instanceof fails across environments)
      expect(result).toBeDefined();
      expect(result.type).toBe('text/csv');
      expect(typeof result.size).toBe('number');
      expect(result.size).toBeGreaterThan(0);
    });

    it('handles export errors', async () => {
      server.use(
        http.get('/api/channels/export-csv', () => {
          return HttpResponse.json(
            { detail: 'Export failed' },
            { status: 500 }
          );
        })
      );

      const { exportChannelsToCSV } = await import('./api');
      await expect(exportChannelsToCSV()).rejects.toThrow();
    });
  });

  describe('downloadCSVTemplate', () => {
    it('downloads CSV template as blob', async () => {
      server.use(
        http.get('/api/channels/csv-template', () => {
          return new HttpResponse(
            '# Template\nchannel_number,name,group_name',
            {
              headers: {
                'Content-Type': 'text/csv',
                'Content-Disposition': 'attachment; filename=template.csv',
              },
            }
          );
        })
      );

      const { downloadCSVTemplate } = await import('./api');
      const result = await downloadCSVTemplate();

      // Check for Blob-like properties (instanceof fails across environments)
      expect(result).toBeDefined();
      expect(result.type).toBe('text/csv');
      expect(typeof result.size).toBe('number');
      expect(result.size).toBeGreaterThan(0);
    });
  });

  describe('importChannelsFromCSV', () => {
    it('imports channels from CSV file', async () => {
      server.use(
        http.post('/api/channels/import-csv', () => {
          return HttpResponse.json({
            success: true,
            channels_created: 3,
            groups_created: 1,
            errors: [],
            warnings: [],
          });
        })
      );

      const { importChannelsFromCSV } = await import('./api');
      const csvFile = new File(
        ['channel_number,name\n101,ESPN HD'],
        'test.csv',
        { type: 'text/csv' }
      );
      const result = await importChannelsFromCSV(csvFile);

      expect(result.success).toBe(true);
      expect(result.channels_created).toBe(3);
      expect(result.groups_created).toBe(1);
      expect(result.errors).toHaveLength(0);
    });

    it('returns validation errors', async () => {
      server.use(
        http.post('/api/channels/import-csv', () => {
          return HttpResponse.json({
            success: false,
            channels_created: 0,
            groups_created: 0,
            errors: [{ row: 2, error: 'Missing required field: name' }],
            warnings: [],
          });
        })
      );

      const { importChannelsFromCSV } = await import('./api');
      const csvFile = new File(
        ['channel_number,name\n101,'],
        'test.csv',
        { type: 'text/csv' }
      );
      const result = await importChannelsFromCSV(csvFile);

      expect(result.success).toBe(false);
      expect(result.errors).toHaveLength(1);
      expect(result.errors[0].row).toBe(2);
    });

    it('handles import errors', async () => {
      server.use(
        http.post('/api/channels/import-csv', () => {
          return HttpResponse.json(
            { detail: 'Missing required column: name' },
            { status: 400 }
          );
        })
      );

      const { importChannelsFromCSV } = await import('./api');
      const csvFile = new File(
        ['channel_number\n101'],
        'test.csv',
        { type: 'text/csv' }
      );

      await expect(importChannelsFromCSV(csvFile)).rejects.toThrow('Missing required column: name');
    });
  });

  describe('parseCSVPreview', () => {
    it('parses CSV content for preview', async () => {
      server.use(
        http.post('/api/channels/preview-csv', () => {
          return HttpResponse.json({
            rows: [
              { channel_number: '101', name: 'ESPN HD', group_name: 'Sports' },
              { channel_number: '102', name: 'CNN', group_name: 'News' },
            ],
            errors: [],
          });
        })
      );

      const { parseCSVPreview } = await import('./api');
      const result = await parseCSVPreview('channel_number,name,group_name\n101,ESPN HD,Sports\n102,CNN,News');

      expect(result.rows).toHaveLength(2);
      expect(result.rows[0].name).toBe('ESPN HD');
      expect(result.errors).toHaveLength(0);
    });

    it('returns validation errors in preview', async () => {
      server.use(
        http.post('/api/channels/preview-csv', () => {
          return HttpResponse.json({
            rows: [],
            errors: [{ row: 2, error: 'Missing required field: name' }],
          });
        })
      );

      const { parseCSVPreview } = await import('./api');
      const result = await parseCSVPreview('channel_number,name\n101,');

      expect(result.errors).toHaveLength(1);
    });
  });

  describe('computeSort', () => {
    it('sends correct payload and returns results', async () => {
      const channels = [
        { channel_id: 10, stream_ids: [1, 2, 3] },
        { channel_id: 20, stream_ids: [4, 5] },
      ];

      const result = await computeSort(channels, 'smart');

      expect(result).toHaveProperty('results');
      expect(result.results).toHaveLength(2);
      expect(result.results[0].channel_id).toBe(10);
      expect(result.results[0]).toHaveProperty('sorted_stream_ids');
      expect(result.results[0]).toHaveProperty('changed');
    });

    it('defaults mode to smart', async () => {
      let requestBody: { mode?: string } | undefined;
      server.use(
        http.post('/api/stream-stats/compute-sort', async ({ request }) => {
          requestBody = await request.json() as { mode?: string };
          return HttpResponse.json({ results: [] });
        })
      );

      await computeSort([{ channel_id: 1, stream_ids: [1] }]);

      expect(requestBody?.mode).toBe('smart');
    });

    it('handles network error', async () => {
      server.use(
        http.post('/api/stream-stats/compute-sort', () => {
          return HttpResponse.error();
        })
      );

      await expect(computeSort([{ channel_id: 1, stream_ids: [1] }])).rejects.toThrow();
    });
  });

  describe('getStaleStreams', () => {
    it('defaults to a 7-day threshold and returns the parsed response', async () => {
      let requestUrl: URL | undefined;
      server.use(
        http.get('/api/stream-stats/stale', ({ request }) => {
          requestUrl = new URL(request.url);
          return HttpResponse.json({
            streams: [
              {
                stream_id: 10,
                stream_name: 'ESPN Feed',
                last_probed: null,
                provider_last_seen: null,
                reasons: ['not_probed_recently'],
                channels: [],
              },
            ],
            threshold_days: 7,
          });
        })
      );

      const result = await getStaleStreams();

      expect(requestUrl?.searchParams.get('days')).toBe('7');
      expect(result.threshold_days).toBe(7);
      expect(result.streams).toHaveLength(1);
      expect(result.streams[0].reasons).toEqual(['not_probed_recently']);
    });

    it('passes a custom days threshold through as a query param', async () => {
      let requestUrl: URL | undefined;
      server.use(
        http.get('/api/stream-stats/stale', ({ request }) => {
          requestUrl = new URL(request.url);
          return HttpResponse.json({ streams: [], threshold_days: 14 });
        })
      );

      await getStaleStreams(14);

      expect(requestUrl?.searchParams.get('days')).toBe('14');
    });

    it('handles network error', async () => {
      server.use(
        http.get('/api/stream-stats/stale', () => {
          return HttpResponse.error();
        })
      );

      await expect(getStaleStreams()).rejects.toThrow();
    });
  });

  describe('getStaleStreamIds', () => {
    it('fetches without bypass_cache by default', async () => {
      let requestUrl: URL | undefined;
      server.use(
        http.get('/api/streams/stale-ids', ({ request }) => {
          requestUrl = new URL(request.url);
          return HttpResponse.json({
            stale_stream_ids: [1, 3],
            last_seen: { '1': '2026-07-01T00:00:00Z', '3': null },
            count: 2,
          });
        })
      );

      const result = await getStaleStreamIds();

      expect(requestUrl?.searchParams.has('bypass_cache')).toBe(false);
      expect(result.stale_stream_ids).toEqual([1, 3]);
      expect(result.count).toBe(2);
      expect(result.last_seen['1']).toBe('2026-07-01T00:00:00Z');
    });

    it('passes bypass_cache=true when requested', async () => {
      let requestUrl: URL | undefined;
      server.use(
        http.get('/api/streams/stale-ids', ({ request }) => {
          requestUrl = new URL(request.url);
          return HttpResponse.json({ stale_stream_ids: [], last_seen: {}, count: 0 });
        })
      );

      await getStaleStreamIds(true);

      expect(requestUrl?.searchParams.get('bypass_cache')).toBe('true');
    });

    it('handles network error', async () => {
      server.use(
        http.get('/api/streams/stale-ids', () => {
          return HttpResponse.error();
        })
      );

      await expect(getStaleStreamIds()).rejects.toThrow();
    });
  });

  // ===========================================================================
  // Dummy EPG preview (trace surface used by the preview UI)
  // ===========================================================================

  describe('previewDummyEPG', () => {
    it('returns the rendered shape for a basic request', async () => {
      const result = await previewDummyEPG({
        sample_name: 'ESPN',
        title_pattern: '(?P<name>.+)',
        title_template: '{name|uppercase}',
      });
      expect(result.rendered.title).toBe('{name|uppercase}');
      expect(result.matched).toBe(true);
      // No include_trace flag → traces should be absent.
      expect(result.traces).toBeUndefined();
    });

    it('carries include_trace through to the server and exposes traces', async () => {
      let sentBody: { include_trace?: boolean } | undefined;
      server.use(
        http.post('/api/dummy-epg/preview', async ({ request }) => {
          sentBody = (await request.json()) as { include_trace?: boolean };
          return HttpResponse.json({
            original_name: 'x',
            substituted_name: 'x',
            substitution_steps: [],
            matched: true,
            matched_variant: null,
            groups: null,
            time_variables: null,
            rendered: {
              title: 'X',
              description: '',
              upcoming_title: '',
              upcoming_description: '',
              ended_title: '',
              ended_description: '',
              fallback_title: '',
              fallback_description: '',
              channel_logo_url: '',
              program_poster_url: '',
            },
            traces: {
              title_template: [
                {
                  kind: 'placeholder',
                  raw: '{name|uppercase}',
                  group_name: 'name',
                  initial_value: 'x',
                  pipes: [
                    { transform: 'uppercase', arg: null, input: 'x', output: 'X' },
                  ],
                  final_value: 'X',
                },
              ],
            },
          });
        }),
      );

      const result = await previewDummyEPG({
        sample_name: 'x',
        title_pattern: '(?P<name>.+)',
        title_template: '{name|uppercase}',
        include_trace: true,
      });
      expect(sentBody?.include_trace).toBe(true);
      expect(result.traces?.title_template?.[0]?.kind).toBe('placeholder');
    });
  });

  // ===========================================================================
  // Logo loader debug-logging contract (bd-nh50y)
  //
  // These tests lock the log-line sequence emitted by getAllLogos(). The lines
  // are operator-grepable ("[LogoLoader] Starting...", "[LogoLoader] Page N:...")
  // and are how we diagnose "logos not loading" reports. A future refactor that
  // silently drops a line would fail these tests rather than dropping
  // observability into production.
  // ===========================================================================

  describe('getAllLogos (debug-logging contract — bd-nh50y)', () => {
    function makeLogo(id: number) {
      return { id, name: `Logo ${id}`, url: `http://example/${id}.png` };
    }

    it('emits the full happy-path log sequence across multiple pages', async () => {
      // Two pages: page 1 returns next=URL, page 2 returns next=null. The
      // pagination loop stops when the API reports no next page; we verify
      // every diagnostic line in the documented sequence fires in order.
      const requestPages: number[] = [];
      server.use(
        http.get('/api/channels/logos', ({ request }) => {
          const url = new URL(request.url);
          const page = Number(url.searchParams.get('page') ?? '1');
          requestPages.push(page);
          if (page === 1) {
            return HttpResponse.json({
              results: [makeLogo(1), makeLogo(2)],
              count: 3,
              next: '/api/channels/logos?page=2',
              previous: null,
            });
          }
          return HttpResponse.json({
            results: [makeLogo(3)],
            count: 3,
            next: null,
            previous: '/api/channels/logos?page=1',
          });
        }),
      );

      // Ensure DEBUG lines actually emit (singleton default is INFO).
      const prevLevel = logger.getLevel();
      logger.setLevel('DEBUG');
      const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
      const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

      try {
        const result = await getAllLogos(500);

        expect(result).toHaveLength(3);
        expect(requestPages).toEqual([1, 2]);
        expect(errSpy).not.toHaveBeenCalled();

        // Flatten all formatted messages so we can assert on ordered content.
        const messages = logSpy.mock.calls.map((call) => String(call[0]));

        // INFO "Starting logo load" fires first
        const startIdx = messages.findIndex((m) =>
          m.includes('[LogoLoader] Starting logo load'),
        );
        expect(startIdx).toBeGreaterThanOrEqual(0);
        expect(messages[startIdx]).toContain('pageSize=500');

        // DEBUG per page in order
        const page1Idx = messages.findIndex((m) =>
          m.includes('[LogoLoader] Page 1: fetched 2 logos, hasMore=true'),
        );
        const page2Idx = messages.findIndex((m) =>
          m.includes('[LogoLoader] Page 2: fetched 1 logos, hasMore=false'),
        );
        expect(page1Idx).toBeGreaterThan(startIdx);
        expect(page2Idx).toBeGreaterThan(page1Idx);

        // INFO completion fires last with total + page count
        const doneIdx = messages.findIndex((m) =>
          m.includes('[LogoLoader] Loaded 3 logos across 2 pages'),
        );
        expect(doneIdx).toBeGreaterThan(page2Idx);

        // getLogos() per-call DEBUG lines also fire (the API-wrapper layer)
        const apiCalls = messages.filter((m) => m.includes('[LogoApi]'));
        // Each page fires "GET /logos" + "Received N logos" — so 2 pages = 4 lines.
        expect(apiCalls.length).toBe(4);
        expect(apiCalls[0]).toContain('GET /logos page=1');
        expect(apiCalls[1]).toContain('Received 2 logos');
        expect(apiCalls[2]).toContain('GET /logos page=2');
        expect(apiCalls[3]).toContain('Received 1 logos');
      } finally {
        logSpy.mockRestore();
        errSpy.mockRestore();
        logger.setLevel(prevLevel);
      }
    });

    it('emits ERROR with the failing page number and partial-results length on mid-pagination failure', async () => {
      // Page 1 succeeds, page 2 errors. The contract: getAllLogos must log an
      // ERROR line that names both the page number and how many logos were
      // collected before the failure — that is the operator's only signal
      // about how much of the list was lost.
      server.use(
        http.get('/api/channels/logos', ({ request }) => {
          const url = new URL(request.url);
          const page = Number(url.searchParams.get('page') ?? '1');
          if (page === 1) {
            return HttpResponse.json({
              results: [makeLogo(1), makeLogo(2)],
              count: 5,
              next: '/api/channels/logos?page=2',
              previous: null,
            });
          }
          return HttpResponse.error();
        }),
      );

      const prevLevel = logger.getLevel();
      logger.setLevel('DEBUG');
      const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
      const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

      try {
        await expect(getAllLogos(500)).rejects.toThrow();

        // ERROR fires from getAllLogos with page-number + partial-results
        const errorMessages = errSpy.mock.calls.map((call) => String(call[0]));
        const loaderError = errorMessages.find((m) =>
          m.includes('[LogoLoader] Failed on page 2'),
        );
        expect(loaderError).toBeDefined();
        expect(loaderError).toContain('2 partial results');

        // The wrapper-level error from getLogos() also fires for the failing page
        const apiError = errorMessages.find(
          (m) => m.includes('[LogoApi]') && m.includes('failed'),
        );
        expect(apiError).toBeDefined();
        expect(apiError).toContain('page=2');
      } finally {
        logSpy.mockRestore();
        errSpy.mockRestore();
        logger.setLevel(prevLevel);
      }
    });
  });

  // Logo Manager sort/unused-filter query params (bead
  // enhancedchannelmanager-09x38.13). Locks the wire contract getLogos()
  // sends so LogoManagerTab's server-side sort/filter stays correct.
  describe('getLogos sort/filter query params (bead 09x38.13)', () => {
    it('sends sort_by/sort_order/unused_only when provided', async () => {
      let capturedUrl: URL | null = null;
      server.use(
        http.get('/api/channels/logos', ({ request }) => {
          capturedUrl = new URL(request.url);
          return HttpResponse.json({ results: [], count: 0, next: null, previous: null });
        }),
      );

      await getLogos({
        page: 2,
        pageSize: 50,
        search: 'espn',
        sortBy: 'channel_count',
        sortOrder: 'asc',
        unusedOnly: true,
      });

      expect(capturedUrl).not.toBeNull();
      const params = capturedUrl!.searchParams;
      expect(params.get('page')).toBe('2');
      expect(params.get('page_size')).toBe('50');
      expect(params.get('search')).toBe('espn');
      expect(params.get('sort_by')).toBe('channel_count');
      expect(params.get('sort_order')).toBe('asc');
      expect(params.get('unused_only')).toBe('true');
    });

    it('omits sort/filter params entirely when not requested', async () => {
      let capturedUrl: URL | null = null;
      server.use(
        http.get('/api/channels/logos', ({ request }) => {
          capturedUrl = new URL(request.url);
          return HttpResponse.json({ results: [], count: 0, next: null, previous: null });
        }),
      );

      await getLogos({ page: 1, pageSize: 50 });

      expect(capturedUrl).not.toBeNull();
      const params = capturedUrl!.searchParams;
      expect(params.has('sort_by')).toBe(false);
      expect(params.has('unused_only')).toBe(false);
    });
  });

  // Wire-format guards for the Settings → Integrations test-connection
  // endpoints (bd-8zi93). The backend Pydantic schemas pick specific field
  // names per integration:
  //   - Emby:     { base_url, api_key }
  //   - Plex:     { base_url, token }       — X-Plex-Token nomenclature
  //   - Jellyfin: { base_url, api_key }
  // A mismatch surfaces as a 422 "Field required" inline error in the UI
  // (the original Plex symptom). These tests lock the exact JSON shape sent
  // over the wire so a future rename can't silently break it again.
  describe('integration test-connection wire format', () => {
    it('testEmbyConnection sends { base_url, api_key }', async () => {
      let requestBody: unknown;
      server.use(
        http.post('/api/settings/emby/test-connection', async ({ request }) => {
          requestBody = await request.json();
          return HttpResponse.json({ ok: true });
        }),
      );

      await testEmbyConnection('http://emby.local:8096', 'emby-api-key');

      expect(requestBody).toEqual({
        base_url: 'http://emby.local:8096',
        api_key: 'emby-api-key',
      });
    });

    it('testPlexConnection sends { base_url, token }', async () => {
      let requestBody: unknown;
      server.use(
        http.post('/api/settings/plex/test-connection', async ({ request }) => {
          requestBody = await request.json();
          return HttpResponse.json({ ok: true });
        }),
      );

      await testPlexConnection('http://plex.local:32400', 'plex-token-value');

      expect(requestBody).toEqual({
        base_url: 'http://plex.local:32400',
        token: 'plex-token-value',
      });
    });

    it('testJellyfinConnection sends { base_url, api_key }', async () => {
      let requestBody: unknown;
      server.use(
        http.post('/api/settings/jellyfin/test-connection', async ({ request }) => {
          requestBody = await request.json();
          return HttpResponse.json({ ok: true });
        }),
      );

      await testJellyfinConnection('http://jellyfin.local:8096', 'jellyfin-api-key');

      expect(requestBody).toEqual({
        base_url: 'http://jellyfin.local:8096',
        api_key: 'jellyfin-api-key',
      });
    });
  });

  // GH #720 Part B (#9) — profile-apply summary interpretation. These pure
  // functions drive the incomplete-apply warning on EVERY save path (primary,
  // linked cascade, Sync Groups union), so they are unit-tested directly.
  describe('profileApplyIncomplete / profileApplyWarningMessage', () => {
    it('an empty or undefined summary is a clean no-op (not incomplete)', () => {
      expect(api.profileApplyIncomplete(undefined)).toBe(false);
      expect(api.profileApplyIncomplete([])).toBe(false);
      expect(api.profileApplyWarningMessage([])).toBeNull();
    });

    it('a clean reconciled-only summary is not incomplete', () => {
      const s = [{ status: 'reconciled' }, { status: 'no_selection' }];
      expect(api.profileApplyIncomplete(s)).toBe(false);
      expect(api.profileApplyWarningMessage(s)).toBeNull();
    });

    it.each([
      ['partial_failure', [{ status: 'partial_failure' }], 'applying some channel profiles failed'],
      ['degraded', [{ status: 'degraded' }], 'could not be fully enforced'],
      ['error-with-detail', [{ status: 'error', error: 'account 22 not updated — Re-save' }], 'account 22 not updated'],
      ['error-no-detail', [{ status: 'error' }], 'hit an error'],
      ['stale_selection', [{ status: 'stale_selection' }], 'no longer exist'],
      ['conflict', [{ status: 'reconciled', conflict: true }], 'conflicting profile selections'],
      ['failed_profile_ids', [{ status: 'reconciled', failed_profile_ids: [2] }], 'applying some channel profiles failed'],
    ])('%s is incomplete with status-specific guidance', (_label, summary, needle) => {
      expect(api.profileApplyIncomplete(summary as never)).toBe(true);
      expect(api.profileApplyWarningMessage(summary as never)).toContain(needle);
    });

    it('accumulated summaries (multi save path) surface the worst status; stale outranks partial', () => {
      // Models the linked-cascade / Sync-Groups accumulation: one path partial,
      // another stale -> the actionable (stale) guidance wins.
      const accumulated = [
        { status: 'partial_failure' },
        { status: 'stale_selection' },
      ];
      expect(api.profileApplyWarningMessage(accumulated)).toContain('no longer exist');
    });

    it('stale/conflict guidance does NOT promise auto-retry (which cannot fix them)', () => {
      expect(api.profileApplyWarningMessage([{ status: 'stale_selection' }]))
        .not.toContain('retry automatically');
      expect(api.profileApplyWarningMessage([{ status: 'reconciled', conflict: true }]))
        .not.toContain('retry automatically');
    });
  });
});
