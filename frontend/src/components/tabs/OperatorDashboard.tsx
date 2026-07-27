import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from '../../services/api';
import { HttpError } from '../../services/httpClient';
import './OperatorDashboard.css';

type CardId = 'service' | 'lineup' | 'sources' | 'changes' | 'tasks' | 'journal';
type CardData = { value: React.ReactNode; status: string; freshness: string };
type CardState =
  | { kind: 'loading' }
  | { kind: 'success'; data: CardData }
  | { kind: 'error'; permission: boolean };

const cardMeta: Record<CardId, { label: string; href: string; error: string }> = {
  service: { label: 'ECM service', href: '#settings/general', error: 'ECM service status' },
  lineup: { label: 'Lineup inventory', href: '#channel-manager', error: 'lineup inventory' },
  sources: { label: 'Source accounts', href: '#m3u-manager', error: 'source accounts' },
  changes: { label: 'Recent M3U changes', href: '#m3u-changes', error: 'recent M3U changes' },
  tasks: { label: 'Scheduled work', href: '#settings/scheduled-tasks', error: 'scheduled tasks' },
  journal: { label: 'Recent journal', href: '#journal', error: 'journal summary' },
};

const checked = () => `Checked ${new Intl.DateTimeFormat(undefined, {
  hour: 'numeric', minute: '2-digit',
}).format(new Date())}`;

