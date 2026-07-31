/**
 * The Scheduled Tasks rail's anchors are PINNED TO `task_id`, and the page
 * deliberately offers no rail at all while the task list is in flight
 * (bead enhancedchannelmanager-de6u1; the Stats half is 22fef24d / mch8j and
 * the Settings half 4af8f487 / b32co).
 *
 * WHY PINNED. `StickySectionNav.discover()` reads `data-section-id` FIRST and
 * unconditionally, so a Settings section CAN pin its id — only the `id`
 * ATTRIBUTE path discards a `settings-`-prefixed value. Left unpinned, each
 * rail id is slugged from `task_name`, which is a display string: renaming a
 * cross-instance sync TARGET rewrites `task_name` ("Cross-Instance Sync: %s")
 * while `task_id` stays put, so every shared link to that card dies silently.
 * `task_id` is the registry key and is what a link should have named all along.
 *
 * THE ID STRINGS BELOW ARE THE CONTRACT. They are asserted literally, not by
 * pattern, because a pattern would still pass if the derivation silently
 * changed. The `settings-scheduled-tasks-` prefix is written out rather than
 * composed from the route: a pinned id must NOT follow the route slug around,
 * or it is not pinned.
 *
 * `_` MAPS TO `-`. `useHashRoute.parseHash` keeps a `?section=` value only if
 * it matches /^[a-z0-9-]+$/, and silently rewrites the hash without the query
 * otherwise — so a raw snake_case task_id renders a perfectly good anchor that
 * no URL can ever reach. Measured in a browser, not inferred. Its own test is
 * below; every backend task_id is [a-z0-9_]+, so the mapping cannot collide.
 *
 * WHY THERE IS NO LOADING PLACEHOLDER HERE, unlike every other page in this
 * defect class. Those pages have a compile-time set of sections, so a
 * placeholder can carry the real label and the real id. Here every entry comes
 * from the fetch, so a placeholder would have to invent both:
 *   - its COUNT is unknown, so N != actual still reflows the rail, and worse,
 *     entries change identity rather than merely appearing;
 *   - its IDS cannot be known, so a deep link still cannot resolve during the
 *     load window — the placeholder would not fix the thing it exists to fix;
 *   - its HEIGHT cannot match. A real task card is ~180px and there are 17 of
 *     them; a deep-linked reader landing against placeholder geometry gets
 *     re-adjusted by ~1800px when the real cards arrive, which is worse than
 *     the single scroll it would replace.
 * So the rail is absent until the count is known, and appears complete in the
 * same commit as the content it indexes. The second test pins that: nothing
 * the rail's selector matches may exist while the fetch is pending.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, within, waitFor, fireEvent } from '@testing-library/react';
import type { TaskStatus } from '../services/api';

vi.mock('../services/api', async () => {
  const actual = await vi.importActual<typeof import('../services/api')>('../services/api');
  return { ...actual, getSettings: vi.fn(), getTasks: vi.fn() };
});

// One STABLE object: `loadTasks` is a useCallback over `notifications`, and
// the mount effect depends on it. A fresh object per render would re-run the
// effect on every render, so the component would re-enter its loading branch
// forever and the test would be measuring the harness, not the component.
const notify = {
  success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(),
  notify: vi.fn(), dismiss: vi.fn(), dismissAll: vi.fn(),
};
vi.mock('../contexts/NotificationContext', () => ({ useNotifications: () => notify }));

// TaskHistoryPanel fetches its own history per card; not under test.
vi.mock('./TaskHistoryPanel', () => ({ TaskHistoryPanel: () => null }));

import * as api from '../services/api';
import { StickySectionNav } from './StickySectionNav';
import { ScheduledTasksSection } from './ScheduledTasksSection';
import { useRef } from 'react';

/** A promise that never settles — the page stays in its loading branch. */
const pending = <T,>() => new Promise<T>(() => {});

function makeTask(task_id: string, task_name: string): TaskStatus {
  return {
    task_id,
    task_name,
    task_description: `${task_name} description`,
    status: 'idle',
    enabled: true,
    effective_enabled: true,
    progress: {
      total: 0, current: 0, status: 'idle', current_item: null,
      success_count: 0, failed_count: 0, skipped_count: 0,
    },
    schedule: { schedule_type: 'manual' },
    schedules: [],
    last_run: null,
    next_run: null,
    config: {},
  } as unknown as TaskStatus;
}

