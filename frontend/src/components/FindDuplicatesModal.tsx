import { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import * as api from '../services/api';
import type { DuplicateGroup, BulkMergeItem } from '../services/api';
import { ModalOverlay } from './ModalOverlay';
import { useOwnedDialog } from '../hooks/useOwnedDialog';
import { IrreversibleActionNotice } from './IrreversibleActionNotice';
import './ModalBase.css';
import './FindDuplicatesModal.css';

interface FindDuplicatesModalProps {
  /**
   * True when opened from Edit Mode's selection toolbar. The merge this modal
   * runs is the same immediate, unstageable operation as the toolbar Merge
   * button's, and needs the same point-of-action notice (bead
   * enhancedchannelmanager-kz089).
   */
  isEditMode?: boolean;
  onClose: () => void;
  onMerged: () => void;
  /** Checkbox-selected channel ids to scope the scan to (enhancedchannelmanager-uahp6).
   *  Undefined or empty scans all channels (global). */
  channelIds?: number[];
}

export function FindDuplicatesModal({ isEditMode = false, onClose, onMerged, channelIds }: FindDuplicatesModalProps) {
  const { titleId, containerRef } = useOwnedDialog();
  // A selection of zero channels isn't a meaningful scope — treat it the
  // same as "no selection passed" (global scan) rather than sending an
  // empty channel_ids that the backend would interpret as "scope to
  // nothing." In practice the caller only opens this modal with a
  // non-empty selection, but this keeps the modal's own contract sane
  // independent of that caller behavior.
  const scopedChannelIds = channelIds && channelIds.length > 0 ? channelIds : undefined;

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // GH #645: opt-in whitespace/case-insensitive grouping ("Eurosport 2" /
  // "Eurosport2" in one group) — same canonicalization as the auto-creation
  // fold_match_key rule flag. Flipping it re-runs the scan.
  const [foldMatchKey, setFoldMatchKey] = useState(false);
  const [groups, setGroups] = useState<DuplicateGroup[]>([]);
  const [totalDuplicates, setTotalDuplicates] = useState(0);

  // Track which channel to keep per group (key = normalized_name, value = channel id)
  const [keepTargets, setKeepTargets] = useState<Record<string, number>>({});
  // Track which groups are included in the merge
  const [includedGroups, setIncludedGroups] = useState<Record<string, boolean>>({});

  const [merging, setMerging] = useState(false);
  // Ticked before Merge will run inside Edit Mode (bead …-kz089).
  const [irreversibleAcknowledged, setIrreversibleAcknowledged] = useState(false);
  const [mergeResult, setMergeResult] = useState<{ merged: number; failed: number } | null>(null);

  // Merge-blind safety net (enhancedchannelmanager-uahp6): a large result set
  // (985 groups observed in the wild) can, under a render-side failure, end
  // up with zero group rows actually mounted in the DOM even though `groups`
  // is non-empty. Merging in that state is sight-unseen — the operator
  // cannot see what they're about to delete. This is a real DOM-presence
  // check (not a data check) so it catches render bugs that a `groups.length`
  // check would miss.
  const groupListRef = useRef<HTMLDivElement | null>(null);
  const [renderGuardTripped, setRenderGuardTripped] = useState(false);

  // Fetch duplicates on mount and whenever the fold toggle flips.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setMergeResult(null);

    api.findDuplicateChannels(scopedChannelIds, foldMatchKey)
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
    // Re-runs only when the fold toggle flips. scopedChannelIds is
    // deliberately NOT a dependency: the parent only ever mounts a fresh
    // instance of this modal on open (conditional render, not a persisted
    // instance), so there is no "prop changed while mounted" case to react to.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [foldMatchKey]);

  // Merge-blind safety net: after the group list (re)renders, verify the
  // DOM actually has a row per group. Deferred one frame so the rows have
  // had a chance to mount before we count them.
  useEffect(() => {
    if (groups.length === 0) {
      setRenderGuardTripped(false);
      return;
    }
    const raf = window.requestAnimationFrame(() => {
      const rowCount = groupListRef.current?.querySelectorAll('.dup-group').length ?? 0;
      setRenderGuardTripped(rowCount === 0);
    });
    return () => window.cancelAnimationFrame(raf);
  }, [groups]);

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
    // Same handler-level refusal as MergeChannelsModal: this merge deletes the
    // duplicate channels, so it must refuse independently of the button's
    // `disabled` expression (bead …-kz089, fix round 2).
    if (isEditMode && !irreversibleAcknowledged) {
      setError('Acknowledge that this merge applies immediately before merging.');
      return;
    }
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
    <ModalOverlay onClose={onClose} role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <div className="modal-container modal-lg find-duplicates-modal" ref={containerRef}>
        <div className="modal-header">
          <div>
            <h2 id={titleId}>
              <span className="material-icons">content_copy</span>
              Find Duplicate Channels
            </h2>
            <div className="modal-subtitle">
              {scopedChannelIds
                ? `Scanned ${scopedChannelIds.length} selected channel${scopedChannelIds.length !== 1 ? 's' : ''}`
                : 'Scanned all channels'}
            </div>
          </div>
          <button className="modal-close-btn" onClick={onClose} title="Close" aria-label="Close">
            <span className="material-icons" aria-hidden="true">close</span>
          </button>
        </div>

        <div className="modal-body">
          {renderGuardTripped && (
            <div className="modal-error-banner dup-render-guard-banner">
              <span className="material-icons">warning</span>
              Found {groups.length} duplicate group{groups.length !== 1 ? 's' : ''}, but they failed
              to render. Merging is disabled until you can see what you're about to merge — close
              and re-run Find Duplicates, or report this issue if it persists.
            </div>
          )}

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

          <div className="dup-fold-toggle">
            <label className="modal-checkbox-label" title={
              'Also group names that differ only by spacing or case '
              + '(e.g. "Eurosport 2" and "Eurosport2"). Names are only compared this way — '
              + 'they are never renamed. Uses the same matching as the auto-creation rule option '
              + '"Ignore spacing & case differences when matching".'
            }>
              <input
                type="checkbox"
                checked={foldMatchKey}
                onChange={e => setFoldMatchKey(e.target.checked)}
                disabled={loading || merging}
                aria-label="Ignore spacing differences"
              />
              <span>Ignore spacing differences</span>
            </label>
          </div>

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

              <div className="dup-group-list" ref={groupListRef}>
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
          {isEditMode && groups.length > 0 && !mergeResult && (
            <IrreversibleActionNotice
              what="This merge"
              consequence="Each group's source channels are deleted once their streams have moved."
              acknowledged={irreversibleAcknowledged}
              onAcknowledgedChange={setIrreversibleAcknowledged}
            />
          )}
          <button className="modal-btn modal-btn-secondary" onClick={onClose}>
            Cancel
          </button>
          {groups.length > 0 && !mergeResult && (
            <button
              className="modal-btn modal-btn-primary"
              onClick={handleMerge}
              disabled={merging || includedCount === 0 || renderGuardTripped || (isEditMode && !irreversibleAcknowledged)}
              title={renderGuardTripped ? 'Merge disabled — duplicate groups failed to render' : undefined}
            >
              {merging ? 'Merging...' : `Merge ${includedCount} Group${includedCount !== 1 ? 's' : ''}`}
            </button>
          )}
        </div>
      </div>
    </ModalOverlay>
  );
}
