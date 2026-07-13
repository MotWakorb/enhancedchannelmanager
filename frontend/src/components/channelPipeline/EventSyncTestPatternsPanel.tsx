/**
 * Test Patterns panel for the Event Sync rule editor (bead ti939.1.5).
 *
 * Paste sample stream names (or fetch live samples from the rule's groups)
 * and see, per parse pattern, exactly what title / date / time each pattern
 * extracts. Extraction runs server-side through the dummy-EPG preview
 * endpoint (`POST /api/dummy-epg/preview/batch`) — the same
 * `extract_groups` → `safe_regex` machinery the Event Sync matcher uses at
 * preview/run time, so what this table shows is what the matcher sees.
 * The "Parsed" verdict comes from the backend's matcher-level
 * `event_sync_start_valid` flag (bead hirm6): Event Sync never guesses
 * start times, so a name whose date/time groups are missing, OR captured
 * but invalid ("45 Jul", a garbage month, hour past 23), is flagged as a
 * parse failure — never shown as parsed.
 */
import { useId, useState } from 'react';
import type { EventSyncPattern } from '../../types/eventSync';
import { getStreams, previewDummyEPGBatch } from '../../services/api';
import { CustomSelect } from '../CustomSelect';
import './EventSyncTestPatternsPanel.css';

/** Backend batch-preview cap (dummy_epg router slices at 100). */
const MAX_SAMPLE_NAMES = 100;

/** Page size when pulling live sample names from a group. */
const LIVE_SAMPLE_COUNT = 20;

export interface LabeledEventSyncPattern {
  label: string;
  pattern: EventSyncPattern;
}

export interface EventSyncTestPatternsPanelProps {
  /** Patterns to test — the editor's effective selection, in order. */
  patterns: LabeledEventSyncPattern[];
  /** Groups the rule scopes to (master + secondaries), for live samples. */
  groupOptions: { id: number; name: string }[];
  /**
   * Fires after each test run with whether any sample was a parse failure
   * (no match, or matched but the matcher would reject the start time). The
   * editor uses this to auto-expand the collapsed Test Patterns panel so the
   * failures are never hidden below the fold.
   */
  onParseFailuresChange?: (hasParseFailures: boolean) => void;
}

interface PatternRow {
  name: string;
  matched: boolean;
  title: string | null;
  date: string | null;
  time: string | null;
  /** All of title + date + time groups were captured. */
  hasAllParts: boolean;
  /**
   * Backend matcher-level verdict: the matcher would actually build a
   * start time (valid month, hour <= 23, real calendar date).
   */
  startValid: boolean;
}

interface PatternResult {
  label: string;
  rows: PatternRow[];
}

function formatDateParts(groups: Record<string, string | null>): string | null {
  const day = groups.day;
  const month = groups.month;
  if (!day || !month) return null;
  const year = groups.year;
  return year ? `${day} ${month} ${year}` : `${day} ${month}`;
}

function formatTimeParts(groups: Record<string, string | null>): string | null {
  const hour = groups.hour;
  const minute = groups.minute;
  if (!hour || !minute) return null;
  const ampm = groups.ampm;
  return ampm ? `${hour}:${minute} ${ampm.toUpperCase()}M` : `${hour}:${minute}`;
}

