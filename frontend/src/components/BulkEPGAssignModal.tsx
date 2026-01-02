/**
 * Bulk EPG Assignment Modal
 *
 * Allows users to assign EPG data to multiple selected channels at once.
 * Features country-aware matching and conflict resolution.
 */

import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import type { Channel, Stream, EPGData, EPGSource } from '../types';
import {
  batchFindEPGMatchesAsync,
  getEPGSourceName,
  type EPGMatchResult,
  type EPGAssignment,
  type BatchMatchProgress,
} from '../utils/epgMatching';
import './BulkEPGAssignModal.css';

export type { EPGAssignment };

interface BulkEPGAssignModalProps {
  isOpen: boolean;
  selectedChannels: Channel[];
  streams: Stream[];
  epgData: EPGData[];
  epgSources: EPGSource[];
  onClose: () => void;
  onAssign: (assignments: EPGAssignment[]) => void;
}

type Phase = 'analyzing' | 'review';

export function BulkEPGAssignModal({
  isOpen,
  selectedChannels,
  streams,
  epgData,
  epgSources,
  onClose,
  onAssign,
}: BulkEPGAssignModalProps) {
  const [phase, setPhase] = useState<Phase>('analyzing');
  const [matchResults, setMatchResults] = useState<EPGMatchResult[]>([]);
  const [conflictResolutions, setConflictResolutions] = useState<Map<number, EPGData | null>>(new Map());
  const [autoMatchedExpanded, setAutoMatchedExpanded] = useState(true);
  const [unmatchedExpanded, setUnmatchedExpanded] = useState(true);
  const [currentConflictIndex, setCurrentConflictIndex] = useState(0);
  const [showConflictReview, setShowConflictReview] = useState(false);
  const [progress, setProgress] = useState<BatchMatchProgress | null>(null);

  // Track if we've already analyzed for this modal session
  const hasAnalyzedRef = useRef(false);

  // Run matching when modal opens
  useEffect(() => {
    if (!isOpen) {
      // Reset state when modal closes
      setPhase('analyzing');
      setMatchResults([]);
      setConflictResolutions(new Map());
      setAutoMatchedExpanded(true);
      setUnmatchedExpanded(true);
      setCurrentConflictIndex(0);
      setShowConflictReview(false);
      setProgress(null);
      hasAnalyzedRef.current = false;
      return;
    }

    // Only run analysis once per modal open
    if (hasAnalyzedRef.current) {
      return;
    }

    // Start analysis
    setPhase('analyzing');
    hasAnalyzedRef.current = true;

    // Run async analysis
    const runAnalysis = async () => {
      try {
        console.log('[BulkEPGAssign] Running analysis...');
        console.log('[BulkEPGAssign] Selected channels:', selectedChannels.length);
        console.log('[BulkEPGAssign] Available streams:', streams.length);
        console.log('[BulkEPGAssign] EPG data entries:', epgData.length);

        // Early exit if no channels selected
        if (selectedChannels.length === 0) {
          console.log('[BulkEPGAssign] No channels selected, skipping analysis');
          setMatchResults([]);
          setPhase('review');
          return;
        }

        const results = await batchFindEPGMatchesAsync(
          selectedChannels,
          streams,
          epgData,
          (prog) => setProgress(prog)
        );
        console.log('[BulkEPGAssign] Match results:', results);
        const autoCount = results.filter(r => r.status === 'exact').length;
        const conflictCount = results.filter(r => r.status === 'multiple').length;
        const unmatchedCount = results.filter(r => r.status === 'none').length;
        console.log(`[BulkEPGAssign] Summary: ${autoCount} auto, ${conflictCount} conflicts, ${unmatchedCount} unmatched`);
        setMatchResults(results);
        setPhase('review');
      } catch (error) {
        console.error('[BulkEPGAssign] Analysis failed:', error);
        // Still transition to review phase so UI doesn't hang
        setMatchResults([]);
        setPhase('review');
      }
    };

    runAnalysis();
  }, [isOpen, selectedChannels, streams, epgData]);

  // Categorize results and sort A-Z by channel name
  const { autoMatched, conflicts, unmatched } = useMemo(() => {
    const auto: EPGMatchResult[] = [];
    const conf: EPGMatchResult[] = [];
    const none: EPGMatchResult[] = [];

    for (const result of matchResults) {
      if (result.status === 'exact') {
        auto.push(result);
      } else if (result.status === 'multiple') {
        conf.push(result);
      } else {
        none.push(result);
      }
    }

    // Sort each category alphabetically by channel name (A-Z)
    const sortByChannelName = (a: EPGMatchResult, b: EPGMatchResult) =>
      a.channel.name.localeCompare(b.channel.name);

    return {
      autoMatched: auto.sort(sortByChannelName),
      conflicts: conf.sort(sortByChannelName),
      unmatched: none.sort(sortByChannelName),
    };
  }, [matchResults]);

  // Handle conflict resolution selection
  const handleConflictSelect = useCallback((channelId: number, epgData: EPGData | null) => {
    setConflictResolutions(prev => {
      const next = new Map(prev);
      next.set(channelId, epgData);
      return next;
    });
  }, []);

  // Navigate to next/previous conflict
  const handleNextConflict = useCallback(() => {
    setCurrentConflictIndex(prev => Math.min(prev + 1, conflicts.length - 1));
  }, [conflicts.length]);

  const handlePrevConflict = useCallback(() => {
    setCurrentConflictIndex(prev => Math.max(prev - 1, 0));
  }, []);

  // Get recommended EPG for a result (first match with matching country, or first match)
  const getRecommendedEpg = useCallback((result: EPGMatchResult): EPGData | null => {
    if (result.matches.length === 0) return null;
    // matches are already sorted with matching country first
    return result.matches[0];
  }, []);

  // Accept all recommended matches for unresolved conflicts
  const handleAcceptAllRecommended = useCallback(() => {
    setConflictResolutions(prev => {
      const next = new Map(prev);
      for (const result of conflicts) {
        // Only set if not already resolved
        if (!next.has(result.channel.id)) {
          const recommended = result.matches[0]; // First match is recommended
          if (recommended) {
            next.set(result.channel.id, recommended);
          }
        }
      }
      return next;
    });
  }, [conflicts]);

  // Count unresolved conflicts
  const unresolvedCount = useMemo(() => {
    return conflicts.filter(c => !conflictResolutions.has(c.channel.id)).length;
  }, [conflicts, conflictResolutions]);

  // Count how many assignments will be made
  const assignmentCount = useMemo(() => {
    let count = autoMatched.length;
    for (const [, selected] of conflictResolutions) {
      if (selected !== null) {
        count++;
      }
    }
    return count;
  }, [autoMatched, conflictResolutions]);

  // Handle assign button click
  const handleAssign = useCallback(() => {
    const assignments: EPGAssignment[] = [];

    // Add auto-matched channels
    for (const result of autoMatched) {
      const match = result.matches[0];
      assignments.push({
        channelId: result.channel.id,
        channelName: result.channel.name,
        tvg_id: match.tvg_id,
        epg_data_id: match.id,
      });
    }

    // Add resolved conflicts
    for (const [channelId, selected] of conflictResolutions) {
      if (selected) {
        const channel = selectedChannels.find(c => c.id === channelId);
        if (channel) {
          assignments.push({
            channelId,
            channelName: channel.name,
            tvg_id: selected.tvg_id,
            epg_data_id: selected.id,
          });
        }
      }
    }

    onAssign(assignments);
  }, [autoMatched, conflictResolutions, selectedChannels, onAssign]);

  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="bulk-epg-modal" onClick={e => e.stopPropagation()}>
        <div className="bulk-epg-header">
          <h2>Bulk EPG Assignment</h2>
          <button className="modal-close-btn" onClick={onClose}>
            <span className="material-icons">close</span>
          </button>
        </div>

        <div className="bulk-epg-body">
          {phase === 'analyzing' ? (
            <div className="bulk-epg-analyzing">
              <span className="material-icons spinning">sync</span>
              <div className="analyzing-text">
                <p>Analyzing {selectedChannels.length} channels...</p>
                {progress && (
                  <div className="analyzing-progress">
                    <div className="progress-bar">
                      <div
                        className="progress-fill"
                        style={{ width: `${(progress.current / progress.total) * 100}%` }}
                      />
                    </div>
                    <p className="progress-detail">
                      {progress.current} / {progress.total}: {progress.channelName}
                    </p>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <>
              {/* Summary */}
              <div className="bulk-epg-summary">
                <div className="summary-item success">
                  <span className="material-icons">check_circle</span>
                  <span>{autoMatched.length} matched</span>
                </div>
                <div className="summary-item warning">
                  <span className="material-icons">help</span>
                  <span>{conflicts.length} need review</span>
                </div>
                <div className="summary-item neutral">
                  <span className="material-icons">remove_circle_outline</span>
                  <span>{unmatched.length} unmatched</span>
                </div>
              </div>

              {/* No EPG data warning */}
              {epgData.length === 0 && (
                <div className="bulk-epg-warning">
                  <span className="material-icons">warning</span>
                  <p>No EPG data available. Load EPG sources in the EPG Manager tab first.</p>
                </div>
              )}

              {/* Choice prompt when there are conflicts and user hasn't chosen yet */}
              {conflicts.length > 0 && !showConflictReview && (
                <div className="bulk-epg-choice">
                  <p>There are {conflicts.length} channels with multiple EPG matches. How would you like to proceed?</p>
                  <div className="choice-buttons">
                    <button
                      className="choice-btn choice-review"
                      onClick={() => setShowConflictReview(true)}
                    >
                      <span className="material-icons">rate_review</span>
                      <div className="choice-content">
                        <span className="choice-title">Review Changes</span>
                        <span className="choice-desc">Manually select the best match for each channel</span>
                      </div>
                    </button>
                    <button
                      className="choice-btn choice-accept"
                      onClick={handleAcceptAllRecommended}
                    >
                      <span className="material-icons">done_all</span>
                      <div className="choice-content">
                        <span className="choice-title">Accept Best Guesses</span>
                        <span className="choice-desc">Use the recommended match for all conflicts</span>
                      </div>
                    </button>
                  </div>
                </div>
              )}

              {/* Conflicts Section - only show when reviewing */}
              {conflicts.length > 0 && showConflictReview && (
                <div className="bulk-epg-section conflicts-section">
                  <div className="section-header conflicts-header">
                    <div className="conflicts-title">
                      <span className="material-icons">help</span>
                      Needs Review ({conflicts.length})
                    </div>
                    <div className="conflicts-actions">
                      {unresolvedCount > 0 && (
                        <button
                          className="accept-all-btn"
                          onClick={handleAcceptAllRecommended}
                          title="Accept recommended match for all unresolved conflicts"
                        >
                          <span className="material-icons">done_all</span>
                          Accept All
                        </button>
                      )}
                      <div className="conflicts-nav">
                        <button
                          className="nav-btn"
                          onClick={handlePrevConflict}
                          disabled={currentConflictIndex === 0}
                          title="Previous"
                        >
                          <span className="material-icons">chevron_left</span>
                        </button>
                        <span className="nav-counter">{currentConflictIndex + 1} / {conflicts.length}</span>
                        <button
                          className="nav-btn"
                          onClick={handleNextConflict}
                          disabled={currentConflictIndex === conflicts.length - 1}
                          title="Next"
                        >
                          <span className="material-icons">chevron_right</span>
                        </button>
                      </div>
                    </div>
                  </div>
                  <div className="conflicts-list">
                    {conflicts.map((result, index) => (
                      <ConflictItem
                        key={result.channel.id}
                        result={result}
                        epgSources={epgSources}
                        selectedEpg={conflictResolutions.get(result.channel.id)}
                        onSelect={epg => handleConflictSelect(result.channel.id, epg)}
                        isExpanded={index === currentConflictIndex}
                        onToggle={() => setCurrentConflictIndex(index)}
                        recommendedEpg={getRecommendedEpg(result)}
                        isResolved={conflictResolutions.has(result.channel.id)}
                      />
                    ))}
                  </div>
                </div>
              )}

              {/* Auto-Matched Section (Collapsible) */}
              {autoMatched.length > 0 && (
                <div className="bulk-epg-section collapsible">
                  <button
                    className="section-header clickable"
                    onClick={() => setAutoMatchedExpanded(!autoMatchedExpanded)}
                  >
                    <span className="material-icons">check_circle</span>
                    Auto-Matched ({autoMatched.length})
                    <span className="material-icons expand-icon">
                      {autoMatchedExpanded ? 'expand_less' : 'expand_more'}
                    </span>
                  </button>
                  {autoMatchedExpanded && (
                    <div className="matched-list">
                      {autoMatched.map(result => (
                        <div key={result.channel.id} className="matched-item">
                          <div className="matched-channel">
                            <span className="channel-name">{result.channel.name}</span>
                            {result.detectedCountry && (
                              <span className="country-badge">{result.detectedCountry.toUpperCase()}</span>
                            )}
                          </div>
                          <span className="material-icons arrow">arrow_forward</span>
                          <div className="matched-epg">
                            <span className="epg-name">{result.matches[0].name}</span>
                            <span className="epg-tvgid">{result.matches[0].tvg_id}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Unmatched Section (Collapsible) */}
              {unmatched.length > 0 && (
                <div className="bulk-epg-section collapsible">
                  <button
                    className="section-header clickable"
                    onClick={() => setUnmatchedExpanded(!unmatchedExpanded)}
                  >
                    <span className="material-icons">remove_circle_outline</span>
                    Unmatched ({unmatched.length})
                    <span className="material-icons expand-icon">
                      {unmatchedExpanded ? 'expand_less' : 'expand_more'}
                    </span>
                  </button>
                  {unmatchedExpanded && (
                    <div className="unmatched-list">
                      {unmatched.map(result => (
                        <div key={result.channel.id} className="unmatched-item">
                          <span className="channel-name">{result.channel.name}</span>
                          {result.detectedCountry && (
                            <span className="country-badge">{result.detectedCountry.toUpperCase()}</span>
                          )}
                          <span className="normalized-name">({result.normalizedName || 'empty'})</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>

        <div className="bulk-epg-footer">
          <button className="btn-cancel" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn-primary"
            onClick={handleAssign}
            disabled={phase === 'analyzing' || assignmentCount === 0}
          >
            Assign {assignmentCount} Channel{assignmentCount !== 1 ? 's' : ''}
          </button>
        </div>
      </div>
    </div>
  );
}

// Conflict resolution item component
interface ConflictItemProps {
  result: EPGMatchResult;
  epgSources: EPGSource[];
  selectedEpg: EPGData | null | undefined;
  onSelect: (epg: EPGData | null) => void;
  isExpanded: boolean;
  onToggle: () => void;
  recommendedEpg: EPGData | null;
  isResolved: boolean;
}

function ConflictItem({ result, epgSources, selectedEpg, onSelect, isExpanded, onToggle, recommendedEpg, isResolved }: ConflictItemProps) {
  return (
    <div className={`conflict-item ${isExpanded ? 'expanded' : 'collapsed'} ${isResolved ? 'resolved' : ''}`}>
      <button className="conflict-header" onClick={onToggle}>
        <div className="conflict-channel">
          <span className="channel-name">{result.channel.name}</span>
          {result.detectedCountry && (
            <span className="country-badge">{result.detectedCountry.toUpperCase()}</span>
          )}
          {isResolved && (
            <span className="resolved-badge">
              <span className="material-icons">check</span>
            </span>
          )}
        </div>
        <span className="material-icons expand-icon">
          {isExpanded ? 'expand_less' : 'expand_more'}
        </span>
      </button>
      {isExpanded && (
        <div className="conflict-body">
          <div className="normalized-label">Normalized: "{result.normalizedName}"</div>
          <div className="conflict-options">
            {result.matches.map(epg => {
              const isRecommended = recommendedEpg?.id === epg.id;
              return (
                <label
                  key={epg.id}
                  className={`conflict-option ${isRecommended ? 'recommended' : ''}`}
                >
                  <input
                    type="radio"
                    name={`conflict-${result.channel.id}`}
                    checked={selectedEpg?.id === epg.id}
                    onChange={() => onSelect(epg)}
                  />
                  <div className="option-content">
                    {epg.icon_url && (
                      <img src={epg.icon_url} alt="" className="epg-icon" />
                    )}
                    <div className="option-info">
                      <span className="epg-name">
                        {epg.name}
                        {isRecommended && <span className="recommended-tag">Recommended</span>}
                      </span>
                      <span className="epg-tvgid">{epg.tvg_id}</span>
                      <span className="epg-source">{getEPGSourceName(epg, epgSources)}</span>
                    </div>
                  </div>
                </label>
              );
            })}
            <label className="conflict-option skip-option">
              <input
                type="radio"
                name={`conflict-${result.channel.id}`}
                checked={selectedEpg === null}
                onChange={() => onSelect(null)}
              />
              <span className="skip-label">Skip this channel</span>
            </label>
          </div>
        </div>
      )}
    </div>
  );
}
