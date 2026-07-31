/**
 * Unit tests for EPGManagerTab — legacy "Dummy EPG Sources" section folding
 * (bead enhancedchannelmanager-09x38.4).
 *
 * PO DECISION #2 (Option B): the ECM "Dummy EPG Profiles" path is the
 * supported one; the legacy Dispatcharr `source_type=dummy` section is
 * deprecated. When an instance has ZERO legacy dummy sources the whole
 * section disappears (no empty state, no "+ Add"). When legacy sources exist
 * (grandfathered on other deployments) they render — visible, editable — with
 * a deprecation notice and NO new-creation affordance.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { EPGManagerTab, getTiedPriorities } from './EPGManagerTab';
import type { EPGSource } from '../../types';
import { HttpError } from '../../services/httpClient';

vi.mock('../../services/api', () => ({
  getEPGSources: vi.fn(),
  getEPGSource: vi.fn(),
  updateEPGSource: vi.fn(),
  deleteEPGSource: vi.fn(),
  refreshEPGSource: vi.fn(),
  createEPGSource: vi.fn(),
  triggerEPGImport: vi.fn(),
}));

// Isolate the legacy section: stub the ECM profiles section (it fetches on
// mount) and the legacy edit modal.
vi.mock('../DummyEPGManagerSection', () => ({
  DummyEPGManagerSection: () => <div data-testid="ecm-dummy-epg-profiles" />,
}));
vi.mock('../DummyEPGSourceModal', () => ({
  DummyEPGSourceModal: () => null,
}));

const notificationMocks = vi.hoisted(() => ({
  success: vi.fn(),
  warning: vi.fn(),
  error: vi.fn(),
}));

vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => notificationMocks,
}));

import * as api from '../../services/api';

function makeSource(overrides: Partial<EPGSource>): EPGSource {
  return {
    id: 1,
    name: 'Source',
    source_type: 'xmltv',
    url: 'https://example.com/epg.xml',
    api_key: null,
    username: null,
    is_active: true,
    file_path: null,
    refresh_interval: 24,
    priority: 0,
    status: 'success',
    last_message: null,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    custom_properties: null,
    epg_data_count: '0',
    ...overrides,
  };
}

describe('EPGManagerTab — legacy Dummy EPG Sources section', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('hides the legacy section entirely when there are zero legacy dummy sources', async () => {
    vi.mocked(api.getEPGSources).mockResolvedValue([
      makeSource({ id: 1, name: 'Guru XMLTV', source_type: 'xmltv' }),
    ]);

    render(<EPGManagerTab />);

    // Standard list has rendered.
    expect(await screen.findByText('Guru XMLTV')).toBeInTheDocument();

    // No legacy section header, empty-state, deprecation notice, or Add button.
    expect(screen.queryByText(/Dummy EPG Sources/i)).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /Add Dummy EPG/i })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/No dummy EPG sources/i)
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/deprecated/i)
    ).not.toBeInTheDocument();
  });

  it('grandfathers existing legacy dummy sources: visible, manageable, deprecation notice, no Add', async () => {
    vi.mocked(api.getEPGSources).mockResolvedValue([
      makeSource({ id: 1, name: 'Guru XMLTV', source_type: 'xmltv' }),
      makeSource({
        id: 2,
        name: 'Old Dummy Source',
        source_type: 'dummy',
        url: null,
      }),
    ]);

    render(<EPGManagerTab />);

    // Legacy source renders (not stranded).
    expect(await screen.findByText('Old Dummy Source')).toBeInTheDocument();

    // Section header marks it legacy, deprecation notice points to profiles.
    expect(screen.getByText(/Dummy EPG Sources \(Legacy\)/i)).toBeInTheDocument();
    expect(screen.getByText(/deprecated/i)).toBeInTheDocument();
    expect(screen.getByText(/Dummy EPG Profiles/i)).toBeInTheDocument();

    // Manageable: edit/delete/toggle controls still present on the legacy row.
    const legacyRow = screen.getByText('Old Dummy Source').closest('.dummy-source-row') as HTMLElement;
    expect(legacyRow).not.toBeNull();
    const within = (name: RegExp) =>
      Array.from(legacyRow.querySelectorAll('button')).find(
        (b) => name.test(b.getAttribute('aria-label') || '')
      );
    expect(within(/Edit EPG source/i)).toBeTruthy();
    expect(within(/Delete EPG source/i)).toBeTruthy();
    expect(within(/Enable EPG source|Disable EPG source/i)).toBeTruthy();

    // No new-creation affordance.
    expect(
      screen.queryByRole('button', { name: /Add Dummy EPG/i })
    ).not.toBeInTheDocument();
  });
});

describe('EPGManagerTab — source lifecycle', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('recovers from a transient failure through the scoped Retry action', async () => {
    vi.mocked(api.getEPGSources)
      .mockRejectedValueOnce(new Error('Network down'))
      .mockResolvedValueOnce([makeSource({ name: 'Recovered EPG' })]);

    render(<EPGManagerTab />);

    expect(await screen.findByRole('status', { name: 'EPG sources unavailable' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Retry loading EPG sources' }));

    expect(await screen.findByText('Recovered EPG')).toBeVisible();
    // Same removal as M3U Manager (bead enhancedchannelmanager-tygwm): the
    // healthy header no longer restates the source count, so recovery is
    // proven by the failure status and its Retry going away.
    expect(screen.queryByRole('status', { name: 'EPG sources unavailable' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry loading EPG sources' })).not.toBeInTheDocument();
    expect(screen.queryByText(/\d+ EPG sources?/)).not.toBeInTheDocument();
    expect(api.getEPGSources).toHaveBeenCalledTimes(2);
  });

  it('does not offer Retry or protected actions for a permission failure', async () => {
    vi.mocked(api.getEPGSources).mockRejectedValue(new HttpError('Forbidden', 403));

    render(<EPGManagerTab />);

    expect(await screen.findByRole('status', { name: 'EPG sources access denied' })).toBeVisible();
    expect(screen.queryByRole('button', { name: /Retry loading/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Add Standard EPG/i })).not.toBeInTheDocument();
  });
});

describe('EPGManagerTab — standard sources section heading', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  // Bead enhancedchannelmanager-7dxx0. The page opened on an unlabelled
  // table: route title, description, then straight into the sources list —
  // while the section below it was labelled "Dummy EPG Profiles". The
  // heading is rendered by PageHeader, so asserting it sits inside
  // `.header-title` is what pins it to the shared section role
  // (15px/600/1.3, asserted from disk in PageHeader.test.tsx) rather than a
  // hand-rolled h2 with per-page typography.
  it('labels the sources table with a section heading rendered above it', async () => {
    vi.mocked(api.getEPGSources).mockResolvedValue([
      makeSource({ id: 1, name: 'Guru XMLTV', source_type: 'xmltv' }),
    ]);

    render(<EPGManagerTab />);

    const list = (await screen.findByText('Guru XMLTV')).closest('.epg-sources-list');
    expect(list).not.toBeNull();

    const heading = screen.getByRole('heading', { level: 2, name: 'EPG Sources' });
    expect(heading.closest('.header-title')).not.toBeNull();
    expect(
      heading.compareDocumentPosition(list as HTMLElement) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
  });

  // The heading is not conditional on there being rows. Beyond keeping the
  // pane's structure stable between states, it repairs an outline that
  // skipped h1 -> h3: the empty state's "No EPG Sources" is an h3 and the
  // route title is the page's only h1.
  it('keeps the heading over the empty state so the outline never skips a level', async () => {
    vi.mocked(api.getEPGSources).mockResolvedValue([]);

    render(<EPGManagerTab />);

    const heading = await screen.findByRole('heading', { level: 2, name: 'EPG Sources' });
    const emptyState = screen.getByRole('heading', { level: 3, name: 'No EPG Sources' });
    expect(
      heading.compareDocumentPosition(emptyState) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
  });
});

describe('getTiedPriorities', () => {
  it('returns priorities shared by two or more sources', () => {
    const sources = [
      makeSource({ id: 1, priority: 5 }),
      makeSource({ id: 2, priority: 5 }),
      makeSource({ id: 3, priority: 9 }),
    ];

    expect(getTiedPriorities(sources)).toEqual(new Set([5]));
  });

  it('returns an empty set when every priority is unique', () => {
    const sources = [
      makeSource({ id: 1, priority: 1 }),
      makeSource({ id: 2, priority: 2 }),
      makeSource({ id: 3, priority: 3 }),
    ];

    expect(getTiedPriorities(sources)).toEqual(new Set());
  });
});

describe('EPGManagerTab — priority-tie badge (bead 09x38.15 item 1)', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('renders a tie badge only on rows sharing a priority', async () => {
    vi.mocked(api.getEPGSources).mockResolvedValue([
      makeSource({ id: 1, name: 'Tied A', source_type: 'xmltv', priority: 3 }),
      makeSource({ id: 2, name: 'Tied B', source_type: 'xmltv', priority: 3 }),
      makeSource({ id: 3, name: 'Unique C', source_type: 'xmltv', priority: 7 }),
    ]);

    render(<EPGManagerTab />);

    const tiedARow = (await screen.findByText('Tied A')).closest('.epg-source-row') as HTMLElement;
    const tiedBRow = screen.getByText('Tied B').closest('.epg-source-row') as HTMLElement;
    const uniqueRow = screen.getByText('Unique C').closest('.epg-source-row') as HTMLElement;

    expect(tiedARow.querySelector('.priority-tie-badge')).toBeTruthy();
    expect(tiedBRow.querySelector('.priority-tie-badge')).toBeTruthy();
    expect(uniqueRow.querySelector('.priority-tie-badge')).toBeFalsy();
  });
});
