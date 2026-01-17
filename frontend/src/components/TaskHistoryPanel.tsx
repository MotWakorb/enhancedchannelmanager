import { useState, useEffect, useCallback } from 'react';
import * as api from '../services/api';
import type { TaskExecution } from '../services/api';
import { logger } from '../utils/logger';

interface TaskHistoryPanelProps {
  taskId: string;
  visible: boolean;
}

function formatDateTime(isoString: string | null): string {
  if (!isoString) return 'Never';
  const date = new Date(isoString);
  return date.toLocaleString();
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return '-';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

function StatusBadge({ status, success }: { status: string; success: boolean | null }) {
  const getColor = () => {
    if (status === 'running') return '#3498db';
    if (status === 'cancelled') return '#f39c12';
    if (success === true) return '#2ecc71';
    if (success === false) return '#e74c3c';
    return 'var(--text-muted)';
  };

  const getIcon = () => {
    if (status === 'running') return 'sync';
    if (status === 'cancelled') return 'cancel';
    if (success === true) return 'check_circle';
    if (success === false) return 'error';
    return 'help';
  };

  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: '0.25rem',
      color: getColor(),
    }}>
      <span
        className="material-icons"
        style={{
          fontSize: '14px',
          animation: status === 'running' ? 'spin 1s linear infinite' : 'none',
        }}
      >
        {getIcon()}
      </span>
      <span style={{ textTransform: 'capitalize' }}>{status}</span>
    </span>
  );
}

