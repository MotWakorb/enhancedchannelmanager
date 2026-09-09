import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getChannelMergeCandidates, type ChannelMergeCandidatesResponse } from './api';
import { fetchJson, HttpError } from './httpClient';

vi.mock('./httpClient', async (importOriginal) => ({
  ...await importOriginal<typeof import('./httpClient')>(),
  fetchJson: vi.fn(),
}));

const response: ChannelMergeCandidatesResponse = {
  stream_name: 'CNN HD',
  candidates: [{ channel_id: '00123', channel_name: 'CNN', confidence: 0.87 }],
  total: 1, page: 1, page_size: 1, total_pages: 1,
};

describe('getChannelMergeCandidates wire contract', () => {
  beforeEach(() => vi.resetAllMocks());

  it.each([
    ['CNN HD', undefined, '?stream_name=CNN+HD'],
    ['CNN HD', null, '?stream_name=CNN+HD'],
    ['CNN HD', 0, '?stream_name=CNN+HD&group_id=0'],
    ['CNN HD', 7, '?stream_name=CNN+HD&group_id=7'],
    ['A&B +/#?=\u00e9', 0, '?stream_name=A%26B+%2B%2F%23%3F%3D%C3%A9&group_id=0'],
    ['', undefined, ''],
    ['', 0, '?group_id=0'],
    ['  ', null, '?stream_name=++'],
  ] as const)('preserves the URL and response for %j / %s', async (name, group, query) => {
    vi.mocked(fetchJson).mockResolvedValue(response);
    expect(await getChannelMergeCandidates(name, group)).toBe(response);
    expect(fetchJson).toHaveBeenCalledExactlyOnceWith(`/api/channel-merges/candidates${query}`);
  });

  it.each([
    new HttpError('rejected', 422, [{ loc: ['query', 'stream_name'], msg: 'required' }]),
    new TypeError('network unavailable'),
    new SyntaxError('invalid JSON'),
  ])('preserves rejection identity: %s', async (error) => {
    vi.mocked(fetchJson).mockRejectedValue(error);
    await expect(getChannelMergeCandidates('CNN HD', 0)).rejects.toBe(error);
    expect(fetchJson).toHaveBeenCalledExactlyOnceWith('/api/channel-merges/candidates?stream_name=CNN+HD&group_id=0');
  });
});