export function EventSyncTestPatternsPanel({
  patterns,
  groupOptions,
  onParseFailuresChange,
}: EventSyncTestPatternsPanelProps) {
  const id = useId();
  const [sampleText, setSampleText] = useState('');
  const [fetchGroupId, setFetchGroupId] = useState('');
  const [fetching, setFetching] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<PatternResult[] | null>(null);

  const sampleNames = sampleText
    .split('\n')
    .map(line => line.trim())
    .filter(line => line.length > 0)
    .slice(0, MAX_SAMPLE_NAMES);

  const handleFetchSamples = async () => {
    const group = groupOptions.find(g => g.id.toString() === fetchGroupId);
    if (!group) return;
    setFetching(true);
    setError(null);
    try {
      const response = await getStreams({ channelGroup: group.name, pageSize: LIVE_SAMPLE_COUNT });
      const names = response.results.map(s => s.name).filter(Boolean);
      if (names.length === 0) {
        setError(`No streams found in group "${group.name}"`);
        return;
      }
      setSampleText(prev => {
        const existing = new Set(
          prev.split('\n').map(l => l.trim()).filter(Boolean)
        );
        const fresh = names.filter(n => !existing.has(n));
        return [prev.trimEnd(), ...fresh].filter(Boolean).join('\n');
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch sample streams');
    } finally {
      setFetching(false);
    }
  };

  const handleTest = async () => {
    if (sampleNames.length === 0 || patterns.length === 0) return;
    setTesting(true);
    setError(null);
    try {
      const perPattern = await Promise.all(
        patterns.map(async ({ label, pattern }) => {
          const previews = await previewDummyEPGBatch({
            sample_names: sampleNames,
            title_pattern: pattern.title_pattern,
            time_pattern: pattern.time_pattern,
            date_pattern: pattern.date_pattern,
          });
          const rows: PatternRow[] = previews.map((p, i) => {
            const groups = (p.groups ?? {}) as Record<string, string | null>;
            const title = groups.title || null;
            const date = formatDateParts(groups);
            const time = formatTimeParts(groups);
            return {
              name: sampleNames[i],
              matched: p.matched,
              title,
              date,
              time,
              hasAllParts: Boolean(title && date && time),
              startValid: Boolean(p.event_sync_start_valid),
            };
          });
          return { label, rows };
        })
      );
      setResults(perPattern);
      // A row is a parse failure when the matcher would not build a valid
      // start time from it (no match, or matched-but-invalid). Surface it so
      // the editor can auto-expand the panel.
      const hasParseFailures = perPattern.some(result =>
        result.rows.some(row => !(row.matched && row.startValid))
      );
      onParseFailuresChange?.(hasParseFailures);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Pattern test failed');
      setResults(null);
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="event-sync-test-patterns" data-testid="event-sync-test-patterns">
      <p className="form-hint">
        Paste sample stream names (one per line) or fetch live samples from a
        selected group, then run the test to see what each pattern extracts.
        Extraction runs on the server with the exact machinery the matcher
        uses.
      </p>

      <div className="form-group">
        <label htmlFor={`${id}-samples`}>Sample stream names</label>
        <textarea
          id={`${id}-samples`}
          value={sampleText}
          onChange={e => setSampleText(e.target.value)}
          rows={5}
          placeholder={'Fubo Sports Network 07 : Yankees vs Red Sox @ 11 Jul 06:00 PM ET\nPeacock 14: Lyon vs Marseille @ Jan 17 02:45 PM ET'}
        />
        <span className="form-hint">
          {sampleNames.length} name{sampleNames.length === 1 ? '' : 's'} (max {MAX_SAMPLE_NAMES})
        </span>
      </div>

      <div className="test-patterns-fetch-row">
        <div className="test-patterns-fetch-select">
          <CustomSelect
            value={fetchGroupId}
            onChange={setFetchGroupId}
            options={groupOptions.map(g => ({ value: g.id.toString(), label: g.name }))}
            placeholder="Pick a group to sample from"
            searchable
            searchPlaceholder="Search groups..."
            disabled={groupOptions.length === 0}
          />
        </div>
        <button
          type="button"
          className="btn-secondary"
          onClick={handleFetchSamples}
          disabled={!fetchGroupId || fetching}
        >
          <span className={`material-icons ${fetching ? 'spinning' : ''}`}>
            {fetching ? 'sync' : 'download'}
          </span>
          {fetching ? 'Fetching...' : 'Fetch live samples'}
        </button>
        <button
          type="button"
          className="btn-primary"
          onClick={handleTest}
          disabled={sampleNames.length === 0 || patterns.length === 0 || testing}
        >
          <span className={`material-icons ${testing ? 'spinning' : ''}`}>
            {testing ? 'sync' : 'science'}
          </span>
          {testing ? 'Testing...' : 'Test patterns'}
        </button>
      </div>

      {error && (
        <div className="warning-message" role="alert">
          <span className="material-icons">warning</span>
          {error}
        </div>
      )}

      {results && results.map(result => (
        <div key={result.label} className="test-patterns-result">
          <table className="test-patterns-table">
            <caption>{result.label}</caption>
            <thead>
              <tr>
                <th scope="col">Raw name</th>
                <th scope="col">Title</th>
                <th scope="col">Date</th>
                <th scope="col">Time</th>
                <th scope="col">Parse status</th>
              </tr>
            </thead>
            <tbody>
              {/* Key includes the index: pasted sample names may repeat. */}
              {result.rows.map((row, rowIndex) => (
                <tr key={`${rowIndex}-${row.name}`}>
                  <td className="test-patterns-raw">{row.name}</td>
                  <td>{row.title ?? '—'}</td>
                  <td>{row.date ?? '—'}</td>
                  <td>{row.time ?? '—'}</td>
                  <td>
                    {row.matched && row.startValid ? (
                      <span className="test-patterns-status">
                        <span className="material-icons" aria-hidden="true">check_circle</span>
                        Parsed
                      </span>
                    ) : row.matched && row.hasAllParts ? (
                      <span className="test-patterns-status">
                        <span className="material-icons" aria-hidden="true">warning</span>
                        Invalid date/time — not a real calendar date/time, so
                        this would be a parse failure (start times are never
                        guessed)
                      </span>
                    ) : row.matched ? (
                      <span className="test-patterns-status">
                        <span className="material-icons" aria-hidden="true">warning</span>
                        Incomplete date/time — would be a parse failure (start
                        times are never guessed)
                      </span>
                    ) : (
                      <span className="test-patterns-status">
                        <span className="material-icons" aria-hidden="true">cancel</span>
                        No match
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}
