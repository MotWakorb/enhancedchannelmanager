import { useEffect, useMemo, useState, useCallback, memo } from 'react';
import { normalizeTexts } from '../services/api';
import { naturalCompare } from '../utils/naturalSort';
import './ModalBase.css';
import './NormalizeNamesModal.css';
import { ModalOverlay } from './ModalOverlay';

interface Channel {
  id: number;
  name: string;
}

interface NormalizationEntry {
  id: number;
  current: string;
  normalized: string;
}

interface NormalizeNamesModalProps {
  channels: Channel[];
  onConfirm: (updates: Array<{ id: number; newName: string }>) => void;
  onCancel: () => void;
}

export const NormalizeNamesModal = memo(function NormalizeNamesModal({ channels, onConfirm, onCancel }: NormalizeNamesModalProps) {
  const [normalizations, setNormalizations] = useState<NormalizationEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Track which items are checked and their (possibly edited) names
  const [checked, setChecked] = useState<Record<number, boolean>>({});
  const [editedNames, setEditedNames] = useState<Record<number, string>>({});

  // Call backend normalization engine. Loading/error resets for a new `channels`
  // input are handled by keying the component on channel-id list at the parent,
  // so here the effect only publishes fetched results (async setState).
  useEffect(() => {
    let cancelled = false;

    const names = channels.map(c => c.name);
    normalizeTexts(names)
      .then(response => {
        if (cancelled) return;
        const entries: NormalizationEntry[] = [];
        for (let i = 0; i < channels.length; i++) {
          const original = channels[i].name;
          const normalized = response.results[i]?.normalized ?? original;
          if (normalized !== original) {
            entries.push({ id: channels[i].id, current: original, normalized });
          }
        }
        entries.sort((a, b) => naturalCompare(a.current, b.current));
        setNormalizations(entries);
        setChecked({});
        setEditedNames({});
        setLoading(false);
      })
      .catch(err => {
        if (cancelled) return;
        setError(err.message || 'Failed to normalize names');
        setLoading(false);
      });

    return () => { cancelled = true; };
  }, [channels]);

  // Derive effective checked/edited state
  const getChecked = useCallback((id: number) => checked[id] ?? true, [checked]);
  const getEditedName = useCallback((id: number, fallback: string) => editedNames[id] ?? fallback, [editedNames]);

  const checkedCount = useMemo(() =>
    normalizations.filter(n => getChecked(n.id)).length,
    [normalizations, getChecked]
  );

  const allChecked = checkedCount === normalizations.length && normalizations.length > 0;
  const noneChecked = checkedCount === 0;

  const toggleAll = useCallback(() => {
    const newValue = !allChecked;
    setChecked(prev => {
      const next = { ...prev };
      for (const n of normalizations) next[n.id] = newValue;
      return next;
    });
  }, [allChecked, normalizations]);

  const toggleOne = useCallback((id: number) => {
    setChecked(prev => ({ ...prev, [id]: !(prev[id] ?? true) }));
  }, []);

  const updateName = useCallback((id: number, value: string) => {
    setEditedNames(prev => ({ ...prev, [id]: value }));
  }, []);

  const handleConfirm = () => {
    const updates = normalizations
      .filter(n => getChecked(n.id))
      .map(n => ({ id: n.id, newName: getEditedName(n.id, n.normalized) }))
      .filter(u => {
        const original = normalizations.find(n => n.id === u.id);
        return original && u.newName !== original.current;
      });
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
          {loading ? (
            <div className="modal-empty-state">
              <span className="material-icons spinning">sync</span>
              <p>Running normalization rules...</p>
            </div>
          ) : error ? (
            <div className="modal-empty-state">
              <span className="material-icons" style={{ color: 'var(--error)' }}>error</span>
              <p>{error}</p>
            </div>
          ) : normalizations.length === 0 ? (
            <div className="modal-empty-state">
              <span className="material-icons">check_circle</span>
              <p>All selected channel names are already normalized.</p>
              <p className="normalize-hint">Configure normalization rules in Settings to define how names should be cleaned up.</p>
            </div>
          ) : (
            <>
              <div className="normalize-summary">
                <label className="normalize-select-all">
                  <input
                    type="checkbox"
                    checked={allChecked}
                    onChange={toggleAll}
                  />
                </label>
                <span className="material-icons">text_format</span>
                <span>
                  {checkedCount} of {normalizations.length} change{normalizations.length !== 1 ? 's' : ''} selected
                </span>
              </div>

              <div className="normalize-preview-list">
                {normalizations.map(n => {
                  const isChecked = getChecked(n.id);
                  return (
                    <div key={n.id} className={`normalize-preview-item ${!isChecked ? 'is-excluded' : ''}`}>
                      <div className="normalize-item-header">
                        <label className="normalize-checkbox">
                          <input
                            type="checkbox"
                            checked={isChecked}
                            onChange={() => toggleOne(n.id)}
                          />
                        </label>
                        <div className="normalize-current">
                          <span className="normalize-label">Current:</span>
                          <span className="normalize-name">{n.current}</span>
                        </div>
                      </div>
                      <div className="normalize-arrow">
                        <span className="material-icons">arrow_downward</span>
                      </div>
                      <div className="normalize-new">
                        <span className="normalize-label">New:</span>
                        <input
                          type="text"
                          className="normalize-name-input"
                          value={getEditedName(n.id, n.normalized)}
                          onChange={e => updateName(n.id, e.target.value)}
                          disabled={!isChecked}
                        />
                        {isChecked && (
                          <button
                            className="normalize-revert-btn"
                            onClick={() => updateName(n.id, n.current)}
                            title="Revert to original name for manual editing"
                          >
                            <span className="material-icons">undo</span>
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>

        <div className="modal-footer">
          {normalizations.length > 0 && !loading && (
            <button
              className="modal-btn modal-btn-primary"
              onClick={handleConfirm}
              disabled={noneChecked}
            >
              <span className="material-icons">check</span>
              Apply {checkedCount} Change{checkedCount !== 1 ? 's' : ''}
            </button>
          )}
        </div>
      </div>
    </ModalOverlay>
  );
});
