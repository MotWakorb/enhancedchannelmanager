import { useState, useEffect } from 'react';
import * as api from '../services/api';
import type { ChannelPopularityScore } from '../types';
import { ModalOverlay } from './ModalOverlay';
import './ModalBase.css';
import './ChannelStatsDetailModal.css';

interface ChannelStatsDetailModalProps {
  /** Resolved integer Channel.id, or null if it couldn't be resolved (e.g.
   * the channel isn't in ECM's channel list — a Custom/unmanaged stream). */
  channelId: number | null;
  /** Stream UUID from the Active Channels row — keys the popularity lookup. */
  uuid: string;
  name: string;
  onClose: () => void;
}

/**
 * Per-channel stats drill-down (enhancedchannelmanager-hq3de.g): detail
 * stats (GET /api/stats/channels/{id}, keyed by the integer Channel.id) +
 * popularity score (GET /api/stats/popularity/channel/{id}, keyed by the
 * stream UUID) for one Active Channels row.
 *
 * MINIMAL VERSION (deferred per the umbrella bead's "implement minimal
 * useful version" guidance): shows the detail payload as a compact key/value
 * list rather than a fully designed layout — the backend's detail shape is
 * a passthrough of Dispatcharr's `/proxy/ts/status/{id}` response with no
 * fixed contract, so a hand-built field-by-field layout would be guessing
 * at fields that may not always be present. Popularity gets a dedicated,
 * fully-designed summary since ChannelPopularityScore IS a stable contract.
 */
export function ChannelStatsDetailModal({ channelId, uuid, name, onClose }: ChannelStatsDetailModalProps) {
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [popularity, setPopularity] = useState<ChannelPopularityScore | null>(null);
  const [popularityError, setPopularityError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setDetail(null);
    setDetailError(null);
    setPopularity(null);
    setPopularityError(null);

    const detailPromise = channelId != null
      ? api.getChannelStatsDetail(channelId)
          .then((d) => { if (active) setDetail(d); })
          .catch((err) => { if (active) setDetailError(err instanceof Error ? err.message : 'Failed to load detail stats'); })
      : Promise.resolve().then(() => {
          if (active) setDetailError('No managed channel record for this stream — detail stats unavailable.');
        });

    const popularityPromise = api.getChannelPopularity(uuid)
      .then((p) => { if (active) setPopularity(p); })
      .catch((err) => { if (active) setPopularityError(err instanceof Error ? err.message : 'Failed to load popularity'); });

    Promise.all([detailPromise, popularityPromise]).finally(() => {
      if (active) setLoading(false);
    });

    return () => { active = false; };
  }, [channelId, uuid]);

  return (
    <ModalOverlay onClose={onClose}>
      <div className="modal-container modal-md channel-stats-detail-modal">
        <div className="modal-header">
          <h3 className="modal-title">Channel Details — {name}</h3>
          <button className="modal-close-btn" onClick={onClose} aria-label="Close" title="Close">
            <span className="material-icons" aria-hidden="true">close</span>
          </button>
        </div>

        <div className="modal-body">
          {loading ? (
            <div className="modal-loading">
              <span className="material-icons spinning">sync</span>
              <p>Loading...</p>
            </div>
          ) : (
            <>
              <div className="channel-stats-detail-section">
                <h4>Popularity</h4>
                {popularityError ? (
                  <p className="channel-stats-detail-hint">{popularityError}</p>
                ) : !popularity ? (
                  <p className="channel-stats-detail-hint">No popularity score calculated yet for this channel.</p>
                ) : (
                  <div className="channel-stats-detail-grid">
                    <span>Score</span><strong>{popularity.score.toFixed(2)}</strong>
                    <span>Rank</span><strong>{popularity.rank ?? '—'}</strong>
                    <span>Watch count (7d)</span><strong>{popularity.watch_count_7d}</strong>
                    <span>Unique viewers (7d)</span><strong>{popularity.unique_viewers_7d}</strong>
                    <span>Trend</span><strong>{popularity.trend} ({popularity.trend_percent.toFixed(1)}%)</strong>
                  </div>
                )}
              </div>

              <div className="channel-stats-detail-section">
                <h4>Live Stream Detail</h4>
                {detailError ? (
                  <p className="channel-stats-detail-hint">{detailError}</p>
                ) : detail ? (
                  <div className="channel-stats-detail-raw">
                    {Object.entries(detail).map(([key, value]) => (
                      <div key={key} className="channel-stats-detail-row">
                        <span className="channel-stats-detail-key">{key}</span>
                        <span className="channel-stats-detail-value">
                          {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="channel-stats-detail-hint">No detail available.</p>
                )}
              </div>
            </>
          )}
        </div>

        <div className="modal-footer">
          <button className="modal-btn modal-btn-secondary" onClick={onClose}>Close</button>
        </div>
      </div>
    </ModalOverlay>
  );
}
