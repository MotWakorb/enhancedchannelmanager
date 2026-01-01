/**
 * Bulk EPG Assignment Modal
 *
 * Allows users to assign EPG data to multiple selected channels at once.
 * Features country-aware matching and conflict resolution.
 */

import { useState, useEffect, useMemo, useCallback } from 'react';
import type { Channel, Stream, EPGData, EPGSource } from '../types';
import {
  batchFindEPGMatches,
  getEPGSourceName,
  type EPGMatchResult,
  type EPGAssignment,
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
  const [autoMatchedExpanded, setAutoMatchedExpanded] = useState(false);
  const [unmatchedExpanded, setUnmatchedExpanded] = useState(false);

  // Run matching when modal opens
  useEffect(() => {
    if (!isOpen) {
      // Reset state when modal closes
      setPhase('analyzing');
      setMatchResults([]);
      setConflictResolutions(new Map());
      setAutoMatchedExpanded(false);
      setUnmatchedExpanded(false);
      return;
    }

    // Start analysis
    setPhase('analyzing');

    // Use setTimeout to allow UI to render "analyzing" state
    const timer = setTimeout(() => {
      const results = batchFindEPGMatches(selectedChannels, streams, epgData);
      setMatchResults(results);
      setPhase('review');
    }, 100);

    return () => clearTimeout(timer);
  }, [isOpen, selectedChannels, streams, epgData]);

  // Categorize results
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

    return { autoMatched: auto, conflicts: conf, unmatched: none };
  }, [matchResults]);

  // Handle conflict resolution selection
  const handleConflictSelect = useCallback((channelId: number, epgData: EPGData | null) => {
    setConflictResolutions(prev => {
      const next = new Map(prev);
      next.set(channelId, epgData);
      return next;
    });
  }, []);

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
              <p>Analyzing {selectedChannels.length} channels...</p>
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

              {/* Conflicts Section */}
              {conflicts.length > 0 && (
                <div className="bulk-epg-section">
                  <h3 className="section-header">
                    <span className="material-icons">help</span>
                    Needs Review ({conflicts.length})
                  </h3>
                  <div className="conflicts-list">
                    {conflicts.map(result => (
                      <ConflictItem
                        key={result.channel.id}
                        result={result}
                        epgSources={epgSources}
                        selectedEpg={conflictResolutions.get(result.channel.id)}
                        onSelect={epg => handleConflictSelect(result.channel.id, epg)}
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
}

function ConflictItem({ result, epgSources, selectedEpg, onSelect }: ConflictItemProps) {
  return (
    <div className="conflict-item">
      <div className="conflict-channel">
        <span className="channel-name">{result.channel.name}</span>
        {result.detectedCountry && (
          <span className="country-badge">{result.detectedCountry.toUpperCase()}</span>
        )}
        <span className="normalized-label">Normalized: "{result.normalizedName}"</span>
      </div>
      <div className="conflict-options">
        {result.matches.map(epg => (
          <label key={epg.id} className="conflict-option">
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
                <span className="epg-name">{epg.name}</span>
                <span className="epg-tvgid">{epg.tvg_id}</span>
                <span className="epg-source">{getEPGSourceName(epg, epgSources)}</span>
              </div>
            </div>
          </label>
        ))}
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
  );
}