/**
 * Mirrors SettingsTab's own wiring for the Scheduled Tasks page: the scroll
 * container, the pane the selector runs over, and the rail. `routeKey` is
 * DELIBERATELY not `settings-scheduled-tasks` — a pinned id must be
 * independent of it, and this proves the pin rather than the fallback.
 */
function Harness() {
  const containerRef = useRef<HTMLDivElement>(null);
  return (
    <div className="settings-content" ref={containerRef}>
      <div className="settings-content-main" data-settings-page="scheduled-tasks">
        <ScheduledTasksSection />
      </div>
      <StickySectionNav
        placement="rail"
        containerRef={containerRef}
        selector=".settings-section, [data-settings-section]"
        routeKey="routekey-that-must-not-appear"
      />
    </div>
  );
}

/**
 * Real task_ids and task_names off the live instance, chosen because their
 * label slugs and their task_ids diverge — an assertion that passed under
 * either derivation would prove nothing. The last one is the volatile case the
 * bead is really about: `task_name` embeds a user-renameable sync target.
 */
const TASKS = [
  makeTask('cleanup', 'Database Cleanup'),
  makeTask('stats_v2_rollup', 'Stats v2 Rollup & Prune'),
  makeTask('failed_stream_reprobe', 'Re-probe Failed Streams'),
  makeTask('dbas_sync_3', 'Cross-Instance Sync: Prod Replica'),
];

/** Label, pinned id, and the id the LABEL SLUG used to produce. */
const PINNED = [
  ['Database Cleanup', 'settings-scheduled-tasks-section-cleanup',
    'settings-scheduled-tasks-section-database-cleanup'],
  ['Stats v2 Rollup & Prune', 'settings-scheduled-tasks-section-stats-v2-rollup',
    'settings-scheduled-tasks-section-stats-v2-rollup-prune'],
  ['Re-probe Failed Streams', 'settings-scheduled-tasks-section-failed-stream-reprobe',
    'settings-scheduled-tasks-section-re-probe-failed-streams'],
  ['Cross-Instance Sync: Prod Replica', 'settings-scheduled-tasks-section-dbas-sync-3',
    'settings-scheduled-tasks-section-cross-instance-sync-prod-replica'],
] as const;

const originalHash = window.location.hash;

