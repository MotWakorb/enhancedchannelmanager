import { useEffect, useState, useCallback, useMemo } from 'react';
import * as api from '../services/api';
import type { DuplicateGroup, BulkMergeItem } from '../services/api';
import { ModalOverlay } from './ModalOverlay';
import './ModalBase.css';
import './FindDuplicatesModal.css';

interface FindDuplicatesModalProps {
  onClose: () => void;
  onMerged: () => void;
}

export function FindDuplicatesModal({ onClose, onMerged }: FindDuplicatesModalProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [groups, setGroups] = useState<DuplicateGroup[]>([]);
  const [totalDuplicates, setTotalDuplicates] = useState(0);

  // Track which channel to keep per group (key = normalized_name, value = channel id)
  const [keepTargets, setKeepTargets] = useState<Record<string, number>>({});
  // Track which groups are included in the merge
  const [includedGroups, setIncludedGroups] = useState<Record<string, boolean>>({});

  const [merging, setMerging] = useState(false);
  const [mergeResult, setMergeResult] = useState<{ merged: number; failed: number } | null>(null);

  // Fetch duplicates on mount
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    api.findDuplicateChannels()
      .then(response => {
        if (cancelled) return;
        setGroups(response.groups);
        setTotalDuplicates(response.total_duplicate_channels);

        // Default: keep the first channel in each group (most streams, backend-sorted)
        const targets: Record<string, number> = {};
        const included: Record<string, boolean> = {};
        for (const group of response.groups) {
          targets[group.normalized_name] = group.channels[0].id;
          included[group.normalized_name] = true;
        }
        setKeepTargets(targets);
        setIncludedGroups(included);
        setLoading(false);
      })
      .catch(err => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : 'Failed to find duplicates');
        setLoading(false);
      });

    return () => { cancelled = true; };
  }, []);

  const setKeepTarget = useCallback((groupName: string, channelId: number) => {
    setKeepTargets(prev => ({ ...prev, [groupName]: channelId }));
  }, []);

  const toggleGroup = useCallback((groupName: string) => {
    setIncludedGroups(prev => ({ ...prev, [groupName]: !prev[groupName] }));
  }, []);

  const includedCount = useMemo(
    () => groups.filter(g => includedGroups[g.normalized_name]).length,
    [groups, includedGroups]
  );

  const handleMerge = async () => {
    setMerging(true);
    setError(null);

    const merges: BulkMergeItem[] = groups
      .filter(g => includedGroups[g.normalized_name])
      .map(g => {
        const targetId = keepTargets[g.normalized_name];
        return {
          target_channel_id: targetId,
          source_channel_ids: g.channels
            .filter(ch => ch.id !== targetId)
            .map(ch => ch.id),
        };
      });

    try {
      const result = await api.bulkMergeChannels(merges);
      setMergeResult({ merged: result.merged, failed: result.failed });
      if (result.failed === 0) {
        onMerged();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Merge failed');
    } finally {
      setMerging(false);
    }
  };

  return (
    <ModalOverlay onClose={onClose}>
      <div className="modal-container modal-lg find-duplicates-modal">
        <div className="modal-header">
          <h2>
            <span className="material-icons">content_copy</span>
            Find Duplicate Channels
          </h2>
          <button className="modal-close-btn" onClick={onClose} title="Close">
            <span className="material-icons">close</span>
          </button>
        </div>

        <div className="modal-body">
          {error && (
            <div className="modal-error-banner">
              <span className="material-icons">error</span>
              {error}
            </div>
          )}

          {mergeResult && (
            <div className="dup-merge-result">
              <span className="material-icons">check_circle</span>
              Merged {mergeResult.merged} group{mergeResult.merged !== 1 ? 's' : ''} successfully.
              {mergeResult.failed > 0 && (
                <span className="dup-merge-failed">
                  {' '}{mergeResult.failed} failed.
                </span>
              )}
            </div>
          )}

          {loading ? (
            <div className="loading-state">
              <span className="material-icons spinning">sync</span>
              <p>Scanning for duplicate channels...</p>
            </div>
          ) : groups.length === 0 ? (
            <div className="empty-state">
              <span className="material-icons">check_circle</span>
              <p>No duplicate channels found.</p>
            </div>
          ) : (
            <>
              <div className="dup-summary">
                <span className="material-icons">info</span>
                Found {groups.length} group{groups.length !== 1 ? 's' : ''} with {totalDuplicates} duplicate channels.
                Select which channel to keep in each group.
              </div>

              <div className="dup-group-list">
                {groups.map(group => {
                  const included = includedGroups[group.normalized_name];
                  const targetId = keepTargets[group.normalized_name];

                  return (
                    <div
                      key={group.normalized_name}
                      className={`dup-group ${!included ? 'is-excluded' : ''}`}
                    >
                      <div className="dup-group-header">
                        <label className="modal-checkbox-label">
                          <input
                            type="checkbox"
                            checked={included}
                            onChange={() => toggleGroup(group.normalized_name)}
                          />
                          <span className="dup-group-name">{group.normalized_name}</span>
                        </label>
                        <span className="dup-group-count">
                          {group.channels.length} channels
                        </span>
                      </div>

                      <div className="dup-channel-list">
                        {group.channels.map(ch => (
                          <label
                            key={ch.id}
                            className={`dup-channel-item ${targetId === ch.id ? 'is-target' : ''}`}
                          >
                            <input
                              type="radio"
                              name={`keep-${group.normalized_name}`}
                              checked={targetId === ch.id}
                              onChange={() => setKeepTarget(group.normalized_name, ch.id)}
                              disabled={!included}
                            />
                            <div className="dup-channel-info">
                              <span className="dup-channel-name">{ch.name}</span>
                              <span className="dup-channel-meta">
                                {ch.channel_number != null && (
                                  <span className="dup-channel-number">#{ch.channel_number}</span>
                                )}
                                <span className="dup-channel-streams">
                                  {ch.stream_count} stream{ch.stream_count !== 1 ? 's' : ''}
                                </span>
                                {ch.channel_group_name && (
                                  <span className="dup-channel-group">{ch.channel_group_name}</span>
                                )}
                              </span>
                            </div>
                            {targetId === ch.id && (
                              <span className="dup-keep-badge">keep</span>
                            )}
                          </label>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>

        <div className="modal-footer">
          <button className="modal-btn modal-btn-secondary" onClick={onClose}>
            Cancel
          </button>
          {groups.length > 0 && !mergeResult && (
            <button
              className="modal-btn modal-btn-primary"
              onClick={handleMerge}
              disabled={merging || includedCount === 0}
            >
              {merging ? 'Merging...' : `Merge ${includedCount} Group${includedCount !== 1 ? 's' : ''}`}
            </button>
          )}
        </div>
      </div>
    </ModalOverlay>
  );
}
