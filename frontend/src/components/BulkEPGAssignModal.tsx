/**
 * Bulk EPG Assignment Modal
 *
 * Allows users to assign EPG data to multiple selected channels at once.
 * Uses the backend EPG matching API for country-aware matching and conflict resolution.
 */

import { logger } from '../utils/logger';
import { useState, useEffect, useMemo, useCallback, useRef, memo } from 'react';
import type { Channel, EPGData, EPGSource } from '../types';
import {
  matchChannelsToEPG,
  type EPGMatchEntry,
  type EPGMatchChannelResult,
} from '../services/api';
import { naturalCompare } from '../utils/naturalSort';
import { ModalOverlay } from './ModalOverlay';
import './BulkEPGAssignModal.css';

/** Assignment to be made after user confirms */
export interface EPGAssignment {
  channelId: number;
  channelName: string;
  tvg_id: string | null;
  epg_data_id: number | null;
}

interface BulkEPGAssignModalProps {
  isOpen: boolean;
  selectedChannels: Channel[];
  epgData: EPGData[];
  epgSources: EPGSource[];
  onClose: () => void;
  onAssign: (assignments: EPGAssignment[]) => void;
  /** Confidence threshold (0-100) for auto-matching. Matches >= threshold are considered "exact". Default: 80 */
  epgAutoMatchThreshold?: number;
}

type Phase = 'analyzing' | 'review';

/** Look up EPG source name by ID */
function getEPGSourceName(sourceId: number, epgSources: EPGSource[]): string {
  return epgSources.find(s => s.id === sourceId)?.name || 'Unknown';
}

/** Convert EPGData to EPGMatchEntry for uniform handling */
function epgDataToMatchEntry(epg: EPGData): EPGMatchEntry {
  return {
    epg_id: epg.id,
    epg_name: epg.name,
    tvg_id: epg.tvg_id,
    epg_source: epg.epg_source,
    confidence: 0,
    match_type: 'manual',
  };
}

/**
 * Multi-word fuzzy search for EPGMatchEntry.
 * All search words must appear in the entry's name, tvg_id, or source name.
 */
function matchesEPGEntrySearch(entry: EPGMatchEntry, searchWords: string[], sourceName?: string): boolean {
  const lowerName = entry.epg_name.toLowerCase();
  const lowerTvgId = entry.tvg_id.toLowerCase();
  const normalizedName = lowerName.replace(/[^a-z0-9]/g, '');
  const normalizedTvgId = lowerTvgId.replace(/[^a-z0-9]/g, '');
  const lowerSourceName = sourceName?.toLowerCase() ?? '';

  return searchWords.every(word => {
    const normalizedWord = word.replace(/[^a-z0-9]/g, '');
    return lowerName.includes(word) ||
           lowerTvgId.includes(word) ||
           normalizedName.includes(normalizedWord) ||
           normalizedTvgId.includes(normalizedWord) ||
           (lowerSourceName && lowerSourceName.includes(word));
  });
}

