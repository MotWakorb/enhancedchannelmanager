import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from '../../services/api';
import type { SourceLoadState } from '../sourceLoadState';
import { HttpError } from '../../services/httpClient';
import './OperatorDashboard.css';

type CardId = 'service' | 'lineup' | 'sources' | 'changes' | 'tasks' | 'journal';
type CardData = { value: React.ReactNode; status: string; freshness: string };
type CardState = { kind: 'loading' } | { kind: 'success'; data: CardData } | { kind: 'error'; permission: boolean };
export type DashboardSnapshot<T> = {
  value: T;
  state: SourceLoadState;
  hasSnapshot: boolean;
  retry: () => void;
};
export interface OperatorDashboardProps {
  health: DashboardSnapshot<api.HealthResponse | null>;
  channels: DashboardSnapshot<number>;
  streams: DashboardSnapshot<number>;
  providers: DashboardSnapshot<number>;
}

const cardMeta: Record<CardId, { label: string; href: string; error: string }> = {
  service: { label: 'ECM service', href: '#settings/general', error: 'ECM service status' },
  lineup: { label: 'Lineup inventory', href: '#channel-manager', error: 'lineup inventory' },
  sources: { label: 'Source accounts', href: '#m3u-manager', error: 'source accounts' },
  changes: { label: 'Recent M3U changes', href: '#m3u-changes?hours=24', error: 'recent M3U changes' },
  tasks: { label: 'Scheduled work', href: '#settings/scheduled-tasks', error: 'scheduled tasks' },
  journal: { label: 'Recent journal', href: '#journal', error: 'journal summary' },
};

const checked = () => `Checked ${new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(new Date())}`;
const asError = (state: SourceLoadState): CardState => ({
  kind: 'error', permission: state === 'permission',
});

function useRemoteCard(loader: () => Promise<CardData>, label: string) {
  const [state, setState] = useState<CardState>({ kind: 'loading' });
  const [announcement, setAnnouncement] = useState('');
  const generation = useRef(0);
  const inFlight = useRef<Promise<CardData> | null>(null);

  const load = useCallback((announce = false) => {
    const request = inFlight.current ?? loader();
    inFlight.current = request;
    const current = ++generation.current;
    setState({ kind: 'loading' });
    request.then((data) => {
      if (generation.current !== current) return;
      setState({ kind: 'success', data });
      if (announce) setAnnouncement(`${label} updated.`);
    }).catch((error: unknown) => {
      if (generation.current !== current) return;
      setState({ kind: 'error', permission: error instanceof HttpError && [401, 403].includes(error.status) });
      if (announce) setAnnouncement(`${label} could not be updated.`);
    }).finally(() => {
      if (inFlight.current === request) inFlight.current = null;
    });
  }, [label, loader]);

  useEffect(() => {
    load();
    return () => { generation.current += 1; };
  }, [load]);
  return { state, retry: () => load(true), announcement };
}

const loadChanges = async (): Promise<CardData> => {
  const summary = await api.getM3UChangesSummary({ hours: 24 });
  return {
    value: `${summary.total_changes} ${summary.total_changes === 1 ? 'change' : 'changes'}`,
    status: summary.total_changes === 0 ? 'No recent M3U changes' : `${summary.streams_added} streams added · ${summary.streams_removed} removed`,
    freshness: `Since ${new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(summary.since))}`,
  };
};
const loadTasks = async (): Promise<CardData> => {
  const { tasks } = await api.getTasks();
  const enabled = tasks.filter((task) => task.effective_enabled ?? task.enabled).length;
  const running = tasks.filter((task) => task.status === 'running').length;
  const failed = tasks.filter((task) => task.status === 'failed').length;
  const runTimes = tasks.map((task) => task.last_run).filter((value): value is string => Boolean(value)).sort();
  const latest = runTimes[runTimes.length - 1];
  return {
    value: `${enabled} enabled`,
    status: tasks.length === 0 ? 'No scheduled tasks configured' : `${running} running · ${failed} failed`,
    freshness: latest ? `Last run ${new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(latest))}` : checked(),
  };
};
const loadJournal = async (): Promise<CardData> => {
  const stats = await api.getJournalStats();
  const categories = Object.keys(stats.by_category).length;
  return {
    value: `${stats.total_entries} ${stats.total_entries === 1 ? 'entry' : 'entries'}`,
    status: stats.total_entries === 0 ? 'No journal entries' : `${categories} ${categories === 1 ? 'category' : 'categories'}`,
    freshness: stats.date_range.newest
      ? `Latest ${new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(stats.date_range.newest))}` : checked(),
  };
};

