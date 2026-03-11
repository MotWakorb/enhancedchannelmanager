import { useState } from 'react';
import type { PublishConfig, PlaylistProfile, CloudTarget, ScheduleType } from '../../types/export';
import * as exportApi from '../../services/exportApi';
import { useNotifications } from '../../contexts/NotificationContext';
import { ModalOverlay } from '../ModalOverlay';
import { CustomSelect } from '../CustomSelect';
import '../ModalBase.css';

interface PublishConfigEditorProps {
  config: PublishConfig | null;
  profiles: PlaylistProfile[];
  targets: CloudTarget[];
  onClose: () => void;
  onSaved: () => void;
}

const SCHEDULE_OPTIONS = [
  { value: 'manual', label: 'Manual' },
  { value: 'cron', label: 'Cron Schedule' },
  { value: 'event', label: 'Event-Triggered' },
];

const EVENT_TRIGGER_OPTIONS = [
  { key: 'm3u_refresh', label: 'M3U Refresh' },
  { key: 'channel_edit', label: 'Channel Edit' },
  { key: 'epg_refresh', label: 'EPG Refresh' },
];

export function PublishConfigEditor({ config, profiles, targets, onClose, onSaved }: PublishConfigEditorProps) {
  const notifications = useNotifications();
  const [saving, setSaving] = useState(false);

  const [name, setName] = useState(config?.name || '');
  const [profileId, setProfileId] = useState<number | ''>(config?.profile_id || '');
  const [targetId, setTargetId] = useState<number | ''>(config?.target_id || '');
  const [scheduleType, setScheduleType] = useState<ScheduleType>(config?.schedule_type || 'manual');
  const [cronExpression, setCronExpression] = useState(config?.cron_expression || '');
  const [eventTriggers, setEventTriggers] = useState<string[]>(config?.event_triggers || []);
  const [enabled, setEnabled] = useState(config?.enabled ?? true);
  const [webhookUrl, setWebhookUrl] = useState(config?.webhook_url || '');

  const profileOptions = profiles.map(p => ({ value: String(p.id), label: p.name }));
  const targetOptions = [
    { value: '', label: 'Local only (no upload)' },
    ...targets.map(t => ({ value: String(t.id), label: `${t.name} (${t.provider_type})` })),
  ];

  const toggleTrigger = (key: string) => {
    setEventTriggers(prev =>
      prev.includes(key) ? prev.filter(t => t !== key) : [...prev, key]
    );
  };

  const handleSave = async () => {
    if (!name.trim()) { notifications.error('Name is required'); return; }
    if (!profileId) { notifications.error('Select a profile'); return; }
    if (scheduleType === 'cron' && !cronExpression.trim()) {
      notifications.error('Cron expression required for cron schedule');
      return;
    }
    if (scheduleType === 'event' && eventTriggers.length === 0) {
      notifications.error('Select at least one event trigger');
      return;
    }

    setSaving(true);
    try {
      const data: Partial<PublishConfig> = {
        name: name.trim(),
        profile_id: Number(profileId),
        target_id: targetId ? Number(targetId) : null,
        schedule_type: scheduleType,
        cron_expression: scheduleType === 'cron' ? cronExpression.trim() : null,
        event_triggers: scheduleType === 'event' ? eventTriggers : [],
        enabled,
        webhook_url: webhookUrl.trim() || null,
      };

      if (config) {
        await exportApi.updatePublishConfig(config.id, data);
        notifications.success(`Config '${name}' updated`);
      } else {
        await exportApi.createPublishConfig(data);
        notifications.success(`Config '${name}' created`);
      }
      onSaved();
      onClose();
    } catch (e) {
      notifications.error(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <ModalOverlay onClose={onClose}>
      <div className="modal-container modal-lg">
        <div className="modal-header">
          <h3>{config ? 'Edit Publish Config' : 'New Publish Config'}</h3>
          <button className="modal-close-btn" onClick={onClose}>
            <span className="material-icons">close</span>
          </button>
        </div>
        <div className="modal-body">
            <div className="modal-form-group">
              <label>Name</label>
              <input type="text" value={name} onChange={e => setName(e.target.value)} placeholder="Nightly Publish" />
            </div>
            <div className="modal-form-group">
              <label>Export Profile</label>
              <CustomSelect
                value={String(profileId)}
                onChange={(val) => setProfileId(val ? Number(val) : '')}
                options={profileOptions}
                placeholder="Select a profile..."
              />
            </div>
            <div className="modal-form-group">
              <label>Cloud Target</label>
              <CustomSelect
                value={String(targetId)}
                onChange={(val) => setTargetId(val ? Number(val) : '')}
                options={targetOptions}
              />
            </div>
            <div className="modal-form-group">
              <label>Schedule</label>
              <CustomSelect
                value={scheduleType}
                onChange={(val) => setScheduleType(val as ScheduleType)}
                options={SCHEDULE_OPTIONS}
              />
            </div>

            {scheduleType === 'cron' && (
              <div className="modal-form-group">
                <label>Cron Expression</label>
                <input
                  type="text"
                  value={cronExpression}
                  onChange={e => setCronExpression(e.target.value)}
                  placeholder="0 3 * * *"
                />
                <span className="form-hint">
                  Format: minute hour day month weekday (e.g., "0 3 * * *" = every day at 3am)
                </span>
              </div>
            )}

            {scheduleType === 'event' && (
              <div className="modal-form-group">
                <label>Event Triggers</label>
                <div className="export-form-checkboxes">
                  {EVENT_TRIGGER_OPTIONS.map(opt => (
                    <label key={opt.key} className="modal-checkbox-label">
                      <input
                        type="checkbox"
                        checked={eventTriggers.includes(opt.key)}
                        onChange={() => toggleTrigger(opt.key)}
                      />
                      {opt.label}
                    </label>
                  ))}
                </div>
              </div>
            )}

            <div className="modal-form-group">
              <label>Webhook URL (optional)</label>
              <input
                type="text"
                value={webhookUrl}
                onChange={e => setWebhookUrl(e.target.value)}
                placeholder="https://hooks.example.com/publish"
              />
            </div>

            <label className="modal-checkbox-label">
              <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
              Enabled
            </label>
        </div>
        <div className="modal-footer">
          <button className="modal-btn modal-btn-secondary" onClick={onClose}>Cancel</button>
          <button className="modal-btn modal-btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? 'Saving...' : config ? 'Save Changes' : 'Create Config'}
          </button>
        </div>
      </div>
    </ModalOverlay>
  );
}
