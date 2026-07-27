/**
 * Tests for the tag-test panel added to TagEngineSection
 * (enhancedchannelmanager-hq3de.f) — mirrors NormalizationEngineSection's
 * test UX, but per tag group via POST /api/tags/test.
 *
 * Note: `useNotifications` must return a STABLE object across renders — the
 * real hook is useMemo'd; a fresh object literal per call would make
 * `loadGroups`'s useCallback unstable and re-fire its mount effect forever
 * (same gotcha documented in NormalizationEngineSection's test suites).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { TagEngineSection } from './TagEngineSection';

const mockSuccess = vi.fn();
const mockError = vi.fn();
const stableNotifications = { success: mockSuccess, error: mockError, warning: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => stableNotifications,
}));

vi.mock('../../services/api', () => ({
  getTagGroups: vi.fn(),
  getTagGroup: vi.fn(),
  createTagGroup: vi.fn(),
  updateTagGroup: vi.fn(),
  deleteTagGroup: vi.fn(),
  addTagsToGroup: vi.fn(),
  updateTag: vi.fn(),
  deleteTag: vi.fn(),
  exportTagsYaml: vi.fn(),
  importTagsYaml: vi.fn(),
  testTags: vi.fn(),
}));

import * as api from '../../services/api';

const group = {
  id: 1,
  name: 'US Networks',
  description: 'US network callsigns',
  is_builtin: false,
  tag_count: 2,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const groupTags = {
  ...group,
  tags: [
    { id: 100, group_id: 1, value: 'ESPN', case_sensitive: false, enabled: true, is_builtin: false },
    { id: 101, group_id: 1, value: 'FOX', case_sensitive: false, enabled: true, is_builtin: false },
  ],
};

describe('TagEngineSection — test panel (bead hq3de.f)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getTagGroups).mockResolvedValue({ groups: [group] });
    vi.mocked(api.getTagGroup).mockResolvedValue(groupTags);
  });

  async function expandGroupAndTestPanel() {
    render(<TagEngineSection />);
    await waitFor(() => screen.getByText('US Networks'));
    fireEvent.click(screen.getByText('US Networks'));
    await waitFor(() => screen.getByText('Test Tags'));
    fireEvent.click(screen.getByText('Test Tags'));
  }

  it('renders the test panel collapsed by default when a group is expanded', async () => {
    render(<TagEngineSection />);
    await waitFor(() => screen.getByText('US Networks'));
    fireEvent.click(screen.getByText('US Networks'));

    await waitFor(() => screen.getByText('Test Tags'));
    expect(screen.queryByPlaceholderText(/enter text to test/i)).not.toBeInTheDocument();
  });

  it('tests text against the group and shows matched tags', async () => {
    vi.mocked(api.testTags).mockResolvedValue({
      text: 'US: ESPN HD',
      group_id: 1,
      group_name: 'US Networks',
      matches: [{ tag_id: 100, value: 'ESPN', case_sensitive: false }],
      match_count: 1,
    });

    await expandGroupAndTestPanel();

    fireEvent.change(screen.getByPlaceholderText(/enter text to test/i), { target: { value: 'US: ESPN HD' } });
    fireEvent.click(screen.getByText('Test'));

    await waitFor(() => {
      expect(api.testTags).toHaveBeenCalledWith(1, 'US: ESPN HD');
    });
    await waitFor(() => {
      expect(screen.getByText('1 tag matched:')).toBeInTheDocument();
    });
  });

  it('shows a no-matches message when nothing matches', async () => {
    vi.mocked(api.testTags).mockResolvedValue({
      text: 'CBC News',
      group_id: 1,
      group_name: 'US Networks',
      matches: [],
      match_count: 0,
    });

    await expandGroupAndTestPanel();

    fireEvent.change(screen.getByPlaceholderText(/enter text to test/i), { target: { value: 'CBC News' } });
    fireEvent.click(screen.getByText('Test'));

    await waitFor(() => {
      expect(screen.getByText('No tags in this group matched.')).toBeInTheDocument();
    });
  });

  it('surfaces an error when the test request fails', async () => {
    vi.mocked(api.testTags).mockRejectedValue(new Error('network error'));

    await expandGroupAndTestPanel();

    fireEvent.change(screen.getByPlaceholderText(/enter text to test/i), { target: { value: 'ESPN' } });
    fireEvent.click(screen.getByText('Test'));

    await waitFor(() => {
      expect(screen.getByText('network error')).toBeInTheDocument();
    });
  });

  it('disables the Test button until text is entered', async () => {
    await expandGroupAndTestPanel();
    expect(screen.getByText('Test').closest('button')).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText(/enter text to test/i), { target: { value: 'x' } });
    expect(screen.getByText('Test').closest('button')).toBeEnabled();
  });
});