export function OperatorDashboard({ health, channels, streams, providers }: OperatorDashboardProps) {
  const changes = useRemoteCard(loadChanges, cardMeta.changes.label);
  const tasks = useRemoteCard(loadTasks, cardMeta.tasks.label);
  const journal = useRemoteCard(loadJournal, cardMeta.journal.label);
  const healthData = health.value;
  const states: Record<CardId, CardState> = {
    service: health.state === 'success' && healthData ? { kind: 'success', data: {
      value: healthData.status || 'Unknown',
      status: `${healthData.service || 'ECM'} ${healthData.version || 'Version unavailable'}${healthData.release_channel ? ` · ${healthData.release_channel}` : ''}`,
      freshness: 'Loaded this session',
    } } : health.state === 'loading' ? { kind: 'loading' } : asError(health.state),
    lineup: channels.state === 'loading' || streams.state === 'loading' ? { kind: 'loading' }
      : channels.state !== 'success' ? asError(channels.state)
      : streams.state !== 'success' ? asError(streams.state)
      : { kind: 'success', data: {
        value: <><span>{channels.value} {channels.value === 1 ? 'channel' : 'channels'}</span><span>{streams.value} {streams.value === 1 ? 'stream' : 'streams'}</span></>,
        status: channels.value + streams.value === 0 ? 'No lineup configured' : 'Inventory available',
        freshness: 'Loaded this session',
      } },
    sources: providers.state === 'success' ? { kind: 'success', data: {
      value: `${providers.value} ${providers.value === 1 ? 'account' : 'accounts'}`,
      status: providers.value === 0 ? 'No M3U accounts configured' : 'Configured sources',
      freshness: 'Loaded this session',
    } } : providers.state === 'loading' ? { kind: 'loading' } : asError(providers.state),
    changes: changes.state, tasks: tasks.state, journal: journal.state,
  };
  const retries: Record<CardId, () => void> = {
    service: health.retry,
    lineup: () => {
      if (channels.state !== 'success') channels.retry();
      if (streams.state !== 'success') streams.retry();
    },
    sources: providers.retry,
    changes: changes.retry, tasks: tasks.retry, journal: journal.retry,
  };

  return (
    <section className="operator-dashboard" aria-labelledby="operator-dashboard-heading">
      <h2 id="operator-dashboard-heading">System summary</h2>
      <p className="operator-dashboard-intro">Current inventory and recent operational activity. Open a card to investigate or act.</p>
      <div className="operator-dashboard-grid">
        {(Object.keys(cardMeta) as CardId[]).map((id) => {
          const meta = cardMeta[id];
          const state = states[id];
          return <article className="operator-dashboard-card" key={id} aria-labelledby={`dashboard-${id}`}>
            <h3 id={`dashboard-${id}`}>{meta.label}</h3>
            {state.kind === 'loading' ? <div className="dashboard-card-skeleton" aria-label={`Loading ${meta.label}`}><span /><span /></div>
              : state.kind === 'error' ? <div className="dashboard-card-error" role="alert">
                <span className="material-icons" aria-hidden="true">error_outline</span>
                <p>{state.permission ? 'You don’t have permission to view this summary' : `Couldn’t load ${meta.error}`}</p>
                {!state.permission && <button type="button" onClick={retries[id]}>Retry</button>}
              </div>
              : <><div className="dashboard-card-value">{state.data.value}</div><p className="dashboard-card-status"><span className="material-icons" aria-hidden="true">info</span>{state.data.status}</p><p className="dashboard-card-freshness">{state.data.freshness}</p></>}
            {state.kind === 'error' && state.permission
              ? <span className="dashboard-card-link dashboard-card-link-unavailable">Destination unavailable with current permissions</span>
              : <a href={meta.href} className="dashboard-card-link">Open {meta.label}<span className="material-icons" aria-hidden="true">arrow_forward</span></a>}
          </article>;
        })}
      </div>
      <div className="sr-only" role="status" aria-live="polite">{[changes.announcement, tasks.announcement, journal.announcement].filter(Boolean).pop()}</div>
    </section>
  );
}
