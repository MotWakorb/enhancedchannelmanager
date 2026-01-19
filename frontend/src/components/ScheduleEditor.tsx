import { useState, useEffect } from 'react';
import type { TaskSchedule, TaskScheduleType, TaskScheduleCreate, TaskScheduleUpdate } from '../services/api';
import './ScheduleEditor.css';

interface ScheduleEditorProps {
  schedule?: TaskSchedule;  // For editing existing schedule
  onSave: (data: TaskScheduleCreate | TaskScheduleUpdate) => Promise<void>;
  onCancel: () => void;
  saving?: boolean;
}

// Common interval presets in seconds
const INTERVAL_PRESETS = [
  { label: '5 min', value: 300 },
  { label: '15 min', value: 900 },
  { label: '30 min', value: 1800 },
  { label: '1 hr', value: 3600 },
  { label: '2 hr', value: 7200 },
  { label: '6 hr', value: 21600 },
  { label: '12 hr', value: 43200 },
  { label: '24 hr', value: 86400 },
];

// Day names
const DAYS_OF_WEEK = [
  { value: 0, label: 'Sun', fullLabel: 'Sunday' },
  { value: 1, label: 'Mon', fullLabel: 'Monday' },
  { value: 2, label: 'Tue', fullLabel: 'Tuesday' },
  { value: 3, label: 'Wed', fullLabel: 'Wednesday' },
  { value: 4, label: 'Thu', fullLabel: 'Thursday' },
  { value: 5, label: 'Fri', fullLabel: 'Friday' },
  { value: 6, label: 'Sat', fullLabel: 'Saturday' },
];

// Common timezone options
const TIMEZONE_OPTIONS = [
  { value: 'UTC', label: 'UTC' },
  { value: 'America/New_York', label: 'Eastern (US)' },
  { value: 'America/Chicago', label: 'Central (US)' },
  { value: 'America/Denver', label: 'Mountain (US)' },
  { value: 'America/Los_Angeles', label: 'Pacific (US)' },
  { value: 'Europe/London', label: 'London' },
  { value: 'Europe/Paris', label: 'Paris' },
  { value: 'Asia/Tokyo', label: 'Tokyo' },
  { value: 'Australia/Sydney', label: 'Sydney' },
];

