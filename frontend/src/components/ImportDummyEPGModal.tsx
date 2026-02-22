import { useState, useEffect, memo } from 'react';
import type { EPGSource, DummyEPGCustomProperties, DummyEPGProfile } from '../types';
import * as api from '../services/api';
import { ModalOverlay } from './ModalOverlay';
import './ModalBase.css';
import './ImportDummyEPGModal.css';

interface ImportDummyEPGModalProps {
  isOpen: boolean;
  onClose: () => void;
  onImport: (data: Partial<DummyEPGProfile>) => void;
}

/** Map Dispatcharr Dummy EPG custom_properties to a partial ECM DummyEPGProfile. */
function mapSourceToProfile(source: EPGSource): Partial<DummyEPGProfile> {
  const cp = (source.custom_properties ?? {}) as DummyEPGCustomProperties;
  return {
    name: source.name,
    name_source: cp.name_source ?? 'channel',
    stream_index: cp.stream_index ?? 1,
    title_pattern: cp.title_pattern ?? null,
    time_pattern: cp.time_pattern ?? null,
    date_pattern: cp.date_pattern ?? null,
    title_template: cp.title_template ?? null,
    description_template: cp.description_template ?? null,
    upcoming_title_template: cp.upcoming_title_template ?? null,
    upcoming_description_template: cp.upcoming_description_template ?? null,
    ended_title_template: cp.ended_title_template ?? null,
    ended_description_template: cp.ended_description_template ?? null,
    fallback_title_template: cp.fallback_title_template ?? null,
    fallback_description_template: cp.fallback_description_template ?? null,
    event_timezone: cp.event_timezone ?? 'US/Eastern',
    output_timezone: cp.output_timezone ?? null,
    program_duration: cp.program_duration ?? 180,
    categories: cp.categories ?? null,
    // Renamed fields
    channel_logo_url_template: cp.channel_logo_url ?? null,
    program_poster_url_template: cp.program_poster_url ?? null,
    // ECM-only defaults
    substitution_pairs: [],
    tvg_id_template: 'ecm-{channel_number}',
    pattern_variants: [],
    // Tags
    include_date_tag: cp.include_date_tag ?? false,
    include_live_tag: cp.include_live_tag ?? false,
    include_new_tag: cp.include_new_tag ?? false,
  };
}

export const ImportDummyEPGModal = memo(function ImportDummyEPGModal({
  isOpen,
  onClose,
  onImport,
}: ImportDummyEPGModalProps) {
  const [sources, setSources] = useState<EPGSource[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    setSelectedId(null);
    setLoading(true);
    api.getEPGSources()
      .then(all => setSources(all.filter(s => s.source_type === 'dummy')))
      .catch(() => setSources([]))
      .finally(() => setLoading(false));
  }, [isOpen]);

  const handleImport = () => {
    const source = sources.find(s => s.id === selectedId);
    if (!source) return;
    onImport(mapSourceToProfile(source));
    onClose();
  };

  if (!isOpen) return null;

  return (
    <ModalOverlay onClose={onClose}>
      <div className="modal-container import-dummy-epg-modal">
        <div className="modal-header">
          <h2>Import from Dispatcharr</h2>
          <button className="modal-close-btn" onClick={onClose}>
            <span className="material-icons">close</span>
          </button>
        </div>

        <div className="modal-body">
          <p className="modal-description">
            Select a Dispatcharr Dummy EPG source to import its settings as a new ECM profile. You can review and edit before saving.
          </p>

          {loading ? (
            <div className="modal-loading">
              <span className="material-icons spinning">sync</span>
              Loading sources...
            </div>
          ) : sources.length === 0 ? (
            <div className="modal-empty-state">
              <span className="material-icons">info</span>
              <p>No Dummy EPG sources found in Dispatcharr. Create one in Dispatcharr first, or use "Add Profile" to start from scratch.</p>
            </div>
          ) : (
            <div className="import-source-list">
              {sources.map(source => (
                <div
                  key={source.id}
                  className={`import-source-item${selectedId === source.id ? ' selected' : ''}`}
                  onClick={() => setSelectedId(source.id)}
                >
                  <div className="import-source-icon">
                    <span className="material-icons">auto_fix_high</span>
                  </div>
                  <div className="import-source-info">
                    <div className="import-source-name">{source.name}</div>
                    <div className="import-source-meta">
                      <span className={`import-source-status ${source.is_active ? 'active' : 'inactive'}`}>
                        <span className="material-icons">
                          {source.is_active ? 'check_circle' : 'block'}
                        </span>
                        {source.is_active ? 'Active' : 'Disabled'}
                      </span>
                      <span>{source.epg_data_count} channels</span>
                    </div>
                  </div>
                  {selectedId === source.id && (
                    <div className="import-source-check">
                      <span className="material-icons">check_circle</span>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="modal-footer modal-footer-spread">
          <button className="modal-btn modal-btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            className="modal-btn modal-btn-primary"
            disabled={selectedId === null}
            onClick={handleImport}
          >
            <span className="material-icons">download</span>
            Import
          </button>
        </div>
      </div>
    </ModalOverlay>
  );
});
