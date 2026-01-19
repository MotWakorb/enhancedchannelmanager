/**
 * Unit tests for API service.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { server } from '../test/mocks/server';
import { http, HttpResponse } from 'msw';
import {
  getChannels,
  getChannel,
  updateChannel,
  addStreamToChannel,
  removeStreamFromChannel,
  reorderChannelStreams,
  deleteChannel,
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

  describe('getChannel', () => {
    it('fetches single channel', async () => {
      server.use(
        http.get('/api/channels/1', () => {
          return HttpResponse.json({
            id: 1,
            uuid: 'uuid-1',
            name: 'Test Channel',
            channel_number: 100,
            channel_group_id: null,
            streams: [],
          });
        })
      );

      const result = await getChannel(1);

      expect(result.id).toBe(1);
      expect(result.name).toBe('Test Channel');
    });

    it('throws on 404', async () => {
      server.use(
        http.get('/api/channels/999', () => {
          return HttpResponse.json(
            { detail: 'Channel not found' },
            { status: 404 }
          );
        })
      );

      await expect(getChannel(999)).rejects.toThrow('Channel not found');
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
});