function ExecutionRow({ execution, isExpanded, onToggle }: {
  execution: TaskExecution;
  isExpanded: boolean;
  onToggle: () => void;
}) {
  const hasDetails = execution.message || execution.error || (execution.details && Object.keys(execution.details).length > 0);

  return (
    <>
      <tr
        style={{
          borderTop: '1px solid var(--border-color)',
          cursor: hasDetails ? 'pointer' : 'default',
          backgroundColor: isExpanded ? 'var(--bg-tertiary)' : 'transparent',
        }}
        onClick={hasDetails ? onToggle : undefined}
      >
        <td style={{ padding: '0.5rem 1rem', width: '24px' }}>
          {hasDetails && (
            <span className="material-icons" style={{ fontSize: '16px', color: 'var(--text-muted)' }}>
              {isExpanded ? 'expand_less' : 'expand_more'}
            </span>
          )}
        </td>
        <td style={{ padding: '0.5rem 1rem' }}>{formatDateTime(execution.started_at)}</td>
        <td style={{ padding: '0.5rem 1rem' }}>{formatDuration(execution.duration_seconds)}</td>
        <td style={{ padding: '0.5rem 1rem' }}>
          <StatusBadge status={execution.status} success={execution.success} />
        </td>
        <td style={{ padding: '0.5rem 1rem' }}>
          <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
            {execution.total_items > 0 && (
              <span style={{ color: 'var(--text-secondary)' }}>{execution.total_items} total</span>
            )}
            {execution.success_count > 0 && (
              <span style={{ color: '#2ecc71' }}>{execution.success_count} ok</span>
            )}
            {execution.failed_count > 0 && (
              <span style={{ color: '#e74c3c' }}>{execution.failed_count} failed</span>
            )}
            {execution.skipped_count > 0 && (
              <span style={{ color: '#f39c12' }}>{execution.skipped_count} skipped</span>
            )}
          </div>
        </td>
        <td style={{ padding: '0.5rem 1rem', textTransform: 'capitalize' }}>
          <span style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.25rem',
          }}>
            <span className="material-icons" style={{ fontSize: '14px', color: 'var(--text-muted)' }}>
              {execution.triggered_by === 'scheduled' ? 'schedule' :
               execution.triggered_by === 'manual' ? 'touch_app' : 'api'}
            </span>
            {execution.triggered_by}
          </span>
        </td>
      </tr>
      {isExpanded && hasDetails && (
        <tr>
          <td colSpan={6} style={{ padding: 0 }}>
            <div style={{
              padding: '1rem',
              backgroundColor: 'var(--bg-tertiary)',
              borderTop: '1px dashed var(--border-color)',
            }}>
              {execution.message && (
                <div style={{ marginBottom: '0.75rem' }}>
                  <div style={{
                    fontSize: '0.75rem',
                    color: 'var(--text-muted)',
                    marginBottom: '0.25rem',
                    textTransform: 'uppercase',
                    letterSpacing: '0.5px',
                  }}>
                    Message
                  </div>
                  <div style={{ fontSize: '0.9rem' }}>{execution.message}</div>
                </div>
              )}
              {execution.error && (
                <div style={{ marginBottom: '0.75rem' }}>
                  <div style={{
                    fontSize: '0.75rem',
                    color: '#e74c3c',
                    marginBottom: '0.25rem',
                    textTransform: 'uppercase',
                    letterSpacing: '0.5px',
                  }}>
                    Error
                  </div>
                  <div style={{
                    fontSize: '0.85rem',
                    fontFamily: 'monospace',
                    backgroundColor: 'rgba(231, 76, 60, 0.1)',
                    padding: '0.5rem',
                    borderRadius: '4px',
                    color: '#e74c3c',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                  }}>
                    {execution.error}
                  </div>
                </div>
              )}
              {execution.details && Object.keys(execution.details).length > 0 && (
                <div>
                  <div style={{
                    fontSize: '0.75rem',
                    color: 'var(--text-muted)',
                    marginBottom: '0.25rem',
                    textTransform: 'uppercase',
                    letterSpacing: '0.5px',
                  }}>
                    Details
                  </div>
                  <div style={{
                    fontSize: '0.85rem',
                    fontFamily: 'monospace',
                    backgroundColor: 'var(--bg-secondary)',
                    padding: '0.5rem',
                    borderRadius: '4px',
                    maxHeight: '200px',
                    overflowY: 'auto',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                  }}>
                    {JSON.stringify(execution.details, null, 2)}
                  </div>
                </div>
              )}
              {execution.completed_at && (
                <div style={{
                  marginTop: '0.75rem',
                  fontSize: '0.8rem',
                  color: 'var(--text-muted)',
                }}>
                  Completed: {formatDateTime(execution.completed_at)}
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export function TaskHistoryPanel({ taskId, visible }: TaskHistoryPanelProps) {
  const [history, setHistory] = useState<TaskExecution[]>([]);
  const [loading, setLoading] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const INITIAL_LIMIT = 10;
  const MAX_LIMIT = 50;

  const loadHistory = useCallback(async (limit: number = INITIAL_LIMIT) => {
    if (!visible) return;
    setLoading(true);
    try {
      const result = await api.getTaskHistory(taskId, limit);
      setHistory(result.history);
      setHasMore(result.history.length >= limit && limit < MAX_LIMIT);
    } catch (err) {
      logger.error('Failed to load task history', err);
    } finally {
      setLoading(false);
    }
  }, [taskId, visible]);

  useEffect(() => {
    if (visible) {
      loadHistory();
    }
  }, [visible, loadHistory]);

  const handleLoadMore = () => {
    loadHistory(MAX_LIMIT);
  };

  const toggleExpanded = (id: number) => {
    setExpandedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  if (!visible) return null;

  return (
    <div style={{
      borderTop: '1px solid var(--border-color)',
      maxHeight: '400px',
      overflowY: 'auto',
    }}>
      {loading && history.length === 0 ? (
        <div style={{ padding: '1.5rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
          <span className="material-icons" style={{ fontSize: '24px', animation: 'spin 1s linear infinite', display: 'block', marginBottom: '0.5rem' }}>sync</span>
          Loading history...
        </div>
      ) : history.length === 0 ? (
        <div style={{ padding: '1.5rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
          <span className="material-icons" style={{ fontSize: '32px', display: 'block', marginBottom: '0.5rem' }}>history</span>
          No execution history yet
        </div>
      ) : (
        <>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
            <thead>
              <tr style={{ backgroundColor: 'var(--bg-tertiary)', position: 'sticky', top: 0 }}>
                <th style={{ padding: '0.5rem 1rem', textAlign: 'left', width: '24px' }}></th>
                <th style={{ padding: '0.5rem 1rem', textAlign: 'left' }}>Started</th>
                <th style={{ padding: '0.5rem 1rem', textAlign: 'left' }}>Duration</th>
                <th style={{ padding: '0.5rem 1rem', textAlign: 'left' }}>Status</th>
                <th style={{ padding: '0.5rem 1rem', textAlign: 'left' }}>Result</th>
                <th style={{ padding: '0.5rem 1rem', textAlign: 'left' }}>Trigger</th>
              </tr>
            </thead>
            <tbody>
              {history.map((exec) => (
                <ExecutionRow
                  key={exec.id}
                  execution={exec}
                  isExpanded={expandedIds.has(exec.id)}
                  onToggle={() => toggleExpanded(exec.id)}
                />
              ))}
            </tbody>
          </table>

          {/* Load more button */}
          {hasMore && (
            <div style={{ padding: '1rem', textAlign: 'center' }}>
              <button
                onClick={handleLoadMore}
                disabled={loading}
                style={{
                  padding: '0.5rem 1rem',
                  backgroundColor: 'var(--bg-secondary)',
                  border: '1px solid var(--border-color)',
                  borderRadius: '4px',
                  color: 'var(--text-primary)',
                  cursor: loading ? 'not-allowed' : 'pointer',
                  fontSize: '0.85rem',
                }}
              >
                {loading ? 'Loading...' : `Load more (up to ${MAX_LIMIT})`}
              </button>
            </div>
          )}

          {/* Summary footer */}
          <div style={{
            padding: '0.75rem 1rem',
            borderTop: '1px solid var(--border-color)',
            backgroundColor: 'var(--bg-tertiary)',
            fontSize: '0.8rem',
            color: 'var(--text-muted)',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}>
            <span>Showing {history.length} execution{history.length !== 1 ? 's' : ''}</span>
            {history.length > 0 && (
              <span>
                Success rate: {Math.round((history.filter(h => h.success === true).length / history.length) * 100)}%
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