describe('Scheduled Tasks section rail — anchors pinned to task_id (bead de6u1)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.scrollTo = vi.fn();
    Element.prototype.scrollIntoView = vi.fn();
    vi.mocked(api.getSettings).mockReturnValue(pending());
  });

  afterEach(() => {
    window.location.hash = originalHash;
    vi.useRealTimers();
  });

  it('names every rail entry by task_name and every anchor by task_id', async () => {
    vi.mocked(api.getTasks).mockResolvedValue({ tasks: TASKS });

    const { container } = render(<Harness />);

    const nav = await screen.findByRole('navigation', { name: 'On this page' });
    // Both halves together, because they are one contract: an entry that kept
    // its label but moved its id still breaks every shared link.
    expect(within(nav).getAllByRole('button').map((b) => b.textContent))
      .toEqual(PINNED.map(([label]) => label));
    for (const [, id] of PINNED) {
      expect(container.querySelector(`[id="${id}"]`), id).toBeInTheDocument();
    }
    // Every fixture task's label slug DIFFERS from its task_id, so the old
    // derivation cannot still be in play, and neither can the routeKey
    // fallback.
    for (const [, , labelSlugId] of PINNED) {
      expect(container.querySelector(`[id="${labelSlugId}"]`), labelSlugId).toBeNull();
    }
    expect(container.querySelector('[id^="routekey-that-must-not-appear"]')).toBeNull();
  });

  it('gives every anchor an id the hash router will actually carry', async () => {
    // useHashRoute.parseHash accepts `?section=` only if it matches
    // /^[a-z0-9-]+$/ and SILENTLY DROPS anything else, rewriting the hash
    // without the query. A raw snake_case task_id is therefore unlinkable: the
    // anchor renders, the URL loses the section, and the link dies with no
    // error anywhere. Measured in a browser before this guard existed.
    vi.mocked(api.getTasks).mockResolvedValue({ tasks: TASKS });

    const { container } = render(<Harness />);
    await screen.findByRole('navigation', { name: 'On this page' });

    const ids = [...container.querySelectorAll('[data-settings-section]')].map((el) => el.id);
    expect(ids).toHaveLength(TASKS.length);
    for (const id of ids) expect(id, id).toMatch(/^[a-z0-9-]+$/);
    // The `_`-to-`-` mapping must stay injective over real task_ids.
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('keeps a card\'s anchor when its task_name is renamed under it', async () => {
    // The whole point of the pin: renaming a cross-instance sync target
    // rewrites task_name and must NOT move the id a shared link names.
    vi.mocked(api.getTasks).mockResolvedValue({ tasks: TASKS });
    const { container } = render(<Harness />);
    await screen.findByRole('navigation', { name: 'On this page' });

    // The rename arrives the way it really does: a refetch into the same
    // mounted component, same task_id, different task_name.
    vi.mocked(api.getTasks).mockResolvedValue({
      tasks: [
        TASKS[0], TASKS[1], TASKS[2],
        makeTask('dbas_sync_3', 'Cross-Instance Sync: Renamed Target'),
      ],
    });
    fireEvent.click(screen.getByRole('button', { name: /refresh/i }));

    const anchorBefore = container.querySelector(
      '[id="settings-scheduled-tasks-section-dbas-sync-3"]',
    );
    expect(anchorBefore).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('Cross-Instance Sync: Renamed Target')).toBeInTheDocument();
    });

    // The rename landed on the card, and the anchor a shared link names is the
    // same element under the same id. Unpinned, this id was slugged from the
    // label and would now be
    // `settings-scheduled-tasks-section-cross-instance-sync-renamed-target`.
    expect(container.querySelector('[data-section-label="Cross-Instance Sync: Renamed Target"]'))
      .toBe(anchorBefore);
    expect(anchorBefore).toHaveAttribute(
      'id', 'settings-scheduled-tasks-section-dbas-sync-3',
    );
    expect(container.querySelector('[id="settings-scheduled-tasks-section-cross-instance-sync-renamed-target"]'))
      .toBeNull();

    // NOT asserted: the rail's visible LABEL. StickySectionNav observes only
    // { childList, subtree }, so a label that changes by attribute or by a
    // single text node's value never wakes discover() and the rail entry stays
    // on the old name. That is a pre-existing StickySectionNav defect, reported
    // separately; pinning the id here strictly reduces its blast radius, since
    // before the pin the same rename silently moved the anchor as well.
  });

  it('resolves a shared link that names a task_id, scrolling the settings container', async () => {
    window.location.hash = '#settings/scheduled-tasks?section=settings-scheduled-tasks-section-dbas-sync-3';
    // Record the element each scroll landed on: the nav must scroll its own
    // container and nothing else.
    const scrolled: HTMLElement[] = [];
    Element.prototype.scrollTo = vi.fn(function (this: HTMLElement) { scrolled.push(this); });
    vi.mocked(api.getTasks).mockResolvedValue({ tasks: TASKS });

    const { container } = render(<Harness />);

    await screen.findByRole('navigation', { name: 'On this page' });
    await waitFor(() => expect(scrolled.length).toBeGreaterThan(0));
    expect(scrolled[0]).toBe(container.querySelector('.settings-content'));
    // `scrollIntoView` would drag every scrollable ancestor, including the
    // document, out from under the fixed shell.
    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
  });
});

/**
 * The deliberate loading state. This is a DECISION guard, not a defect guard:
 * it is green before this bead as well as after, and it is here so that a
 * later "fix" cannot quietly add fabricated placeholder entries — entries
 * whose ids no shared link could ever name, and whose count and height are
 * guesses. The fetch never settles, so nothing here can pass by waiting.
 */
describe('Scheduled Tasks section rail — nothing is advertised before the count is known (bead de6u1)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.scrollTo = vi.fn();
    Element.prototype.scrollIntoView = vi.fn();
    vi.mocked(api.getSettings).mockReturnValue(pending());
    vi.mocked(api.getTasks).mockReturnValue(pending());
  });

  afterEach(() => {
    window.location.hash = originalHash;
  });

  it('exposes no rail and no anchor while the task list is in flight', async () => {
    const { container } = render(<Harness />);

    // This is the load window, not a settled page.
    expect(screen.getByText('Loading scheduled tasks...')).toBeInTheDocument();

    const pane = container.querySelector<HTMLElement>('.settings-content-main');
    expect(pane).not.toBeNull();
    expect([...pane!.querySelectorAll('.settings-section, [data-settings-section]')]).toEqual([]);
    expect(screen.queryByRole('navigation', { name: 'On this page' })).toBeNull();

    // Give the nav's MutationObserver and the deep-link effect every chance to
    // fire, then assert the page still offers nothing to point at.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByRole('navigation', { name: 'On this page' })).toBeNull();
    expect(Element.prototype.scrollTo).not.toHaveBeenCalled();
  });
});
