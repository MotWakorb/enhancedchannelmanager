import { useMemo, memo } from 'react';
import { previewNameNormalizations } from '../utils/epgMatching';
import { naturalCompare } from '../utils/naturalSort';
import './ModalBase.css';
import './NormalizeNamesModal.css';
import { ModalOverlay } from './ModalOverlay';

interface Channel {
  id: number;
  name: string;
}

interface NormalizeNamesModalProps {
  channels: Channel[];
  onConfirm: (updates: Array<{ id: number; newName: string }>) => void;
  onCancel: () => void;
}

export const NormalizeNamesModal = memo(function NormalizeNamesModal({ channels, onConfirm, onCancel }: NormalizeNamesModalProps) {
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
    <ModalOverlay onClose={onCancel}>
      <div className="modal-container modal-md normalize-names-modal">
        <div className="modal-header">
          <h2>Normalize Channel Names</h2>
          <button className="modal-close-btn" onClick={onCancel}>
            <span className="material-icons">close</span>
          </button>
        </div>

        <div className="modal-body">
          {normalizations.length === 0 ? (
            <div className="modal-empty-state">
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

        <div className="modal-footer">
          {normalizations.length > 0 && (
            <button className="modal-btn modal-btn-primary" onClick={handleConfirm}>
              <span className="material-icons">check</span>
              Apply {normalizations.length} Change{normalizations.length !== 1 ? 's' : ''}
            </button>
          )}
        </div>
      </div>
    </ModalOverlay>
  );
});
