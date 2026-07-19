/**
 * EventSyncTeamAliasesSection (bead enhancedchannelmanager-ti939.4.2)
 *
 * Operator team-alias dictionary for the Event Sync matcher's team-token
 * layer: groups of KNOWN-equivalent team spellings ("Man Utd" ==
 * "Manchester United" == "MUFC"). Aliases raise recall on
 * abbreviation-heavy providers WITHOUT loosening the fuzzy threshold —
 * and they are corpus-gated by policy: only add a group when observed
 * provider pairs prove the equivalence (a wrong alias is a new
 * false-positive vector).
 *
 * Self-contained: loads on mount from GET /api/event-sync/team-aliases and
 * saves the full dictionary via PUT (the backend validates each term
 * against the matcher's own normalization and journals the change).
 */
import { useCallback, useEffect, useState } from 'react';

import {
  getEventSyncTeamAliases,
  updateEventSyncTeamAliases,
} from '../../services/channelPipelineApi';
import type { EventSyncTeamAliasGroup } from '../../services/channelPipelineApi';
import { useNotifications } from '../../contexts/NotificationContext';
import { logger } from '../../utils/logger';

import './EventSyncTeamAliasesSection.css';

export function EventSyncTeamAliasesSection() {
  const notifications = useNotifications();
  const [groups, setGroups] = useState<EventSyncTeamAliasGroup[]>([]);
  const [termInputs, setTermInputs] = useState<Record<number, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const response = await getEventSyncTeamAliases();
        if (!cancelled) setGroups(response.groups);
      } catch (err) {
        logger.error('Failed to load event sync team aliases', err);
        if (!cancelled) notifications.error('Failed to load team aliases');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [notifications]);

  const mutateGroups = useCallback(
    (updater: (prev: EventSyncTeamAliasGroup[]) => EventSyncTeamAliasGroup[]) => {
      setGroups(updater);
      setDirty(true);
    },
    [],
  );

  const addGroup = useCallback(() => {
    mutateGroups((prev) => [...prev, { terms: [], note: null }]);
  }, [mutateGroups]);

  const removeGroup = useCallback((index: number) => {
    mutateGroups((prev) => prev.filter((_, i) => i !== index));
  }, [mutateGroups]);

  const setNote = useCallback((index: number, note: string) => {
    mutateGroups((prev) =>
      prev.map((g, i) => (i === index ? { ...g, note: note || null } : g)),
    );
  }, [mutateGroups]);

  const addTerm = useCallback((index: number) => {
    const term = (termInputs[index] ?? '').trim();
    if (!term) return;
    mutateGroups((prev) =>
      prev.map((g, i) =>
        i === index && !g.terms.includes(term)
          ? { ...g, terms: [...g.terms, term] }
          : g,
      ),
    );
    setTermInputs((inputs) => ({ ...inputs, [index]: '' }));
  }, [mutateGroups, termInputs]);

  const removeTerm = useCallback((index: number, term: string) => {
    mutateGroups((prev) =>
      prev.map((g, i) =>
        i === index ? { ...g, terms: g.terms.filter((t) => t !== term) } : g,
      ),
    );
  }, [mutateGroups]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      const response = await updateEventSyncTeamAliases(groups);
      setGroups(response.groups);
      setDirty(false);
      notifications.success('Team aliases saved');
    } catch (err) {
      logger.error('Failed to save event sync team aliases', err);
      notifications.error(
        err instanceof Error ? err.message : 'Failed to save team aliases',
      );
    } finally {
      setSaving(false);
    }
  }, [groups, notifications]);

  return (
    <div className="settings-section">
      <div className="settings-section-header">
        <span className="material-icons">diversity_3</span>
        <h3>Event Sync Team Aliases</h3>
      </div>
      <div className="settings-group">
        <span className="form-description">
          Known-equivalent team spellings for the Event Sync matcher — e.g.
          {' '}<em>Man Utd == Manchester United == MUFC</em>.
          Aliases raise recall on abbreviation-heavy providers without
          lowering the match threshold. Add a group only when preview or
          journal evidence proves the equivalence: a wrong alias can attach
          the wrong event.
        </span>

        {loading ? (
          <p className="team-aliases-empty">Loading...</p>
        ) : groups.length === 0 ? (
          <p className="team-aliases-empty">
            No alias groups configured. The matcher runs on exact/abbreviation
            logic alone until equivalences are added here.
          </p>
        ) : (
          groups.map((group, index) => (
            <div className="team-alias-group" key={index}>
              <div className="team-alias-group-header">
                <span className="team-alias-group-title">
                  Group {index + 1}
                </span>
                <button
                  className="btn-secondary btn-small team-alias-remove-group"
                  onClick={() => removeGroup(index)}
                  title="Remove this alias group"
                  aria-label={`Remove group ${index + 1}`}
                >
                  <span className="material-icons" aria-hidden="true">delete</span>
                  Remove group
                </button>
              </div>
              <div className="email-recipients-list">
                {group.terms.length === 0 ? (
                  <span className="no-recipients">
                    No terms yet — add at least 2 equivalent spellings
                  </span>
                ) : (
                  group.terms.map((term) => (
                    <span key={term} className="email-recipient-tag">
                      {term}
                      <button
                        className="remove-btn"
                        onClick={() => removeTerm(index, term)}
                        title="Remove term"
                        aria-label={`Remove term ${term}`}
                      >
                        <span className="material-icons" aria-hidden="true">close</span>
                      </button>
                    </span>
                  ))
                )}
              </div>
              <div className="add-email-row">
                <input
                  type="text"
                  placeholder="Add a spelling, e.g. MUFC"
                  value={termInputs[index] ?? ''}
                  onChange={(e) =>
                    setTermInputs((inputs) => ({ ...inputs, [index]: e.target.value }))
                  }
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') addTerm(index);
                  }}
                />
                <button
                  className="btn-secondary"
                  onClick={() => addTerm(index)}
                  disabled={!(termInputs[index] ?? '').trim()}
                >
                  <span className="material-icons">add</span>
                  Add
                </button>
              </div>
              <input
                type="text"
                className="team-alias-note-input"
                placeholder="Evidence note (optional), e.g. corpus pair 2026-07-18"
                value={group.note ?? ''}
                onChange={(e) => setNote(index, e.target.value)}
              />
            </div>
          ))
        )}

        <div className="team-aliases-actions">
          <button className="btn-secondary" onClick={addGroup} disabled={loading}>
            <span className="material-icons">add</span>
            Add alias group
          </button>
          <button
            className="btn-primary"
            onClick={handleSave}
            disabled={!dirty || saving || loading}
          >
            {saving ? 'Saving…' : 'Save Team Aliases'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default EventSyncTeamAliasesSection;
