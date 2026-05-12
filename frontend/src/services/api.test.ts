/**
 * Unit tests for API service.
 */
import { describe, it, expect, afterEach, beforeAll, afterAll } from 'vitest';
import { server } from '../test/mocks/server';
import { http, HttpResponse } from 'msw';
import {
  getChannels,
  updateChannel,
  addStreamToChannel,
  removeStreamFromChannel,
  reorderChannelStreams,
  deleteChannel,
  // Compute sort
  computeSort,
  // Enhanced stats (v0.11.0)
  getBandwidthStats,
  getUniqueViewersSummary,
  getChannelBandwidthStats,
  getUniqueViewersByChannel,
  getPopularityRankings,
  getTrendingChannels,
  calculatePopularity,
  // Lookup tables (v0.14.0 dummy EPG template engine)
  listLookupTables,
  getLookupTable,
  createLookupTable,
  updateLookupTable,
  deleteLookupTable,
  // Dummy EPG preview
  previewDummyEPG,
  // Alert methods
  listAlertMethods,
  createAlertMethod,
  updateAlertMethod,
} from './api';

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

  // ===========================================================================
  // Lookup tables (v0.14.0 dummy EPG template engine)
  // ===========================================================================

  describe('listLookupTables', () => {
    it('returns summaries without entries payload', async () => {
      const result = await listLookupTables();
      expect(Array.isArray(result)).toBe(true);
      expect(result[0]).toMatchObject({
        id: 1,
        name: 'callsigns',
        entry_count: 2,
      });
      // The list endpoint omits `entries` — callers must fetch by id to edit.
      expect((result[0] as unknown as { entries?: unknown }).entries).toBeUndefined();
    });

    it('surfaces server errors', async () => {
      server.use(
        http.get('/api/lookup-tables', () => HttpResponse.error()),
      );
      await expect(listLookupTables()).rejects.toThrow();
    });
  });

  describe('getLookupTable', () => {
    it('fetches a single table with entries', async () => {
      const result = await getLookupTable(1);
      expect(result.id).toBe(1);
      expect(result.entries).toEqual({ ESPN: 'espn.com', CNN: 'cnn.com' });
    });

    it('interpolates the id into the URL', async () => {
      let capturedId: string | undefined;
      server.use(
        http.get('/api/lookup-tables/:id', ({ params }) => {
          capturedId = params.id as string;
          return HttpResponse.json({
            id: Number(capturedId),
            name: 'test',
            description: null,
            entries: {},
            entry_count: 0,
            created_at: null,
            updated_at: null,
          });
        }),
      );

      await getLookupTable(42);
      expect(capturedId).toBe('42');
    });
  });

  describe('createLookupTable', () => {
    it('POSTs name + description + entries and returns the created table', async () => {
      let requestBody: unknown;
      server.use(
        http.post('/api/lookup-tables', async ({ request }) => {
          requestBody = await request.json();
          return HttpResponse.json(
            {
              id: 99,
              name: 'seasons',
              description: 'TV seasons',
              entries: { S1: 'Season 1' },
              entry_count: 1,
              created_at: '2026-04-19T00:00:00Z',
              updated_at: '2026-04-19T00:00:00Z',
            },
            { status: 201 },
          );
        }),
      );

      const created = await createLookupTable({
        name: 'seasons',
        description: 'TV seasons',
        entries: { S1: 'Season 1' },
      });
      expect(requestBody).toMatchObject({
        name: 'seasons',
        description: 'TV seasons',
        entries: { S1: 'Season 1' },
      });
      expect(created.id).toBe(99);
    });
  });

  describe('updateLookupTable', () => {
    it('PATCHes and returns the updated table', async () => {
      const result = await updateLookupTable(1, { entries: { A: '1', B: '2' } });
      expect(result.entries).toEqual({ A: '1', B: '2' });
      expect(result.entry_count).toBe(2);
    });

    it('allows renaming without touching entries', async () => {
      let requestBody: { name?: string; entries?: unknown } | undefined;
      server.use(
        http.patch('/api/lookup-tables/:id', async ({ request }) => {
          requestBody = (await request.json()) as { name?: string; entries?: unknown };
          return HttpResponse.json({
            id: 1,
            name: requestBody.name ?? 'unchanged',
            description: null,
            entries: {},
            entry_count: 0,
            created_at: null,
            updated_at: null,
          });
        }),
      );

      await updateLookupTable(1, { name: 'renamed' });
      expect(requestBody?.name).toBe('renamed');
      expect(requestBody?.entries).toBeUndefined();
    });
  });

  describe('deleteLookupTable', () => {
    it('issues a DELETE and resolves on 204', async () => {
      await expect(deleteLookupTable(1)).resolves.toBeUndefined();
    });

    it('rejects on 404', async () => {
      server.use(
        http.delete('/api/lookup-tables/:id', () =>
          new HttpResponse(JSON.stringify({ detail: 'Lookup table not found' }), { status: 404 }),
        ),
      );
      await expect(deleteLookupTable(999)).rejects.toThrow();
    });
  });

  // ===========================================================================
  // Dummy EPG preview (lookup + trace surface used by the preview UI)
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

    it('forwards inline_lookups + global_lookup_ids in the payload', async () => {
      let sentBody: {
        inline_lookups?: Record<string, Record<string, string>>;
        global_lookup_ids?: number[];
      } | undefined;
      server.use(
        http.post('/api/dummy-epg/preview', async ({ request }) => {
          sentBody = (await request.json()) as typeof sentBody;
          return HttpResponse.json({
            original_name: 'x',
            substituted_name: 'x',
            substitution_steps: [],
            matched: true,
            matched_variant: null,
            groups: null,
            time_variables: null,
            rendered: {
              title: '',
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
          });
        }),
      );

      await previewDummyEPG({
        sample_name: 'x',
        inline_lookups: { codes: { a: '1' } },
        global_lookup_ids: [7, 8, 9],
      });
      expect(sentBody?.inline_lookups).toEqual({ codes: { a: '1' } });
      expect(sentBody?.global_lookup_ids).toEqual([7, 8, 9]);
    });
  });
});