export function ScheduleEditor({ schedule, onSave, onCancel, saving }: ScheduleEditorProps) {
  // Form state
  const [name, setName] = useState(schedule?.name || '');
  const [enabled, setEnabled] = useState(schedule?.enabled ?? true);
  const [scheduleType, setScheduleType] = useState<TaskScheduleType>(schedule?.schedule_type || 'daily');
  const [intervalSeconds, setIntervalSeconds] = useState(schedule?.interval_seconds || 3600);
  const [scheduleTime, setScheduleTime] = useState(schedule?.schedule_time || '03:00');
  const [timezone, setTimezone] = useState(schedule?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone);
  const [daysOfWeek, setDaysOfWeek] = useState<number[]>(schedule?.days_of_week || [1, 2, 3, 4, 5]); // Default: weekdays
  const [dayOfMonth, setDayOfMonth] = useState(schedule?.day_of_month || 1);
  const [useCustomInterval, setUseCustomInterval] = useState(false);

  // Check if browser timezone is in our list
  const browserTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const tzInList = TIMEZONE_OPTIONS.some(tz => tz.value === browserTz);

  // Check if current interval matches a preset
  useEffect(() => {
    const isPreset = INTERVAL_PRESETS.some(p => p.value === intervalSeconds);
    setUseCustomInterval(!isPreset);
  }, []);

  const handleSave = async () => {
    const data: TaskScheduleCreate | TaskScheduleUpdate = {
      name: name || null,
      enabled,
      schedule_type: scheduleType,
    };

    // Add type-specific fields
    if (scheduleType === 'interval') {
      data.interval_seconds = intervalSeconds;
    } else {
      data.schedule_time = scheduleTime;
      data.timezone = timezone;

      if (scheduleType === 'weekly' || scheduleType === 'biweekly') {
        data.days_of_week = daysOfWeek;
      }

      if (scheduleType === 'monthly') {
        data.day_of_month = dayOfMonth;
      }
    }

    await onSave(data);
  };

  const handleIntervalPreset = (value: number) => {
    setIntervalSeconds(value);
    setUseCustomInterval(false);
  };

  const toggleDay = (day: number) => {
    if (daysOfWeek.includes(day)) {
      setDaysOfWeek(daysOfWeek.filter(d => d !== day));
    } else {
      setDaysOfWeek([...daysOfWeek, day].sort());
    }
  };

  const selectWeekdays = () => setDaysOfWeek([1, 2, 3, 4, 5]);
  const selectWeekends = () => setDaysOfWeek([0, 6]);
  const selectAllDays = () => setDaysOfWeek([0, 1, 2, 3, 4, 5, 6]);

  const customIntervalHours = Math.floor(intervalSeconds / 3600);
  const customIntervalMinutes = Math.floor((intervalSeconds % 3600) / 60);

  return (
    <div className="schedule-editor">
      {/* Schedule Name */}
      <div className="form-group">
        <label>Schedule Name (optional)</label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g., 'Daily morning run'"
          className="form-input"
        />
      </div>

      {/* Enable/Disable */}
      <div className="form-group checkbox-group">
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          <span>Enable this schedule</span>
        </label>
      </div>

      {/* Schedule Type Selector */}
      <div className="form-group">
        <label>Schedule Type</label>
        <div className="type-selector">
          {[
            { type: 'interval' as TaskScheduleType, label: 'Interval', icon: 'schedule' },
            { type: 'daily' as TaskScheduleType, label: 'Daily', icon: 'today' },
            { type: 'weekly' as TaskScheduleType, label: 'Weekly', icon: 'date_range' },
            { type: 'biweekly' as TaskScheduleType, label: 'Bi-weekly', icon: 'view_week' },
            { type: 'monthly' as TaskScheduleType, label: 'Monthly', icon: 'calendar_month' },
          ].map(({ type, label, icon }) => (
            <button
              key={type}
              type="button"
              className={`type-button ${scheduleType === type ? 'active' : ''}`}
              onClick={() => setScheduleType(type)}
            >
              <span className="material-icons">{icon}</span>
              <span>{label}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Type-specific Configuration */}
      <div className="config-section">
        {/* INTERVAL */}
        {scheduleType === 'interval' && (
          <>
            <label>Run Interval</label>
            <div className="interval-presets">
              {INTERVAL_PRESETS.map((preset) => (
                <button
                  key={preset.value}
                  type="button"
                  className={`preset-button ${!useCustomInterval && intervalSeconds === preset.value ? 'active' : ''}`}
                  onClick={() => handleIntervalPreset(preset.value)}
                >
                  {preset.label}
                </button>
              ))}
            </div>
            <div className="custom-interval">
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={useCustomInterval}
                  onChange={(e) => setUseCustomInterval(e.target.checked)}
                />
                <span>Custom:</span>
              </label>
              <input
                type="number"
                min={0}
                max={23}
                value={customIntervalHours}
                onChange={(e) => {
                  setUseCustomInterval(true);
                  setIntervalSeconds(parseInt(e.target.value || '0') * 3600 + customIntervalMinutes * 60);
                }}
                disabled={!useCustomInterval}
                className="number-input"
              />
              <span>hours</span>
              <input
                type="number"
                min={0}
                max={59}
                value={customIntervalMinutes}
                onChange={(e) => {
                  setUseCustomInterval(true);
                  setIntervalSeconds(customIntervalHours * 3600 + parseInt(e.target.value || '0') * 60);
                }}
                disabled={!useCustomInterval}
                className="number-input"
              />
              <span>minutes</span>
            </div>
          </>
        )}

        {/* DAILY */}
        {scheduleType === 'daily' && (
          <>
            <label>Time of Day</label>
            <div className="time-picker-row">
              <input
                type="time"
                value={scheduleTime}
                onChange={(e) => setScheduleTime(e.target.value)}
                className="time-input"
              />
              <select
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
                className="timezone-select"
              >
                {TIMEZONE_OPTIONS.map((tz) => (
                  <option key={tz.value} value={tz.value}>{tz.label}</option>
                ))}
                {!tzInList && (
                  <option value={browserTz}>{browserTz} (Local)</option>
                )}
              </select>
            </div>
          </>
        )}

        {/* WEEKLY */}
        {scheduleType === 'weekly' && (
          <>
            <label>Days of Week</label>
            <div className="day-quick-select">
              <button type="button" onClick={selectWeekdays} className="quick-button">Weekdays</button>
              <button type="button" onClick={selectWeekends} className="quick-button">Weekends</button>
              <button type="button" onClick={selectAllDays} className="quick-button">Every Day</button>
            </div>
            <div className="days-grid">
              {DAYS_OF_WEEK.map((day) => (
                <button
                  key={day.value}
                  type="button"
                  className={`day-button ${daysOfWeek.includes(day.value) ? 'active' : ''}`}
                  onClick={() => toggleDay(day.value)}
                  title={day.fullLabel}
                >
                  {day.label}
                </button>
              ))}
            </div>
            <label>Time</label>
            <div className="time-picker-row">
              <input
                type="time"
                value={scheduleTime}
                onChange={(e) => setScheduleTime(e.target.value)}
                className="time-input"
              />
              <select
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
                className="timezone-select"
              >
                {TIMEZONE_OPTIONS.map((tz) => (
                  <option key={tz.value} value={tz.value}>{tz.label}</option>
                ))}
                {!tzInList && (
                  <option value={browserTz}>{browserTz} (Local)</option>
                )}
              </select>
            </div>
          </>
        )}

        {/* BIWEEKLY */}
        {scheduleType === 'biweekly' && (
          <>
            <label>Days of Week (every 2 weeks)</label>
            <div className="day-quick-select">
              <button type="button" onClick={selectWeekdays} className="quick-button">Weekdays</button>
              <button type="button" onClick={selectWeekends} className="quick-button">Weekends</button>
              <button type="button" onClick={selectAllDays} className="quick-button">Every Day</button>
            </div>
            <div className="days-grid">
              {DAYS_OF_WEEK.map((day) => (
                <button
                  key={day.value}
                  type="button"
                  className={`day-button ${daysOfWeek.includes(day.value) ? 'active' : ''}`}
                  onClick={() => toggleDay(day.value)}
                  title={day.fullLabel}
                >
                  {day.label}
                </button>
              ))}
            </div>
            <label>Time</label>
            <div className="time-picker-row">
              <input
                type="time"
                value={scheduleTime}
                onChange={(e) => setScheduleTime(e.target.value)}
                className="time-input"
              />
              <select
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
                className="timezone-select"
              >
                {TIMEZONE_OPTIONS.map((tz) => (
                  <option key={tz.value} value={tz.value}>{tz.label}</option>
                ))}
                {!tzInList && (
                  <option value={browserTz}>{browserTz} (Local)</option>
                )}
              </select>
            </div>
          </>
        )}

        {/* MONTHLY */}
        {scheduleType === 'monthly' && (
          <>
            <label>Day of Month</label>
            <div className="monthly-day-row">
              <select
                value={dayOfMonth}
                onChange={(e) => setDayOfMonth(parseInt(e.target.value))}
                className="day-select"
              >
                {Array.from({ length: 31 }, (_, i) => i + 1).map((day) => (
                  <option key={day} value={day}>{day}</option>
                ))}
                <option value={-1}>Last day</option>
              </select>
              <span className="day-suffix">of each month</span>
            </div>
            <label>Time</label>
            <div className="time-picker-row">
              <input
                type="time"
                value={scheduleTime}
                onChange={(e) => setScheduleTime(e.target.value)}
                className="time-input"
              />
              <select
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
                className="timezone-select"
              >
                {TIMEZONE_OPTIONS.map((tz) => (
                  <option key={tz.value} value={tz.value}>{tz.label}</option>
                ))}
                {!tzInList && (
                  <option value={browserTz}>{browserTz} (Local)</option>
                )}
              </select>
            </div>
          </>
        )}
      </div>

      {/* Actions */}
      <div className="editor-actions">
        <button type="button" onClick={onCancel} className="btn-cancel">
          Cancel
        </button>
        <button
          type="button"
          onClick={handleSave}
          disabled={saving || (scheduleType !== 'interval' && !scheduleTime)}
          className="btn-save"
        >
          {saving ? (
            <>
              <span className="material-icons spinning">sync</span>
              Saving...
            </>
          ) : (
            <>
              <span className="material-icons">save</span>
              {schedule ? 'Update Schedule' : 'Add Schedule'}
            </>
          )}
        </button>
      </div>
    </div>
  );
}
