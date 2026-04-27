/**
 * Bulk-edit shared rule settings for multiple auto-creation rules.
 * Only sections marked "Apply" send fields to the server.
 */
import { useState, useEffect, useId, useRef } from 'react';
import type { AutoCreationRule, BulkUpdateRulesPatch } from '../../types/autoCreation';
import { CustomSelect } from '../CustomSelect';
import { getNormalizationRules } from '../../services/api';
import { ModalOverlay } from '../ModalOverlay';
import './BulkRuleSettingsModal.css';

export interface BulkRuleSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  selectedRuleIds: number[];
  rules: AutoCreationRule[];
  onApply: (ruleIds: number[], patch: BulkUpdateRulesPatch) => Promise<void>;
}

export function BulkRuleSettingsModal({
  isOpen,
  onClose,
  selectedRuleIds,
  rules,
  onApply,
}: BulkRuleSettingsModalProps) {
  const id = useId();
  const [saving, setSaving] = useState(false);

  const [applyOptions, setApplyOptions] = useState(false);
  const [enabled, setEnabled] = useState(true);
  const [runOnRefresh, setRunOnRefresh] = useState(false);
  const [stopOnFirstMatch, setStopOnFirstMatch] = useState(true);
  const [skipStruckStreams, setSkipStruckStreams] = useState(false);

  const [applyNorm, setApplyNorm] = useState(false);
  const [normGroupIds, setNormGroupIds] = useState<number[]>([]);
  const [availableNormGroups, setAvailableNormGroups] = useState<{ id: number; name: string; enabled: boolean }[]>([]);

  const [applyChannelSort, setApplyChannelSort] = useState(false);
  const [sortField, setSortField] = useState('');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');
  const [probeOnSort, setProbeOnSort] = useState(false);
  const [sortRegex, setSortRegex] = useState('');

  const [applyStreamSort, setApplyStreamSort] = useState(false);
  const [streamSortField, setStreamSortField] = useState('smart_sort');
  const [streamSortOrder, setStreamSortOrder] = useState<'asc' | 'desc'>('asc');
  const [streamQualityTieBreakOrder, setStreamQualityTieBreakOrder] = useState<'asc' | 'desc'>('desc');
  const [streamProbeOnSort, setStreamProbeOnSort] = useState(false);

  const [applyOrphan, setApplyOrphan] = useState(false);
  const [orphanAction, setOrphanAction] = useState<string>('delete');

  const [applyMergePrune, setApplyMergePrune] = useState(false);
  const [mergeRemoveNonMatching, setMergeRemoveNonMatching] = useState(false);
  const [probeOnSortError, setProbeOnSortError] = useState<string | null>(null);

  /** When the modal is open, parent `rules` can get a new array reference every fetch; don't retrigger seed from `rules`. */
  const rulesRef = useRef(rules);
  rulesRef.current = rules;

  useEffect(() => {
    getNormalizationRules().then(({ groups }) => {
      setAvailableNormGroups(groups.map(g => ({ id: g.id, name: g.name, enabled: g.enabled })));
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!isOpen || selectedRuleIds.length === 0) return;
    const sample = rulesRef.current.find(r => selectedRuleIds.includes(r.id));
    if (!sample) return;

    setEnabled(sample.enabled);
    setRunOnRefresh(sample.run_on_refresh);
    setStopOnFirstMatch(sample.stop_on_first_match);
    setSkipStruckStreams(sample.skip_struck_streams ?? false);
    setNormGroupIds(sample.normalization_group_ids ?? []);
    setSortField(sample.sort_field ?? '');
    setSortOrder((sample.sort_order as 'asc' | 'desc') || 'asc');
    setProbeOnSort(sample.probe_on_sort ?? false);
    setSortRegex(sample.sort_regex || '');
    setStreamSortField(sample.stream_sort_field || 'smart_sort');
    setStreamSortOrder((sample.stream_sort_order as 'asc' | 'desc') || 'asc');
    setStreamQualityTieBreakOrder((sample.quality_tie_break_order as 'asc' | 'desc') || 'desc');
    setStreamProbeOnSort(sample.probe_on_sort ?? false);
    setOrphanAction(sample.orphan_action || 'delete');

    setApplyOptions(false);
    setApplyNorm(false);
    setApplyChannelSort(false);
    setApplyStreamSort(false);
    setApplyOrphan(false);
    setApplyMergePrune(false);
  }, [isOpen, selectedRuleIds]);

  const handleSubmit = async () => {
    const patch: BulkUpdateRulesPatch = {};
    setProbeOnSortError(null);

    if (applyOptions) {
      patch.enabled = enabled;
      patch.run_on_refresh = runOnRefresh;
      patch.stop_on_first_match = stopOnFirstMatch;
      patch.skip_struck_streams = skipStruckStreams;
    }
    if (applyNorm) {
      patch.normalization_group_ids = [...normGroupIds];
    }

    const applyingChannelQualityProbe = applyChannelSort && sortField === 'quality';
    const applyingStreamQualityProbe = applyStreamSort && streamSortField === 'quality';
    const shouldSendProbeOnSort = applyingChannelQualityProbe || applyingStreamQualityProbe;
    if (shouldSendProbeOnSort) {
      const desired = applyingChannelQualityProbe ? probeOnSort : streamProbeOnSort;
      if (applyingChannelQualityProbe && applyingStreamQualityProbe && probeOnSort !== streamProbeOnSort) {
        setProbeOnSortError('Channel sort and stream sort are both applying Quality probing, but have different values. Make them match (or un-apply one section) and try again.');
        return;
      }
      patch.probe_on_sort = desired;
    }

    if (applyChannelSort) {
      patch.sort_field = sortField || null;
      patch.sort_order = sortOrder;
      patch.sort_regex = sortRegex || null;
    }
    if (applyStreamSort) {
      patch.stream_sort_field = streamSortField || null;
      patch.stream_sort_order = streamSortOrder;
      if (streamSortField === 'quality') {
        patch.quality_tie_break_order = streamQualityTieBreakOrder;
      }
    }
    if (applyOrphan) {
      patch.orphan_action = orphanAction;
    }
    if (applyMergePrune) {
      patch.merge_streams_remove_non_matching = mergeRemoveNonMatching;
    }

    if (Object.keys(patch).length === 0) {
      onClose();
      return;
    }

    setSaving(true);
    try {
      await onApply(selectedRuleIds, patch);
      onClose();
    } catch {
      /* Parent shows toast; keep modal open for retry */
    } finally {
      setSaving(false);
    }
  };

  if (!isOpen) return null;

  const n = selectedRuleIds.length;

  return (
    <ModalOverlay onClose={onClose} role="dialog" aria-modal="true" aria-labelledby={`${id}-title`}>
      <div className="modal-container modal-md bulk-rule-settings-modal">
        <div className="modal-header">
          <h2 id={`${id}-title`}>Bulk edit rules ({n} selected)</h2>
          <button type="button" className="modal-close-btn" onClick={onClose} aria-label="Close">
            <span className="material-icons">close</span>
          </button>
        </div>
        <div className="modal-body bulk-rule-settings-body">
          <p className="bulk-rule-settings-intro">
            Check <strong>Apply</strong> for each group you want to change. Unselected groups are left unchanged on every selected rule.
          </p>

          <section className="bulk-section">
            <label className="bulk-apply-row">
              <input
                type="checkbox"
                checked={applyOptions}
                onChange={e => setApplyOptions(e.target.checked)}
                aria-label="Apply rule options"
              />
              <span>Apply rule options</span>
            </label>
            {applyOptions && (
              <div className="checkbox-group horizontal bulk-checkbox-group">
                <label className="checkbox-item">
                  <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
                  <span>Enabled</span>
                </label>
                <label className="checkbox-item">
                  <input type="checkbox" checked={runOnRefresh} onChange={e => setRunOnRefresh(e.target.checked)} />
                  <span>Run on M3U refresh</span>
                </label>
                <label className="checkbox-item">
                  <input type="checkbox" checked={stopOnFirstMatch} onChange={e => setStopOnFirstMatch(e.target.checked)} />
                  <span>Stop on first match</span>
                </label>
                <label className="checkbox-item">
                  <input type="checkbox" checked={skipStruckStreams} onChange={e => setSkipStruckStreams(e.target.checked)} />
                  <span>Skip struck-out streams</span>
                </label>
              </div>
            )}
          </section>

          <section className="bulk-section">
            <label className="bulk-apply-row">
              <input
                type="checkbox"
                checked={applyNorm}
                onChange={e => setApplyNorm(e.target.checked)}
                aria-label="Apply normalization groups"
              />
              <span>Apply normalization groups</span>
            </label>
            {applyNorm && availableNormGroups.length > 0 && (
              <div className="checkbox-group vertical">
                {availableNormGroups.map(group => (
                  <label key={group.id} className="checkbox-item">
                    <input
                      type="checkbox"
                      checked={normGroupIds.includes(group.id)}
                      onChange={e => {
                        if (e.target.checked) setNormGroupIds([...normGroupIds, group.id]);
                        else setNormGroupIds(normGroupIds.filter(x => x !== group.id));
                      }}
                    />
                    <span className={!group.enabled ? 'norm-group-disabled' : ''}>
                      {group.name}{!group.enabled ? ' (disabled)' : ''}
                    </span>
                  </label>
                ))}
              </div>
            )}
            {applyNorm && availableNormGroups.length === 0 && (
              <p className="form-hint">No normalization groups configured.</p>
            )}
          </section>

          <section className="bulk-section">
            <label className="bulk-apply-row">
              <input
                type="checkbox"
                checked={applyChannelSort}
                onChange={e => setApplyChannelSort(e.target.checked)}
              />
              <span>Apply channel sort</span>
            </label>
            {applyChannelSort && (
              <div className="sort-config-row">
                <CustomSelect
                  options={[
                    { value: '', label: 'No sorting (keep manual numbers)' },
                    { value: 'stream_name', label: 'Stream Name' },
                    { value: 'stream_name_natural', label: 'Stream Name (Natural)' },
                    { value: 'group_name', label: 'Group Name' },
                    { value: 'quality', label: 'Quality (Resolution)' },
                    { value: 'stream_name_regex', label: 'Stream Name (Regex)' },
                    { value: 'provider_order', label: 'Provider Order (M3U)' },
                    { value: 'channel_number', label: 'Channel Number' },
                  ]}
                  value={sortField}
                  onChange={setSortField}
                />
                {!!sortField && (
                  <CustomSelect
                    options={[
                      { value: 'asc', label: 'Ascending' },
                      { value: 'desc', label: 'Descending' },
                    ]}
                    value={sortOrder}
                    onChange={v => setSortOrder(v as 'asc' | 'desc')}
                  />
                )}
              </div>
            )}
            {applyChannelSort && sortField === 'stream_name_regex' && (
              <div className="form-field">
                <label htmlFor={`${id}-sort-regex`}>Sort regex</label>
                <input
                  id={`${id}-sort-regex`}
                  className="action-input"
                  value={sortRegex}
                  onChange={e => setSortRegex(e.target.value)}
                />
              </div>
            )}
            {applyChannelSort && sortField === 'quality' && (
              <label className="checkbox-option">
                <input
                  type="checkbox"
                  checked={probeOnSort}
                  onChange={e => {
                    const next = e.target.checked;
                    setProbeOnSort(next);
                    if (applyStreamSort && streamSortField === 'quality') setStreamProbeOnSort(next);
                  }}
                />
                <span>Probe unprobed streams before sorting</span>
              </label>
            )}
          </section>

          <section className="bulk-section">
            <label className="bulk-apply-row">
              <input
                type="checkbox"
                checked={applyStreamSort}
                onChange={e => setApplyStreamSort(e.target.checked)}
              />
              <span>Apply stream sort</span>
            </label>
            {applyStreamSort && (
              <>
                <div className="sort-config-row">
                  <CustomSelect
                    options={[
                      { value: 'smart_sort', label: 'Smart Sort (default)' },
                      { value: '', label: 'No sorting' },
                      { value: 'quality', label: 'Quality (Resolution)' },
                      { value: 'stream_name', label: 'Stream Name' },
                      { value: 'stream_name_natural', label: 'Stream Name (Natural)' },
                      { value: 'provider_order', label: 'Provider Order (M3U)' },
                    ]}
                    value={streamSortField}
                    onChange={setStreamSortField}
                  />
                  {!!streamSortField && streamSortField !== 'smart_sort' && (
                    <CustomSelect
                      options={[
                        { value: 'asc', label: 'Ascending' },
                        { value: 'desc', label: 'Descending' },
                      ]}
                      value={streamSortOrder}
                      onChange={v => setStreamSortOrder(v as 'asc' | 'desc')}
                    />
                  )}
                </div>
                {streamSortField === 'quality' && (
                  <>
                    <div className="form-field" style={{ marginTop: '8px' }}>
                      <label>Equal resolution — M3U tie-break</label>
                      <div className="sort-config-row">
                        <CustomSelect
                          options={[
                            { value: 'desc', label: 'Higher priority first' },
                            { value: 'asc', label: 'Lower priority first' },
                          ]}
                          value={streamQualityTieBreakOrder}
                          onChange={v => setStreamQualityTieBreakOrder(v as 'asc' | 'desc')}
                        />
                      </div>
                    </div>
                    <label className="checkbox-option">
                      <input
                        type="checkbox"
                        checked={streamProbeOnSort}
                        onChange={e => {
                          const next = e.target.checked;
                          setStreamProbeOnSort(next);
                          if (applyChannelSort && sortField === 'quality') setProbeOnSort(next);
                        }}
                      />
                      <span>Probe unprobed streams before sorting</span>
                    </label>
                  </>
                )}
              </>
            )}
          </section>

          {probeOnSortError && (
            <p className="form-hint" style={{ color: 'var(--danger)', marginTop: '8px' }}>
              {probeOnSortError}
            </p>
          )}

          <section className="bulk-section">
            <label className="bulk-apply-row">
              <input type="checkbox" checked={applyOrphan} onChange={e => setApplyOrphan(e.target.checked)} />
              <span>Apply orphan cleanup</span>
            </label>
            {applyOrphan && (
              <CustomSelect
                options={[
                  { value: 'delete', label: 'Delete orphaned channels' },
                  { value: 'move_uncategorized', label: 'Move to Uncategorized' },
                  { value: 'delete_and_cleanup_groups', label: 'Delete channels + empty groups' },
                  { value: 'none', label: 'Do nothing (keep orphans)' },
                ]}
                value={orphanAction}
                onChange={setOrphanAction}
              />
            )}
          </section>

          <section className="bulk-section">
            <label className="bulk-apply-row">
              <input type="checkbox" checked={applyMergePrune} onChange={e => setApplyMergePrune(e.target.checked)} />
              <span>Apply Merge Streams — remove streams that no longer match</span>
            </label>
            {applyMergePrune && (
              <>
                <label className="checkbox-option">
                  <input
                    type="checkbox"
                    checked={mergeRemoveNonMatching}
                    onChange={e => setMergeRemoveNonMatching(e.target.checked)}
                  />
                  <span>Remove streams that no longer match (all <code>merge_streams</code> actions)</span>
                </label>
                <p className="form-hint">
                  Updates every Merge Streams action on each selected rule. Rules with no merge action are unchanged except for other sections you apply above.
                </p>
              </>
            )}
          </section>
        </div>
        <div className="modal-footer">
          <button type="button" className="btn-secondary" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button type="button" className="btn-primary" onClick={() => void handleSubmit()} disabled={saving}>
            {saving ? 'Saving…' : 'Apply to selected'}
          </button>
        </div>
      </div>
    </ModalOverlay>
  );
}
