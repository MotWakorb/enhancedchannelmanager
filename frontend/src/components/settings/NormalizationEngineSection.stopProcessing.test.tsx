import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { NormalizationEngineSection } from './NormalizationEngineSection';
import * as api from '../../services/api';

const notifications = { error: vi.fn(), success: vi.fn() };
vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => notifications,
}));
vi.mock('../../services/api', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../services/api')>(),
  getNormalizationRules: vi.fn(),
  getTagGroups: vi.fn(),
  testNormalizationRule: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getNormalizationRules).mockResolvedValue({ groups: [{
    id: 1, name: 'Clippers', description: null, enabled: true, priority: 0,
    is_builtin: false, created_at: '', updated_at: '', rules: [],
  }] });
  vi.mocked(api.getTagGroups).mockResolvedValue({ groups: [] });
  vi.mocked(api.testNormalizationRule).mockImplementation(async (request) => ({
    matched: true, before: request.text,
    after: request.stop_processing ? ' LA  Clippers ' : 'LA Clippers',
    match_start: 0, match_end: request.text.length, matched_tag: null, else_applied: false,
  }));
});

it('sends the stop checkbox to Live Preview and displays its exact response', async () => {
  render(<NormalizationEngineSection />);
  fireEvent.click(await screen.findByText('Clippers'));
  fireEvent.click(screen.getByRole('button', { name: /Add Rule/ }));
  fireEvent.change(screen.getByPlaceholderText('e.g., HD'), {
    target: { value: 'Los Angeles Clippers' },
  });
  await waitFor(() => expect(api.testNormalizationRule).toHaveBeenLastCalledWith(
    expect.objectContaining({ stop_processing: false }),
  ));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Stop Processing After Match' }));
  await waitFor(() => expect(api.testNormalizationRule).toHaveBeenLastCalledWith(
    expect.objectContaining({ stop_processing: true }),
  ));
  expect(screen.getByText('LA Clippers', { exact: true }).textContent).toBe(' LA  Clippers ');
});