export function OperatorDashboard({ initialHealth }: { initialHealth?: api.HealthResponse | null }) {
  const [states, setStates] = useState<Record<CardId, CardState>>({
    service: initialHealth ? {
      kind: 'success',
      data: {
        value: initialHealth.status || 'Unknown',
        status: `${initialHealth.service || 'ECM'} ${initialHealth.version || 'Version unavailable'}${initialHealth.release_channel ? ` · ${initialHealth.release_channel}` : ''}`,
        freshness: 'Loaded this session',
      },
    } : { kind: 'loading' }, lineup: { kind: 'loading' }, sources: { kind: 'loading' },
    changes: { kind: 'loading' }, tasks: { kind: 'loading' }, journal: { kind: 'loading' },
  });
  const [announcement, setAnnouncement] = useState('');
  const mounted = useRef(true);

  useEffect(() => () => { mounted.current = false; }, []);

  const load = useCallback(async (id: CardId, announce = false) => {
    setStates((current) => ({ ...current, [id]: { kind: 'loading' } }));
    try {
      let data: CardData;
      if (id === 'service') {
        const health = await api.getHealth();
        data = {
          value: health.status || 'Unknown',
          status: `${health.service || 'ECM'} ${health.version || 'Version unavailable'}${health.release_channel ? ` · ${health.release_channel}` : ''}`,
          freshness: checked(),
        };
      } else if (id === 'lineup') {
        const [channels, streams] = await Promise.all([
          api.getChannels({ page: 1, pageSize: 1 }),
          api.getStreams({ page: 1, pageSize: 1 }),
        ]);
        data = {
          value: <>
            <span>{channels.count} {channels.count === 1 ? 'channel' : 'channels'}</span>
            <span>{streams.count} {streams.count === 1 ? 'stream' : 'streams'}</span>
          </>,
          status: channels.count + streams.count === 0 ? 'No lineup configured' : 'Inventory available',
          freshness: checked(),
        };
      } else if (id === 'sources') {
        const accounts = await api.getM3UAccounts();
        data = {
          value: `${accounts.length} ${accounts.length === 1 ? 'account' : 'accounts'}`,
          status: accounts.length === 0 ? 'No M3U accounts configured' : 'Configured sources',
          freshness: checked(),
        };
      } else if (id === 'changes') {
        const summary = await api.getM3UChangesSummary({ hours: 24 });
        data = {
          value: `${summary.total_changes} ${summary.total_changes === 1 ? 'change' : 'changes'}`,
          status: summary.total_changes === 0
            ? 'No recent M3U changes'
            : `${summary.streams_added} streams added · ${summary.streams_removed} removed`,
          freshness: `Since ${new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(summary.since))}`,
        };
      } else if (id === 'tasks') {
        const { tasks } = await api.getTasks();
        const enabled = tasks.filter((task) => task.effective_enabled ?? task.enabled).length;
        const running = tasks.filter((task) => task.status === 'running').length;
        const failed = tasks.filter((task) => task.status === 'failed').length;
        const runTimes = tasks
          .map((task) => task.last_run)
          .filter((value): value is string => Boolean(value))
          .sort();
        const latest = runTimes[runTimes.length - 1];
        data = {
          value: `${enabled} enabled`,
          status: tasks.length === 0 ? 'No scheduled tasks configured' : `${running} running · ${failed} failed`,
          freshness: latest ? `Last run ${new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(latest))}` : checked(),
        };
      } else {
        const stats = await api.getJournalStats();
        const categories = Object.keys(stats.by_category).length;
        data = {
          value: `${stats.total_entries} ${stats.total_entries === 1 ? 'entry' : 'entries'}`,
          status: stats.total_entries === 0 ? 'No journal entries' : `${categories} ${categories === 1 ? 'category' : 'categories'}`,
          freshness: stats.date_range.newest
            ? `Latest ${new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(stats.date_range.newest))}`
            : checked(),
        };
      }
      if (!mounted.current) return;
      setStates((current) => ({ ...current, [id]: { kind: 'success', data } }));
      if (announce) setAnnouncement(`${cardMeta[id].label} updated.`);
    } catch (error) {
      if (!mounted.current) return;
      setStates((current) => ({
        ...current,
        [id]: { kind: 'error', permission: error instanceof HttpError && [401, 403].includes(error.status) },
      }));
      if (announce) setAnnouncement(`${cardMeta[id].label} could not be updated.`);
    }
  }, []);

  useEffect(() => {
    (Object.keys(cardMeta) as CardId[]).forEach((id) => {
      if (id !== 'service' || !initialHealth) void load(id);
    });
  }, [initialHealth, load]);

  return (
    <section className="operator-dashboard" aria-labelledby="operator-dashboard-heading">
      <h2 id="operator-dashboard-heading">System summary</h2>
      <p className="operator-dashboard-intro">Current inventory and recent operational activity. Open a card to investigate or act.</p>
      <div className="operator-dashboard-grid">
        {(Object.keys(cardMeta) as CardId[]).map((id) => {
          const meta = cardMeta[id];
          const state = states[id];
          return (
            <article className="operator-dashboard-card" key={id} aria-labelledby={`dashboard-${id}`}>
              <h3 id={`dashboard-${id}`}>{meta.label}</h3>
              {state.kind === 'loading' ? (
                <div className="dashboard-card-skeleton" aria-label={`Loading ${meta.label}`}><span /><span /></div>
              ) : state.kind === 'error' ? (
                <div className="dashboard-card-error" role="alert">
                  <span className="material-icons" aria-hidden="true">error_outline</span>
                  <p>{state.permission ? 'You don’t have permission to view this summary' : `Couldn’t load ${meta.error}`}</p>
                  {!state.permission && <button type="button" onClick={() => void load(id, true)}>Retry</button>}
                </div>
              ) : (
                <>
                  <div className="dashboard-card-value">{state.data.value}</div>
                  <p className="dashboard-card-status"><span className="material-icons" aria-hidden="true">info</span>{state.data.status}</p>
                  <p className="dashboard-card-freshness">{state.data.freshness}</p>
                </>
              )}
              <a href={meta.href} className="dashboard-card-link">Open {meta.label}<span className="material-icons" aria-hidden="true">arrow_forward</span></a>
            </article>
          );
        })}
      </div>
      <div className="sr-only" role="status" aria-live="polite">{announcement}</div>
    </section>
  );
}