export const BulkEPGAssignModal = memo(function BulkEPGAssignModal({
  isOpen,
  selectedChannels,
  epgData,
  epgSources,
  onClose,
  onAssign,
  epgAutoMatchThreshold = 80,
}: BulkEPGAssignModalProps) {
  const [phase, setPhase] = useState<Phase>('analyzing');
  const [matchResults, setMatchResults] = useState<EPGMatchChannelResult[]>([]);
  const [conflictResolutions, setConflictResolutions] = useState<Map<number, EPGMatchEntry | null>>(new Map());
  const [autoMatchedExpanded, setAutoMatchedExpanded] = useState(true);
  const [unmatchedExpanded, setUnmatchedExpanded] = useState(true);
  const [currentConflictIndex, setCurrentConflictIndex] = useState(0);
  const [showConflictReview, setShowConflictReview] = useState(false);
  const [epgSearchFilter, setEpgSearchFilter] = useState('');

  // Unmatched channel search state
  const [unmatchedSelections, setUnmatchedSelections] = useState<Map<number, EPGMatchEntry>>(new Map());
  const [searchingUnmatchedId, setSearchingUnmatchedId] = useState<number | null>(null);
  const [unmatchedSearchTerm, setUnmatchedSearchTerm] = useState('');

  // Auto-match override state (for editing auto-matched items)
  const [autoMatchOverrides, setAutoMatchOverrides] = useState<Map<number, EPGMatchEntry | null>>(new Map());
  const [editingAutoMatchId, setEditingAutoMatchId] = useState<number | null>(null);
  const [autoMatchSearchTerm, setAutoMatchSearchTerm] = useState('');

  // EPG Source selection state - simple Set of selected source IDs
  const [selectedSourceIds, setSelectedSourceIds] = useState<Set<number> | null>(null);
  const [sourceDropdownOpen, setSourceDropdownOpen] = useState(false);
  const sourceDropdownRef = useRef<HTMLDivElement>(null);

  // Track if we've already analyzed for this modal session
  const hasAnalyzedRef = useRef(false);

  // Get available sources (exclude dummy EPG sources)
  const availableSources = useMemo(() => {
    if (!epgSources || !Array.isArray(epgSources)) return [];
    return epgSources.filter(s => s.source_type !== 'dummy' && s.is_active);
  }, [epgSources]);

  // Initialize selected sources when modal opens (select all by default)
  const effectiveSelectedSourceIds = useMemo(() => {
    if (selectedSourceIds !== null) return selectedSourceIds;
    // Default: all available sources selected
    return new Set(availableSources.map(s => s.id));
  }, [selectedSourceIds, availableSources]);

  // Filter EPG data based on selected sources (for manual "Search All" feature)
  const filteredEpgData = useMemo(() => {
    if (!epgData || !Array.isArray(epgData)) return [];
    if (effectiveSelectedSourceIds.size === 0) return [];
    return epgData.filter(e => effectiveSelectedSourceIds.has(e.epg_source));
  }, [epgData, effectiveSelectedSourceIds]);

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (sourceDropdownRef.current && !sourceDropdownRef.current.contains(event.target as Node)) {
        setSourceDropdownOpen(false);
      }
    };
    if (sourceDropdownOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [sourceDropdownOpen]);

  // Toggle source selection
  const handleToggleSource = useCallback((sourceId: number) => {
    setSelectedSourceIds(prev => {
      const current = prev ?? new Set(availableSources.map(s => s.id));
      const next = new Set(current);
      if (next.has(sourceId)) {
        next.delete(sourceId);
      } else {
        next.add(sourceId);
      }
      return next;
    });
  }, [availableSources]);

  // Select/deselect all sources
  const handleSelectAllSources = useCallback(() => {
    setSelectedSourceIds(new Set(availableSources.map(s => s.id)));
  }, [availableSources]);

  const handleClearAllSources = useCallback(() => {
    setSelectedSourceIds(new Set());
  }, []);

  // Re-run analysis with current source selection
  const handleRerunAnalysis = useCallback(() => {
    hasAnalyzedRef.current = false;
    setPhase('analyzing');
    setMatchResults([]);
    setConflictResolutions(new Map());
    setShowConflictReview(false);
    setCurrentConflictIndex(0);
  }, []);

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
      setEpgSearchFilter('');
      setUnmatchedSelections(new Map());
      setSearchingUnmatchedId(null);
      setUnmatchedSearchTerm('');
      setAutoMatchOverrides(new Map());
      setEditingAutoMatchId(null);
      setAutoMatchSearchTerm('');
      setSelectedSourceIds(null); // Reset to default (all selected)
      setSourceDropdownOpen(false);
      hasAnalyzedRef.current = false;
      return;
    }

    // Only run analysis once per modal open (or when re-run is triggered)
    if (hasAnalyzedRef.current) {
      return;
    }

    // Start analysis
    setPhase('analyzing');
    hasAnalyzedRef.current = true;

    // Run async analysis via backend API
    const runAnalysis = async () => {
      try {
        logger.debug('[BulkEPGAssign] Running analysis...');
        logger.debug('[BulkEPGAssign] Selected channels:', selectedChannels.length);

        // Early exit if no channels selected
        if (selectedChannels.length === 0) {
          logger.debug('[BulkEPGAssign] No channels selected, skipping analysis');
          setMatchResults([]);
          setPhase('review');
          return;
        }

        const response = await matchChannelsToEPG({
          channel_ids: selectedChannels.map(ch => ch.id),
          epg_source_ids: Array.from(effectiveSelectedSourceIds),
        });

        // Flatten all results into single array
        const results = [...response.exact, ...response.multiple, ...response.none];

        logger.debug('[BulkEPGAssign] Match results:', results);
        logger.debug(`[BulkEPGAssign] Summary: ${response.summary.exact_count} auto, ${response.summary.multiple_count} conflicts, ${response.summary.none_count} unmatched`);
        setMatchResults(results);
        setPhase('review');
      } catch (error) {
        logger.error('[BulkEPGAssign] Analysis failed:', error);
        // Still transition to review phase so UI doesn't hang
        setMatchResults([]);
        setPhase('review');
      }
    };

    runAnalysis();
  // Note: effectiveSelectedSourceIds changes when source selection changes, but we only want to re-run
  // when hasAnalyzedRef is reset (via handleRerunAnalysis), not on every source toggle
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, selectedChannels]);

  // Categorize results and sort A-Z by channel name
  // Uses confidence threshold to determine auto-matching
  const { autoMatched, conflicts, unmatched } = useMemo(() => {
    const auto: EPGMatchChannelResult[] = [];
    const conf: EPGMatchChannelResult[] = [];
    const none: EPGMatchChannelResult[] = [];

    for (const result of matchResults) {
      if (result.status === 'none') {
        // No matches found
        none.push(result);
      } else if (result.best_score >= epgAutoMatchThreshold) {
        // High confidence match (single or multiple) - auto-match
        auto.push(result);
      } else {
        // Below threshold - needs review
        conf.push(result);
      }
    }

    // Sort each category alphabetically by channel name (A-Z) with natural sort
    const sortByChannelName = (a: EPGMatchChannelResult, b: EPGMatchChannelResult) =>
      naturalCompare(a.channel_name, b.channel_name);

    return {
      autoMatched: auto.sort(sortByChannelName),
      conflicts: conf.sort(sortByChannelName),
      unmatched: none.sort(sortByChannelName),
    };
  }, [matchResults, epgAutoMatchThreshold]);


  // Pre-select recommended matches for all conflicts when entering review phase
  useEffect(() => {
    if (phase !== 'review' || conflicts.length === 0) return;

    // Only pre-select if we haven't set any resolutions yet (fresh review)
    if (conflictResolutions.size > 0) return;

    const preselected = new Map<number, EPGMatchEntry | null>();
    for (const result of conflicts) {
      if (result.matches.length > 0) {
        // First match is the recommended one (already sorted by confidence/country priority)
        preselected.set(result.channel_id, result.matches[0]);
      }
    }
    if (preselected.size > 0) {
      setConflictResolutions(preselected);
    }
  }, [phase, conflicts, conflictResolutions.size]);

  // Handle conflict resolution selection
  const handleConflictSelect = useCallback((channelId: number, entry: EPGMatchEntry | null) => {
    setConflictResolutions(prev => {
      const next = new Map(prev);
      next.set(channelId, entry);
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

  // Get recommended EPG for a result (the one with highest confidence score)
  const getRecommendedEpg = useCallback((result: EPGMatchChannelResult): EPGMatchEntry | null => {
    if (result.matches.length === 0) return null;
    // Return the match with highest confidence score
    let best = result.matches[0];
    for (const match of result.matches) {
      if (match.confidence > best.confidence) {
        best = match;
      }
    }
    return best;
  }, []);

  // Accept all recommended matches for unresolved conflicts
  const handleAcceptAllRecommended = useCallback(() => {
    setConflictResolutions(prev => {
      const next = new Map(prev);
      for (const result of conflicts) {
        // Only set if not already resolved
        if (!next.has(result.channel_id)) {
          const recommended = getRecommendedEpg(result);
          if (recommended) {
            next.set(result.channel_id, recommended);
          }
        }
      }
      return next;
    });
  }, [conflicts, getRecommendedEpg]);

  // Count unresolved conflicts
  const unresolvedCount = useMemo(() => {
    return conflicts.filter(c => !conflictResolutions.has(c.channel_id)).length;
  }, [conflicts, conflictResolutions]);

  // Handle unmatched channel EPG selection
  const handleUnmatchedSelect = useCallback((channelId: number, entry: EPGMatchEntry | null) => {
    setUnmatchedSelections(prev => {
      const next = new Map(prev);
      if (entry) {
        next.set(channelId, entry);
      } else {
        next.delete(channelId);
      }
      return next;
    });
    setSearchingUnmatchedId(null);
    setUnmatchedSearchTerm('');
  }, []);

  // Open search for an unmatched channel
  const handleOpenUnmatchedSearch = useCallback((result: EPGMatchChannelResult) => {
    setSearchingUnmatchedId(result.channel_id);
    setUnmatchedSearchTerm(result.channel_name);
  }, []);

  // Close unmatched search without selecting
  const handleCloseUnmatchedSearch = useCallback(() => {
    setSearchingUnmatchedId(null);
    setUnmatchedSearchTerm('');
  }, []);

  // Handle auto-match override selection
  const handleAutoMatchOverride = useCallback((channelId: number, entry: EPGMatchEntry | null) => {
    setAutoMatchOverrides(prev => {
      const next = new Map(prev);
      if (entry) {
        next.set(channelId, entry);
      } else {
        next.delete(channelId);
      }
      return next;
    });
    setEditingAutoMatchId(null);
    setAutoMatchSearchTerm('');
  }, []);

  // Open edit for an auto-matched channel
  const handleOpenAutoMatchEdit = useCallback((result: EPGMatchChannelResult) => {
    setEditingAutoMatchId(result.channel_id);
    setAutoMatchSearchTerm('');
  }, []);

  // Close auto-match edit without changing
  const handleCloseAutoMatchEdit = useCallback(() => {
    setEditingAutoMatchId(null);
    setAutoMatchSearchTerm('');
  }, []);

  // Reset an auto-match override back to original
  const handleResetAutoMatchOverride = useCallback((channelId: number) => {
    setAutoMatchOverrides(prev => {
      const next = new Map(prev);
      next.delete(channelId);
      return next;
    });
  }, []);

  // Count how many assignments will be made
  const assignmentCount = useMemo(() => {
    // Count auto-matched, accounting for overrides
    let count = 0;
    for (const result of autoMatched) {
      if (autoMatchOverrides.has(result.channel_id)) {
        // Has an override - count if not null
        if (autoMatchOverrides.get(result.channel_id) !== null) {
          count++;
        }
      } else {
        // No override - count the original auto-match
        count++;
      }
    }
    for (const [, selected] of conflictResolutions) {
      if (selected !== null) {
        count++;
      }
    }
    // Add unmatched selections
    count += unmatchedSelections.size;
    return count;
  }, [autoMatched, autoMatchOverrides, conflictResolutions, unmatchedSelections]);

  // Handle assign button click
  const handleAssign = useCallback(() => {
    const assignments: EPGAssignment[] = [];

    // Add auto-matched channels (with override support)
    for (const result of autoMatched) {
      if (autoMatchOverrides.has(result.channel_id)) {
        // Use override if set
        const override = autoMatchOverrides.get(result.channel_id);
        if (override) {
          assignments.push({
            channelId: result.channel_id,
            channelName: result.channel_name,
            tvg_id: override.tvg_id,
            epg_data_id: override.epg_id,
          });
        }
        // If override is null, skip this channel (user chose to not assign)
      } else {
        // Use original auto-match
        const match = result.matches[0];
        assignments.push({
          channelId: result.channel_id,
          channelName: result.channel_name,
          tvg_id: match.tvg_id,
          epg_data_id: match.epg_id,
        });
      }
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
            epg_data_id: selected.epg_id,
          });
        }
      }
    }

    // Add unmatched selections
    for (const [channelId, selected] of unmatchedSelections) {
      const channel = selectedChannels.find(c => c.id === channelId);
      if (channel) {
        assignments.push({
          channelId,
          channelName: channel.name,
          tvg_id: selected.tvg_id,
          epg_data_id: selected.epg_id,
        });
      }
    }

    onAssign(assignments);
  }, [autoMatched, autoMatchOverrides, conflictResolutions, unmatchedSelections, selectedChannels, onAssign]);

  if (!isOpen) return null;

  return (
    <ModalOverlay onClose={onClose}>
      <div className="modal-container modal-xxl bulk-epg-modal">
        <div className="modal-header">
          <h2>Bulk EPG Assignment</h2>
          <button className="modal-close-btn" onClick={onClose}>
            <span className="material-icons">close</span>
          </button>
        </div>

        {/* EPG Source Filter */}
        {availableSources.length > 0 && (
          <div className="bulk-epg-source-filter">
            <span className="source-filter-label">EPG Sources:</span>
            <div className="source-filter-dropdown" ref={sourceDropdownRef}>
              <button
                className="source-filter-button"
                onClick={() => setSourceDropdownOpen(!sourceDropdownOpen)}
                type="button"
              >
                <span>
                  {effectiveSelectedSourceIds.size === availableSources.length
                    ? `All Sources (${availableSources.length})`
                    : effectiveSelectedSourceIds.size === 0
                      ? 'No Sources'
                      : `${effectiveSelectedSourceIds.size} source${effectiveSelectedSourceIds.size !== 1 ? 's' : ''}`
                  }
                </span>
                <span className="dropdown-arrow">&#x25BC;</span>
              </button>
              {sourceDropdownOpen && (
                <div className="source-filter-menu">
                  <div className="source-filter-actions">
                    <button
                      type="button"
                      className="source-filter-action"
                      onClick={handleSelectAllSources}
                    >
                      Select All
                    </button>
                    <button
                      type="button"
                      className="source-filter-action"
                      onClick={handleClearAllSources}
                    >
                      Clear
                    </button>
                  </div>
                  <div className="source-filter-options">
                    {availableSources.map(source => {
                      const epgCount = epgData.filter(e => e.epg_source === source.id).length;
                      return (
                        <div
                          key={source.id}
                          className={`source-filter-option ${effectiveSelectedSourceIds.has(source.id) ? 'selected' : ''}`}
                        >
                          <label className="source-option-label">
                            <input
                              type="checkbox"
                              checked={effectiveSelectedSourceIds.has(source.id)}
                              onChange={() => handleToggleSource(source.id)}
                            />
                            <span className="source-option-name">{source.name}</span>
                            <span className="source-option-count">({epgCount})</span>
                          </label>
                        </div>
                      );
                    })}
                  </div>
                  <div className="source-filter-apply">
                    <button
                      type="button"
                      className="source-apply-btn"
                      onClick={() => {
                        setSourceDropdownOpen(false);
                        handleRerunAnalysis();
                      }}
                    >
                      <span className="material-icons">refresh</span>
                      Re-analyze
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        <div className="modal-body bulk-epg-body">
          {phase === 'analyzing' ? (
            <div className="modal-loading bulk-epg-analyzing">
              <span className="material-icons modal-spinning-ccw">sync</span>
              <div className="analyzing-text">
                <p>Analyzing {selectedChannels.length} channels...</p>
              </div>
            </div>
          ) : (
            <>
              {/* Summary */}
              <div className="modal-summary">
                <div className="modal-summary-item success">
                  <span className="material-icons">check_circle</span>
                  <span>
                    {autoMatched.length} matched
                    {autoMatched.length > 0 && (
                      <span className="score-range">
                        ({Math.min(...autoMatched.map(r => r.best_score))}-{Math.max(...autoMatched.map(r => r.best_score))}%)
                      </span>
                    )}
                  </span>
                </div>
                <div className="modal-summary-item warning">
                  <span className="material-icons">help</span>
                  <span>
                    {conflicts.length} need review
                    {conflicts.length > 0 && (
                      <span className="score-range">
                        ({Math.min(...conflicts.map(r => r.best_score))}-{Math.max(...conflicts.map(r => r.best_score))}%)
                      </span>
                    )}
                  </span>
                </div>
                <div className="modal-summary-item neutral">
                  <span className="material-icons">remove_circle_outline</span>
                  <span>{unmatched.length} unmatched</span>
                </div>
              </div>

              {/* No EPG data warning */}
              {epgData.length === 0 && (
                <div className="modal-warning-banner">
                  <span className="material-icons">warning</span>
                  <p>No EPG data available. Load EPG sources in the EPG Manager tab first.</p>
                </div>
              )}

              {/* Choice prompt when there are conflicts and user hasn't chosen yet */}
              {conflicts.length > 0 && !showConflictReview && (
                <div className="modal-choice-prompt">
                  <p>There are {conflicts.length} channels with multiple EPG matches. How would you like to proceed?</p>
                  <div className="modal-choice-buttons">
                    <button
                      className="modal-choice-btn choice-review"
                      onClick={() => setShowConflictReview(true)}
                    >
                      <span className="material-icons">rate_review</span>
                      <div className="modal-choice-content">
                        <span className="modal-choice-title">Review Changes</span>
                        <span className="modal-choice-desc">Manually select the best match for each channel</span>
                      </div>
                    </button>
                    <button
                      className="modal-choice-btn choice-accept"
                      onClick={handleAcceptAllRecommended}
                    >
                      <span className="material-icons">done_all</span>
                      <div className="modal-choice-content">
                        <span className="modal-choice-title">Accept Best Guesses</span>
                        <span className="modal-choice-desc">Use the recommended match for all conflicts</span>
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
                    </div>
                  </div>
                  {/* Navigation above the card */}
                  <div className="modal-nav">
                    <button
                      className="modal-nav-btn"
                      onClick={handlePrevConflict}
                      disabled={currentConflictIndex === 0}
                      title="Previous"
                    >
                      <span className="material-icons">chevron_left</span>
                      <span className="nav-label">Previous</span>
                    </button>
                    <span className="modal-nav-counter">{currentConflictIndex + 1} / {conflicts.length}</span>
                    <button
                      className="modal-nav-btn"
                      onClick={handleNextConflict}
                      disabled={currentConflictIndex === conflicts.length - 1}
                      title="Next"
                    >
                      <span className="nav-label">Next</span>
                      <span className="material-icons">chevron_right</span>
                    </button>
                  </div>
                  {/* Single conflict card - show only current conflict */}
                  {conflicts[currentConflictIndex] && (
                    <ConflictCard
                      result={conflicts[currentConflictIndex]}
                      epgSources={epgSources}
                      allEpgData={filteredEpgData}
                      selectedEpg={conflictResolutions.get(conflicts[currentConflictIndex].channel_id)}
                      onSelect={epg => handleConflictSelect(conflicts[currentConflictIndex].channel_id, epg)}
                      recommendedEpg={getRecommendedEpg(conflicts[currentConflictIndex])}
                      searchFilter={epgSearchFilter}
                      onSearchChange={setEpgSearchFilter}
                    />
                  )}
                </div>
              )}

              {/* Auto-Matched Section (Collapsible) */}
              {autoMatched.length > 0 && (
                <div className="modal-collapsible">
                  <button
                    className="modal-collapsible-header"
                    onClick={() => setAutoMatchedExpanded(!autoMatchedExpanded)}
                  >
                    <span className="material-icons">check_circle</span>
                    Auto-Matched ({autoMatched.length})
                    {autoMatchOverrides.size > 0 && (
                      <span className="override-count">({autoMatchOverrides.size} modified)</span>
                    )}
                    <span className="material-icons expand-icon">
                      {autoMatchedExpanded ? 'expand_less' : 'expand_more'}
                    </span>
                  </button>
                  {autoMatchedExpanded && (
                    <div className="matched-list">
                      {autoMatched.map(result => {
                        const override = autoMatchOverrides.get(result.channel_id);
                        const hasOverride = autoMatchOverrides.has(result.channel_id);
                        const displayEpg = hasOverride && override ? override : result.matches[0];
                        const isEditing = editingAutoMatchId === result.channel_id;

                        return (
                          <div key={result.channel_id}>
                            <div className={`matched-item ${hasOverride ? 'has-override' : ''}`}>
                              <div className="matched-channel">
                                <span className="channel-name" title={result.channel_name}>{result.channel_name}</span>
                                {result.detected_country && (
                                  <span className="country-badge">{result.detected_country.toUpperCase()}</span>
                                )}
                              </div>
                              <span className="material-icons arrow">arrow_forward</span>
                              <div className="matched-epg">
                                <span className="epg-name" title={displayEpg.epg_name}>
                                  {displayEpg.epg_name}
                                  {hasOverride && <span className="modified-tag">Modified</span>}
                                </span>
                                <span className="epg-tvgid" title={displayEpg.tvg_id}>{displayEpg.tvg_id}</span>
                              </div>
                              <span className="confidence-badge" title="Confidence score">{result.best_score}%</span>
                              <button
                                className="edit-match-btn"
                                onClick={() => handleOpenAutoMatchEdit(result)}
                                title="Change EPG assignment"
                              >
                                <span className="material-icons">edit</span>
                              </button>
                              {hasOverride && (
                                <button
                                  className="reset-match-btn"
                                  onClick={() => handleResetAutoMatchOverride(result.channel_id)}
                                  title="Reset to original match"
                                >
                                  <span className="material-icons">undo</span>
                                </button>
                              )}
                            </div>
                            {isEditing && (
                              <EPGSearchCard
                                channelName={result.channel_name}
                                normalizedName={result.channel_name}
                                detectedCountry={result.detected_country}
                                epgData={filteredEpgData}
                                epgSources={epgSources}
                                selectedEpg={displayEpg}
                                onSelect={(epg) => handleAutoMatchOverride(result.channel_id, epg)}
                                onClose={handleCloseAutoMatchEdit}
                                searchTerm={autoMatchSearchTerm}
                                onSearchChange={setAutoMatchSearchTerm}
                              />
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}

              {/* Unmatched Section (Collapsible) */}
              {unmatched.length > 0 && (
                <div className="modal-collapsible">
                  <button
                    className="modal-collapsible-header"
                    onClick={() => setUnmatchedExpanded(!unmatchedExpanded)}
                  >
                    <span className="material-icons">remove_circle_outline</span>
                    Unmatched ({unmatched.length})
                    {unmatchedSelections.size > 0 && (
                      <span className="assigned-count">({unmatchedSelections.size} assigned)</span>
                    )}
                    <span className="material-icons expand-icon">
                      {unmatchedExpanded ? 'expand_less' : 'expand_more'}
                    </span>
                  </button>
                  {unmatchedExpanded && (
                    <div className="unmatched-list">
                      {unmatched.map(result => {
                        const assignedEpg = unmatchedSelections.get(result.channel_id);
                        const isSearching = searchingUnmatchedId === result.channel_id;
                        return (
                          <div key={result.channel_id}>
                            <div
                              className={`unmatched-item clickable ${assignedEpg ? 'assigned' : ''}`}
                              onClick={() => handleOpenUnmatchedSearch(result)}
                            >
                              <div className="unmatched-item-main">
                                <span className="channel-name" title={result.channel_name}>{result.channel_name}</span>
                                {result.detected_country && (
                                  <span className="country-badge">{result.detected_country.toUpperCase()}</span>
                                )}
                                {!assignedEpg && (
                                  <span className="normalized-name">({result.channel_name})</span>
                                )}
                              </div>
                              {assignedEpg ? (
                                <div className="assigned-epg-info">
                                  <span className="material-icons assigned-icon">check_circle</span>
                                  <span className="assigned-epg-name" title={assignedEpg.epg_name}>{assignedEpg.epg_name}</span>
                                </div>
                              ) : (
                                <span className="material-icons search-icon">search</span>
                              )}
                            </div>
                            {isSearching && (
                              <EPGSearchCard
                                channelName={result.channel_name}
                                normalizedName={result.channel_name}
                                detectedCountry={result.detected_country}
                                epgData={filteredEpgData}
                                epgSources={epgSources}
                                selectedEpg={assignedEpg}
                                onSelect={(epg) => handleUnmatchedSelect(result.channel_id, epg)}
                                onClose={handleCloseUnmatchedSearch}
                                searchTerm={unmatchedSearchTerm}
                                onSearchChange={setUnmatchedSearchTerm}
                              />
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>

        <div className="modal-footer">
          <button
            className="modal-btn modal-btn-primary"
            onClick={handleAssign}
            disabled={phase === 'analyzing' || assignmentCount === 0}
          >
            Assign {assignmentCount} Channel{assignmentCount !== 1 ? 's' : ''}
          </button>
        </div>
      </div>
    </ModalOverlay>
  );
});

// Conflict card component - shows a single conflict as a card
interface ConflictCardProps {
  result: EPGMatchChannelResult;
  epgSources: EPGSource[];
  allEpgData: EPGData[];  // All EPG data for "Search All" mode
  selectedEpg: EPGMatchEntry | null | undefined;
  onSelect: (epg: EPGMatchEntry | null) => void;
  recommendedEpg: EPGMatchEntry | null;
  searchFilter: string;
  onSearchChange: (filter: string) => void;
}

const MAX_ALL_EPG_RESULTS = 50;

const ConflictCard = memo(function ConflictCard({ result, epgSources, allEpgData, selectedEpg, onSelect, recommendedEpg, searchFilter, onSearchChange }: ConflictCardProps) {
  // State for "Search All EPG" mode
  const [searchAllMode, setSearchAllMode] = useState(false);

  // Fuzzy multi-word search matcher for EPGMatchEntry
  const matchesEntrySearch = useCallback((entry: EPGMatchEntry, searchWords: string[]): boolean => {
    const sourceName = getEPGSourceName(entry.epg_source, epgSources);
    return matchesEPGEntrySearch(entry, searchWords, sourceName);
  }, [epgSources]);

  // Fuzzy multi-word search matcher for EPGData (used in Search All mode)
  const matchesDataSearch = useCallback((epg: EPGData, searchWords: string[]): boolean => {
    const sourceName = getEPGSourceName(epg.epg_source, epgSources);
    return matchesEPGEntrySearch(epgDataToMatchEntry(epg), searchWords, sourceName);
  }, [epgSources]);

  // Build a map from EPG id to confidence score for quick lookup
  const scoreByEpgId = useMemo(() => {
    const map = new Map<number, number>();
    for (const match of result.matches) {
      map.set(match.epg_id, match.confidence);
    }
    return map;
  }, [result.matches]);

  // Filter matches based on search - either from suggestions or all EPG data
  const filteredMatches: EPGMatchEntry[] = useMemo(() => {
    if (searchAllMode) {
      // Search all EPG data with fuzzy multi-word matching
      if (!searchFilter.trim()) return [];
      const searchWords = searchFilter.toLowerCase().split(/\s+/).filter(w => w.length > 0);
      if (searchWords.length === 0) return [];
      const results = allEpgData
        .filter(epg => matchesDataSearch(epg, searchWords))
        .slice(0, MAX_ALL_EPG_RESULTS)
        .map(epgDataToMatchEntry);
      return results;
    } else {
      // Filter within suggestions with fuzzy multi-word matching
      if (!searchFilter.trim()) return result.matches;
      const searchWords = searchFilter.toLowerCase().split(/\s+/).filter(w => w.length > 0);
      if (searchWords.length === 0) return result.matches;
      return result.matches.filter(entry => matchesEntrySearch(entry, searchWords));
    }
  }, [searchAllMode, result.matches, allEpgData, searchFilter, matchesEntrySearch, matchesDataSearch]);

  // Check if there are more results when in search all mode
  const hasMoreResults = useMemo(() => {
    if (!searchAllMode || !searchFilter.trim()) return false;
    const searchWords = searchFilter.toLowerCase().split(/\s+/).filter(w => w.length > 0);
    if (searchWords.length === 0) return false;
    const totalCount = allEpgData.filter(epg => matchesDataSearch(epg, searchWords)).length;
    return totalCount > MAX_ALL_EPG_RESULTS;
  }, [searchAllMode, allEpgData, searchFilter, matchesDataSearch]);

  return (
    <div className="conflict-card">
      <div className="conflict-card-header">
        <div className="conflict-channel">
          <span className="channel-name" title={result.channel_name}>{result.channel_name}</span>
          {result.detected_country && (
            <span className="country-badge">{result.detected_country.toUpperCase()}</span>
          )}
        </div>
        <div className="normalized-label">Searching for: &quot;{result.channel_name}&quot;</div>
      </div>
      <div className="conflict-card-search">
        <span className="material-icons">search</span>
        <input
          type="text"
          placeholder={searchAllMode ? "Search all EPG data..." : "Filter suggestions..."}
          value={searchFilter}
          onChange={e => onSearchChange(e.target.value)}
        />
        {searchFilter && (
          <button className="clear-search" onClick={() => onSearchChange('')}>
            <span className="material-icons">close</span>
          </button>
        )}
      </div>
      <div className="conflict-card-mode-toggle">
        <button
          className={`mode-btn ${!searchAllMode ? 'active' : ''}`}
          onClick={() => { setSearchAllMode(false); onSearchChange(''); }}
        >
          Suggestions ({result.matches.length})
        </button>
        <button
          className={`mode-btn ${searchAllMode ? 'active' : ''}`}
          onClick={() => { setSearchAllMode(true); onSearchChange(result.channel_name || ''); }}
        >
          Search All EPG
        </button>
      </div>
      <div className="conflict-card-body">
        <div className="conflict-options">
          {searchAllMode && !searchFilter.trim() && (
            <div className="search-prompt">Type to search across all EPG entries</div>
          )}
          {filteredMatches.map(entry => {
            const isRecommended = !searchAllMode && recommendedEpg?.epg_id === entry.epg_id;
            const confidence = scoreByEpgId.get(entry.epg_id) ?? entry.confidence;
            return (
              <label
                key={entry.epg_id}
                className={`conflict-option ${isRecommended ? 'recommended' : ''} ${selectedEpg?.epg_id === entry.epg_id ? 'selected' : ''}`}
              >
                <input
                  type="radio"
                  name={`conflict-${result.channel_id}`}
                  checked={selectedEpg?.epg_id === entry.epg_id}
                  onChange={() => onSelect(entry)}
                />
                <div className="option-content">
                  <div className="option-info">
                    <span className="epg-name" title={entry.epg_name}>
                      {entry.epg_name}
                      {isRecommended && <span className="recommended-tag">Recommended</span>}
                    </span>
                    <span className="epg-tvgid" title={entry.tvg_id}>{entry.tvg_id}</span>
                    <span className="epg-source">{getEPGSourceName(entry.epg_source, epgSources)}</span>
                  </div>
                  {confidence > 0 && (
                    <span className="option-confidence" title="Confidence score">{confidence}%</span>
                  )}
                </div>
              </label>
            );
          })}
          {filteredMatches.length === 0 && searchFilter && !searchAllMode && (
            <div className="no-matches">No matches found for &quot;{searchFilter}&quot;</div>
          )}
          {filteredMatches.length === 0 && searchFilter && searchAllMode && (
            <div className="no-matches">No EPG entries found for &quot;{searchFilter}&quot;</div>
          )}
          {hasMoreResults && (
            <div className="more-results">
              Showing first {MAX_ALL_EPG_RESULTS} results. Refine your search for more specific matches.
            </div>
          )}
          <label className={`conflict-option skip-option ${selectedEpg === null ? 'selected' : ''}`}>
            <input
              type="radio"
              name={`conflict-${result.channel_id}`}
              checked={selectedEpg === null}
              onChange={() => onSelect(null)}
            />
            <span className="skip-label">Skip this channel</span>
          </label>
        </div>
      </div>
    </div>
  );
});

// EPG Search Card - for searching ALL EPG data (used for unmatched channels and auto-match editing)
interface EPGSearchCardProps {
  channelName: string;
  normalizedName: string;
  detectedCountry: string | null;
  epgData: EPGData[];
  epgSources: EPGSource[];
  selectedEpg: EPGMatchEntry | undefined;
  onSelect: (epg: EPGMatchEntry | null) => void;
  onClose: () => void;
  searchTerm: string;
  onSearchChange: (term: string) => void;
}

const MAX_SEARCH_RESULTS = 50;

const EPGSearchCard = memo(function EPGSearchCard({
  channelName,
  normalizedName,
  detectedCountry,
  epgData,
  epgSources,
  selectedEpg,
  onSelect,
  onClose,
  searchTerm,
  onSearchChange,
}: EPGSearchCardProps) {
  // Fuzzy multi-word search matcher
  const matchesSearch = useCallback((epg: EPGData, searchWords: string[]): boolean => {
    return matchesEPGEntrySearch(epgDataToMatchEntry(epg), searchWords);
  }, []);

  const searchResults = useMemo(() => {
    if (!searchTerm.trim()) return [];
    const searchWords = searchTerm.toLowerCase().split(/\s+/).filter(w => w.length > 0);
    if (searchWords.length === 0) return [];

    const results = epgData.filter(epg => matchesSearch(epg, searchWords));
    // Limit results for performance
    return results.slice(0, MAX_SEARCH_RESULTS);
  }, [epgData, searchTerm, matchesSearch]);

  const hasMoreResults = useMemo(() => {
    if (!searchTerm.trim()) return false;
    const searchWords = searchTerm.toLowerCase().split(/\s+/).filter(w => w.length > 0);
    if (searchWords.length === 0) return false;

    const totalCount = epgData.filter(epg => matchesSearch(epg, searchWords)).length;
    return totalCount > MAX_SEARCH_RESULTS;
  }, [epgData, searchTerm, matchesSearch]);

  return (
    <div className="epg-search-card">
      <div className="epg-search-card-header">
        <div className="epg-search-channel">
          <span className="channel-name" title={channelName}>{channelName}</span>
          {detectedCountry && (
            <span className="country-badge">{detectedCountry.toUpperCase()}</span>
          )}
        </div>
        <button className="close-btn" onClick={onClose} title="Close">
          <span className="material-icons">close</span>
        </button>
      </div>
      <div className="epg-search-card-search">
        <span className="material-icons">search</span>
        <input
          type="text"
          placeholder="Search all EPG data..."
          value={searchTerm}
          onChange={e => onSearchChange(e.target.value)}
          autoFocus
        />
        {searchTerm && (
          <button className="clear-search" onClick={() => onSearchChange('')}>
            <span className="material-icons">close</span>
          </button>
        )}
      </div>
      <div className="epg-search-hint">
        Searching for: &quot;{normalizedName}&quot;
      </div>
      <div className="epg-search-card-body">
        {searchTerm.trim() === '' ? (
          <div className="search-prompt">Type to search across all EPG entries</div>
        ) : searchResults.length === 0 ? (
          <div className="no-matches">No EPG entries found for &quot;{searchTerm}&quot;</div>
        ) : (
          <div className="epg-search-options">
            {searchResults.map(epg => (
              <label
                key={epg.id}
                className={`conflict-option ${selectedEpg?.epg_id === epg.id ? 'selected' : ''}`}
              >
                <input
                  type="radio"
                  name="epg-search-select"
                  checked={selectedEpg?.epg_id === epg.id}
                  onChange={() => onSelect(epgDataToMatchEntry(epg))}
                />
                <div className="option-content">
                  {epg.icon_url && (
                    <img src={epg.icon_url} alt="" className="epg-icon" />
                  )}
                  <div className="option-info">
                    <span className="epg-name" title={epg.name}>{epg.name}</span>
                    <span className="epg-tvgid" title={epg.tvg_id}>{epg.tvg_id}</span>
                    <span className="epg-source">{getEPGSourceName(epg.epg_source, epgSources)}</span>
                  </div>
                </div>
              </label>
            ))}
            {hasMoreResults && (
              <div className="more-results">
                Showing first {MAX_SEARCH_RESULTS} results. Refine your search for more specific matches.
              </div>
            )}
          </div>
        )}
      </div>
      <div className="epg-search-card-footer">
        <button className="modal-btn modal-btn-secondary" onClick={onClose}>
          Cancel
        </button>
        <button
          className="modal-btn modal-btn-primary"
          onClick={() => onSelect(selectedEpg || null)}
          disabled={!selectedEpg}
        >
          {selectedEpg ? 'Assign EPG' : 'Select an EPG'}
        </button>
      </div>
    </div>
  );
});
