/**
 * Unit tests for stream loading API endpoints.
 * Validates pagination, filtering, and stream-groups contract.
 */
import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach } from 'vitest';
import { server } from '../test/mocks/server';
import { mockDataStore, createMockStream, resetMockDataStore } from '../test/mocks/handlers';
import { getStreams, getStreamGroups } from './api';

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('Stream Loading API', () => {
  beforeEach(() => {
    resetMockDataStore();
  });

  describe('getStreams', () => {
    it('returns paginated response with next and count', async () => {
      // Add enough streams to span 2 pages
      for (let i = 0; i < 5; i++) {
        mockDataStore.streams.push(createMockStream({ name: `Stream ${i}` }));
      }

      const result = await getStreams({ page: 1, pageSize: 3 });

      expect(result).toHaveProperty('results');
      expect(result).toHaveProperty('count', 5);
      expect(result).toHaveProperty('next');
      expect(result.next).not.toBeNull();
      expect(result.results).toHaveLength(3);
    });

    it('returns null next on last page', async () => {
      mockDataStore.streams.push(createMockStream({ name: 'Only Stream' }));

      const result = await getStreams({ page: 1, pageSize: 100 });

      expect(result.next).toBeNull();
      expect(result.results).toHaveLength(1);
    });

    it('passes search parameter for server-side filtering', async () => {
      mockDataStore.streams.push(
        createMockStream({ name: 'ESPN HD' }),
        createMockStream({ name: 'CNN Live' }),
        createMockStream({ name: 'ESPN 2' }),
      );

      const result = await getStreams({ search: 'ESPN' });

      expect(result.results).toHaveLength(2);
      expect(result.results.every(s => s.name.includes('ESPN'))).toBe(true);
      expect(result.count).toBe(2);
    });

    it('passes m3uAccount filter parameter', async () => {
      mockDataStore.streams.push(
        createMockStream({ name: 'Stream A', m3u_account: 1 }),
        createMockStream({ name: 'Stream B', m3u_account: 2 }),
        createMockStream({ name: 'Stream C', m3u_account: 1 }),
      );

      const result = await getStreams({ m3uAccount: 1 });

      expect(result.results).toHaveLength(2);
      expect(result.results.every(s => s.m3u_account === 1)).toBe(true);
    });

    it('passes channelGroup filter parameter', async () => {
      mockDataStore.streams.push(
        createMockStream({ name: 'Sports 1', channel_group_name: 'Sports' }),
        createMockStream({ name: 'News 1', channel_group_name: 'News' }),
        createMockStream({ name: 'Sports 2', channel_group_name: 'Sports' }),
      );

      const result = await getStreams({ channelGroup: 'Sports' });

      expect(result.results).toHaveLength(2);
      expect(result.results.every(s => s.channel_group_name === 'Sports')).toBe(true);
    });

    it('respects pageSize parameter', async () => {
      for (let i = 0; i < 10; i++) {
        mockDataStore.streams.push(createMockStream({ name: `Stream ${i}` }));
      }

      const result = await getStreams({ page: 1, pageSize: 5 });

      expect(result.results).toHaveLength(5);
      expect(result.count).toBe(10);
    });
  });

  describe('getStreamGroups', () => {
    it('returns group names and counts derived from streams', async () => {
      mockDataStore.streams.push(
        createMockStream({ channel_group_name: 'Sports' }),
        createMockStream({ channel_group_name: 'Sports' }),
        createMockStream({ channel_group_name: 'News' }),
        createMockStream({ channel_group_name: 'Movies' }),
      );

      const groups = await getStreamGroups();

      expect(groups).toHaveLength(3);
      const sports = groups.find(g => g.name === 'Sports');
      expect(sports).toBeDefined();
      expect(sports!.stream_count).toBe(2);
      const news = groups.find(g => g.name === 'News');
      expect(news).toBeDefined();
      expect(news!.stream_count).toBe(1);
    });

    it('returns empty array when no streams exist', async () => {
      const groups = await getStreamGroups();
      expect(groups).toHaveLength(0);
    });
  });
});
