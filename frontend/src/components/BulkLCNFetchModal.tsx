/**
 * Bulk LCN (Gracenote ID) Fetch Modal
 *
 * Allows users to fetch Gracenote IDs from EPG XML sources for multiple
 * selected channels at once. Uses batch API for efficient lookup.
 */

import { useState, useEffect, useMemo, useRef, memo } from 'react';
import type { Channel, EPGData } from '../types';
import { getEPGLcnBatch, type LCNLookupItem } from '../services/api';
import { naturalCompare } from '../utils/naturalSort';
import './BulkLCNFetchModal.css';

export interface LCNAssignment {
  channelId: number;
  channelName: string;
  tvc_guide_stationid: string;
}

interface BulkLCNFetchModalProps {
  isOpen: boolean;
  selectedChannels: Channel[];
  epgData: EPGData[];
  onClose: () => void;
  onAssign: (assignments: LCNAssignment[]) => void;
}

type Phase = 'fetching' | 'review';

interface ChannelLCNResult {
  channel: Channel;
  tvgId: string | null;
  lcn: string | null;
  source: string | null;
  alreadyHasLcn: boolean;
}

export const BulkLCNFetchModal = memo(function BulkLCNFetchModal({
  isOpen,
  selectedChannels,
  epgData,
  onClose,
  onAssign,
}: BulkLCNFetchModalProps) {
  const [phase, setPhase] = useState<Phase>('fetching');
  const [results, setResults] = useState<ChannelLCNResult[]>([]);
  const [error, setError] = useState<string | null>(null);

  // Selection state - which results to include in assignment
  const [selectedForAssignment, setSelectedForAssignment] = useState<Set<number>>(new Set());

  // Section collapsed states
  const [foundExpanded, setFoundExpanded] = useState(true);
  const [noTvgIdExpanded, setNoTvgIdExpanded] = useState(true);
  const [notFoundExpanded, setNotFoundExpanded] = useState(true);
  const [alreadyHasExpanded, setAlreadyHasExpanded] = useState(false);

  // Track if we've already fetched for this modal session
  const hasFetchedRef = useRef(false);

  // Fetch LCNs when modal opens
  useEffect(() => {
    if (!isOpen) {
      // Reset state when modal closes
      setPhase('fetching');
      setResults([]);
      setError(null);
      setSelectedForAssignment(new Set());
      setFoundExpanded(true);
      setNoTvgIdExpanded(true);
      setNotFoundExpanded(true);
      setAlreadyHasExpanded(false);
      hasFetchedRef.current = false;
      return;
    }

    // Only fetch once per modal open
    if (hasFetchedRef.current) {
      return;
    }
    hasFetchedRef.current = true;

    // Start fetching
    const fetchLCNs = async () => {
      setPhase('fetching');
      setError(null);

      // Build list of channels with TVG-IDs to look up
      const channelResults: ChannelLCNResult[] = [];
      const lookupItems: LCNLookupItem[] = [];
      const tvgIdToChannels = new Map<string, Channel[]>();

      for (const channel of selectedChannels) {
        // Get TVG-ID and EPG source from channel's EPG data
        const epgDataEntry = epgData.find(e => e.id === channel.epg_data_id);
        const tvgId = channel.tvg_id || epgDataEntry?.tvg_id || null;
        const epgSourceId = epgDataEntry?.epg_source ?? null;

        const alreadyHasLcn = Boolean(channel.tvc_guide_stationid);

        if (!tvgId) {
          // No TVG-ID available
          channelResults.push({
            channel,
            tvgId: null,
            lcn: null,
            source: null,
            alreadyHasLcn,
          });
        } else {
          // Track which channels have this TVG-ID
          const existing = tvgIdToChannels.get(tvgId) || [];
          existing.push(channel);
          tvgIdToChannels.set(tvgId, existing);

          // Add lookup item with EPG source (only add once per unique tvg_id)
          if (existing.length === 1) {
            lookupItems.push({
              tvg_id: tvgId,
              epg_source_id: epgSourceId,
            });
          }
        }
      }

      // Fetch LCNs for all lookup items
      if (lookupItems.length > 0) {
        try {
          const response = await getEPGLcnBatch(lookupItems);
          const lcnResults = response.results;

          // Map results back to channels
          for (const [tvgId, channels] of tvgIdToChannels.entries()) {
            const lcnData = lcnResults[tvgId];
            for (const channel of channels) {
              const alreadyHasLcn = Boolean(channel.tvc_guide_stationid);
              channelResults.push({
                channel,
                tvgId,
                lcn: lcnData?.lcn || null,
                source: lcnData?.source || null,
                alreadyHasLcn,
              });
            }
          }
        } catch (err) {
          console.error('Failed to fetch LCNs:', err);
          setError('Failed to fetch Gracenote IDs from EPG sources');
          // Still show channels without LCN data
          for (const [tvgId, channels] of tvgIdToChannels.entries()) {
            for (const channel of channels) {
              const alreadyHasLcn = Boolean(channel.tvc_guide_stationid);
              channelResults.push({
                channel,
                tvgId,
                lcn: null,
                source: null,
                alreadyHasLcn,
              });
            }
          }
        }
      }

      // Sort results by channel name
      channelResults.sort((a, b) => naturalCompare(a.channel.name, b.channel.name));

      setResults(channelResults);

      // Pre-select all channels that have LCN found and don't already have one
      const toSelect = new Set<number>();
      for (const result of channelResults) {
        if (result.lcn && !result.alreadyHasLcn) {
          toSelect.add(result.channel.id);
        }
      }
      setSelectedForAssignment(toSelect);

      setPhase('review');
    };

    fetchLCNs();
  }, [isOpen, selectedChannels, epgData]);

  // Categorize results
  const { found, notFound, noTvgId, alreadyHas } = useMemo(() => {
    const foundItems: ChannelLCNResult[] = [];
    const notFoundItems: ChannelLCNResult[] = [];
    const noTvgIdItems: ChannelLCNResult[] = [];
    const alreadyHasItems: ChannelLCNResult[] = [];

    for (const result of results) {
      if (!result.tvgId) {
        // No TVG-ID available
        noTvgIdItems.push(result);
      } else if (result.lcn) {
        // EPG found a Gracenote ID
        if (result.alreadyHasLcn) {
          // Channel already has a Gracenote ID - check if it's different
          if (result.channel.tvc_guide_stationid !== result.lcn) {
            // Different ID - add to found so conflict modal can handle it
            foundItems.push(result);
          } else {
            // Same ID - already correct
            alreadyHasItems.push(result);
          }
        } else {
          // Channel doesn't have one - new assignment
          foundItems.push(result);
        }
      } else {
        // EPG doesn't have a Gracenote ID for this TVG-ID
        if (result.alreadyHasLcn) {
          // Channel has one but EPG doesn't - keep in alreadyHas
          alreadyHasItems.push(result);
        } else {
          // Not found anywhere
          notFoundItems.push(result);
        }
      }
    }

    return {
      found: foundItems,
      notFound: notFoundItems,
      noTvgId: noTvgIdItems,
      alreadyHas: alreadyHasItems,
    };
  }, [results]);

  // Toggle selection for a channel
  const toggleSelection = (channelId: number) => {
    setSelectedForAssignment(prev => {
      const next = new Set(prev);
      if (next.has(channelId)) {
        next.delete(channelId);
      } else {
        next.add(channelId);
      }
      return next;
    });
  };

  // Select/deselect all found
  const selectAllFound = () => {
    setSelectedForAssignment(prev => {
      const next = new Set(prev);
      for (const result of found) {
        next.add(result.channel.id);
      }
      return next;
    });
  };

  const deselectAllFound = () => {
    setSelectedForAssignment(prev => {
      const next = new Set(prev);
      for (const result of found) {
        next.delete(result.channel.id);
      }
      return next;
    });
  };

  // Count selected
  const selectedCount = useMemo(() => {
    let count = 0;
    for (const result of results) {
      if (selectedForAssignment.has(result.channel.id) && result.lcn) {
        count++;
      }
    }
    return count;
  }, [results, selectedForAssignment]);

  // Handle assign
  const handleAssign = () => {
    const assignments: LCNAssignment[] = [];
    for (const result of results) {
      if (selectedForAssignment.has(result.channel.id) && result.lcn) {
        assignments.push({
          channelId: result.channel.id,
          channelName: result.channel.name,
          tvc_guide_stationid: result.lcn,
        });
      }
    }
    onAssign(assignments);
  };

  if (!isOpen) return null;

  return (
    <div className="modal-overlay">
      <div className="bulk-lcn-modal" onClick={e => e.stopPropagation()}>
        <div className="bulk-lcn-header">
          <h2>Fetch Gracenote IDs</h2>
          <button className="modal-close-btn" onClick={onClose}>
            <span className="material-icons">close</span>
          </button>
        </div>

        <div className="bulk-lcn-body">
          {phase === 'fetching' ? (
            <div className="bulk-lcn-fetching">
              <span className="material-icons spinning">sync</span>
              <div className="fetching-text">
                <p>Fetching Gracenote IDs for {selectedChannels.length} channels...</p>
                <p className="fetching-hint">This may take a moment for large EPG files</p>
              </div>
            </div>
          ) : (
            <>
              {/* Error message */}
              {error && (
                <div className="bulk-lcn-error">
                  <span className="material-icons">error</span>
                  <span>{error}</span>
                </div>
              )}

              {/* Summary */}
              <div className="bulk-lcn-summary">
                <div className="summary-item success">
                  <span className="material-icons">check_circle</span>
                  <span>{found.length} found</span>
                </div>
                <div className="summary-item warning">
                  <span className="material-icons">help</span>
                  <span>{notFound.length} not found</span>
                </div>
                <div className="summary-item neutral">
                  <span className="material-icons">link_off</span>
                  <span>{noTvgId.length} no TVG-ID</span>
                </div>
                {alreadyHas.length > 0 && (
                  <div className="summary-item info">
                    <span className="material-icons">verified</span>
                    <span>{alreadyHas.length} already set</span>
                  </div>
                )}
              </div>

              {/* Found Section */}
              {found.length > 0 && (
                <div className="bulk-lcn-section collapsible">
                  <button
                    className="section-header clickable"
                    onClick={() => setFoundExpanded(!foundExpanded)}
                  >
                    <span className="material-icons">check_circle</span>
                    Found ({found.length})
                    <span className="selected-count">
                      {selectedCount} selected
                    </span>
                    <span className="material-icons expand-icon">
                      {foundExpanded ? 'expand_less' : 'expand_more'}
                    </span>
                  </button>
                  {foundExpanded && (
                    <>
                      <div className="section-actions">
                        <button className="action-btn" onClick={selectAllFound}>
                          Select All
                        </button>
                        <button className="action-btn" onClick={deselectAllFound}>
                          Deselect All
                        </button>
                      </div>
                      <div className="lcn-list">
                        {found.map(result => (
                          <label
                            key={result.channel.id}
                            className={`lcn-item ${selectedForAssignment.has(result.channel.id) ? 'selected' : ''}`}
                          >
                            <input
                              type="checkbox"
                              checked={selectedForAssignment.has(result.channel.id)}
                              onChange={() => toggleSelection(result.channel.id)}
                            />
                            <div className="lcn-item-content">
                              <span className="channel-name" title={result.channel.name}>
                                {result.channel.name}
                              </span>
                              <span className="material-icons arrow">arrow_forward</span>
                              <span className="lcn-value" title={result.lcn || ''}>
                                {result.lcn}
                              </span>
                              <span className="lcn-source" title={result.source || ''}>
                                ({result.source})
                              </span>
                            </div>
                          </label>
                        ))}
                      </div>
                    </>
                  )}
                </div>
              )}

              {/* Not Found Section */}
              {notFound.length > 0 && (
                <div className="bulk-lcn-section collapsible">
                  <button
                    className="section-header clickable"
                    onClick={() => setNotFoundExpanded(!notFoundExpanded)}
                  >
                    <span className="material-icons">help</span>
                    Not Found in EPG ({notFound.length})
                    <span className="material-icons expand-icon">
                      {notFoundExpanded ? 'expand_less' : 'expand_more'}
                    </span>
                  </button>
                  {notFoundExpanded && (
                    <div className="lcn-list muted">
                      {notFound.map(result => (
                        <div key={result.channel.id} className="lcn-item disabled">
                          <div className="lcn-item-content">
                            <span className="channel-name" title={result.channel.name}>
                              {result.channel.name}
                            </span>
                            <span className="tvg-id-hint" title={result.tvgId || ''}>
                              TVG-ID: {result.tvgId}
                            </span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* No TVG-ID Section */}
              {noTvgId.length > 0 && (
                <div className="bulk-lcn-section collapsible">
                  <button
                    className="section-header clickable"
                    onClick={() => setNoTvgIdExpanded(!noTvgIdExpanded)}
                  >
                    <span className="material-icons">link_off</span>
                    No TVG-ID ({noTvgId.length})
                    <span className="material-icons expand-icon">
                      {noTvgIdExpanded ? 'expand_less' : 'expand_more'}
                    </span>
                  </button>
                  {noTvgIdExpanded && (
                    <div className="lcn-list muted">
                      <div className="section-hint">
                        These channels need a TVG-ID assigned before Gracenote ID can be fetched.
                      </div>
                      {noTvgId.map(result => (
                        <div key={result.channel.id} className="lcn-item disabled">
                          <div className="lcn-item-content">
                            <span className="channel-name" title={result.channel.name}>
                              {result.channel.name}
                            </span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Already Has Section */}
              {alreadyHas.length > 0 && (
                <div className="bulk-lcn-section collapsible">
                  <button
                    className="section-header clickable"
                    onClick={() => setAlreadyHasExpanded(!alreadyHasExpanded)}
                  >
                    <span className="material-icons">verified</span>
                    Already Has Gracenote ID ({alreadyHas.length})
                    <span className="material-icons expand-icon">
                      {alreadyHasExpanded ? 'expand_less' : 'expand_more'}
                    </span>
                  </button>
                  {alreadyHasExpanded && (
                    <div className="lcn-list muted">
                      {alreadyHas.map(result => (
                        <div key={result.channel.id} className="lcn-item disabled">
                          <div className="lcn-item-content">
                            <span className="channel-name" title={result.channel.name}>
                              {result.channel.name}
                            </span>
                            <span className="existing-lcn" title={result.channel.tvc_guide_stationid || ''}>
                              Current: {result.channel.tvc_guide_stationid}
                            </span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>

        <div className="bulk-lcn-footer">
          <button className="btn-cancel" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn-primary"
            onClick={handleAssign}
            disabled={phase === 'fetching' || selectedCount === 0}
          >
            Assign {selectedCount} Gracenote ID{selectedCount !== 1 ? 's' : ''}
          </button>
        </div>
      </div>
    </div>
  );
});

export default BulkLCNFetchModal;
