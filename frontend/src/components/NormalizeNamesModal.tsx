import { useMemo } from 'react';
import { previewNameNormalizations } from '../utils/epgMatching';
import { naturalCompare } from '../utils/naturalSort';
import './NormalizeNamesModal.css';

interface Channel {
  id: number;
  name: string;
}

interface NormalizeNamesModalProps {
  channels: Channel[];
  onConfirm: (updates: Array<{ id: number; newName: string }>) => void;
  onCancel: () => void;
}

export function NormalizeNamesModal({ channels, onConfirm, onCancel }: NormalizeNamesModalProps) {
  const normalizations = useMemo(() => {
    const results = previewNameNormalizations(channels);
    // Sort by current name in natural order (e.g., "700 | NFL..." before "701 | NFL...")
    return results.sort((a, b) => naturalCompare(a.current, b.current));
  }, [channels]);

  const handleConfirm = () => {
    const updates = normalizations.map(n => ({
      id: n.id,
      newName: n.normalized
    }));
    onConfirm(updates);
  };

  return (
    <div className="normalize-modal-overlay" onClick={onCancel}>
      <div className="normalize-modal" onClick={e => e.stopPropagation()}>
        <div className="normalize-modal-header">
          <h2>Normalize Channel Names</h2>
          <button className="normalize-modal-close" onClick={onCancel}>
            <span className="material-icons">close</span>
          </button>
        </div>

        <div className="normalize-modal-content">
          {normalizations.length === 0 ? (
            <div className="normalize-no-changes">
              <span className="material-icons">check_circle</span>
              <p>All selected channel names are already normalized.</p>
            </div>
          ) : (
            <>
              <div className="normalize-summary">
                <span className="material-icons">text_format</span>
                <span>
                  {normalizations.length} of {channels.length} channel{channels.length !== 1 ? 's' : ''} will be renamed
                </span>
              </div>

              <div className="normalize-preview-list">
                {normalizations.map(n => (
                  <div key={n.id} className="normalize-preview-item">
                    <div className="normalize-current">
                      <span className="normalize-label">Current:</span>
                      <span className="normalize-name">{n.current}</span>
                    </div>
                    <div className="normalize-arrow">
                      <span className="material-icons">arrow_downward</span>
                    </div>
                    <div className="normalize-new">
                      <span className="normalize-label">New:</span>
                      <span className="normalize-name">{n.normalized}</span>
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>

        <div className="normalize-modal-footer">
          <button className="normalize-btn-cancel" onClick={onCancel}>
            Cancel
          </button>
          {normalizations.length > 0 && (
            <button className="normalize-btn-confirm" onClick={handleConfirm}>
              <span className="material-icons">check</span>
              Apply {normalizations.length} Change{normalizations.length !== 1 ? 's' : ''}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
